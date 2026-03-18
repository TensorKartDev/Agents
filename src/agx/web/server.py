"""FastAPI web server that visualizes agent runs with live status updates."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
import re
import subprocess
import io
import contextlib
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status
try:
    from authlib.integrations.starlette_client import OAuth
except Exception:  # pragma: no cover - optional dependency
    OAuth = None
try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import id_token as google_id_token
except Exception:  # pragma: no cover - optional dependency
    GoogleAuthRequest = None
    google_id_token = None
from starlette.websockets import WebSocketState
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..admin_store import AdminStore, PackageRecord
from ..agents.manifest import normalize_manifest, validate_manifest
from .. import __version__ as agx_version
from ..agents.orchestrator import Orchestrator
from ..autogen_runner import AutogenOrchestrator
from ..config import ProjectConfig
from ..oauth_providers import load_oauth_providers, visible_provider_cards
from ..persistence import PostgresRunStore
from ..runtime.integrations import build_runtime_integrations
from ..runtime.interoperability import build_handoff_payload, parse_output_text, resolve_bindings
from ..remote_worker import hostname as worker_hostname
from ..security import AuthManager, SessionUser
from ..workspace import resolve_workspace_paths
from dotenv import load_dotenv
load_dotenv()
def _load_structured(text: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return text


def _run_command(args: List[str], *, timeout: int = 120) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        t1 = time.perf_counter()
        return {
            "code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "duration": t1 - t0,
        }
    except FileNotFoundError:
        t1 = time.perf_counter()
        return {"code": 127, "stdout": "", "stderr": f"{args[0]} not found", "duration": t1 - t0}
    except subprocess.TimeoutExpired:
        t1 = time.perf_counter()
        return {"code": -1, "stdout": "", "stderr": f"timeout after {timeout}s", "duration": t1 - t0}
    except Exception as exc:
        t1 = time.perf_counter()
        return {"code": 1, "stdout": "", "stderr": f"failed to run {' '.join(args)}: {exc}", "duration": t1 - t0}


def _summarize(label: str, result: Dict[str, Any], *, limit: int = 1200) -> str:
    output = (result.get("stdout") or result.get("stderr") or "").strip()
    if not output:
        output = "<no output>"
    if len(output) > limit:
        output = output[:limit] + "\n...[truncated]..."
    status = "ok" if result.get("code") == 0 else f"exit {result.get('code')}"
    duration = result.get("duration")
    timing = f" in {duration:.2f}s" if isinstance(duration, (int, float)) else ""
    return f"{label} ({status}{timing}):\n{output}"


def _run_with_capture(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()
from ..tasks.runner import TaskRunner
from ..tools.builtin import register_builtin_tools
from ..tools.registry import ToolRegistry
from ..tools.base import ToolContext


app = FastAPI(title="AGX Web Runner")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("AGX_AUTH_SECRET", "agx-dev-secret-change-me"))
app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")

WORKSPACE = resolve_workspace_paths()
BASE_DIR = WORKSPACE.base_dir
AGENTS_DIR = WORKSPACE.agents_dir
AGENT_REGISTRY = WORKSPACE.registry_path
STATIC_IMG = Path(__file__).parent / "img"
INDEX_HTML = Path(__file__).parent / "index.html"
ADMIN_HTML = Path(__file__).parent / "admin.html"
LOGIN_HTML = Path(__file__).parent / "login.html"
RUNS_DIR = WORKSPACE.runs_dir
ADMIN_DB_PATH = Path(os.getenv("AGX_ADMIN_DB_PATH", str(BASE_DIR / ".agx" / "admin.db"))).expanduser().resolve()
AUTH_COOKIE_NAME = "agx_session"
AUTH = AuthManager()
ADMIN_STORE = AdminStore(ADMIN_DB_PATH)
OAUTH_PROVIDERS = load_oauth_providers()
OAUTH = OAuth() if OAuth is not None else None

if OAUTH is not None:
    for provider in OAUTH_PROVIDERS.values():
        if provider.name == "google" and not provider.client_secret:
            continue
        kwargs: Dict[str, Any] = {
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "client_kwargs": {"scope": provider.scopes},
        }
        if provider.kind == "oidc":
            kwargs["server_metadata_url"] = provider.server_metadata_url
        else:
            kwargs["authorize_url"] = provider.authorize_url
            kwargs["access_token_url"] = provider.access_token_url
            kwargs["api_base_url"] = provider.api_base_url
        OAUTH.register(provider.name, **kwargs)

# Mount so icons and assets inside agent folders can be served statically.
app.mount("/agents", StaticFiles(directory=AGENTS_DIR, check_dir=False), name="agents")

# Mount default static images (e.g., robot.svg) if present
if STATIC_IMG.exists():
    app.mount("/static/img", StaticFiles(directory=STATIC_IMG), name="static-img")


@app.on_event("startup")
async def init_run_store() -> None:
    global RUN_STORE
    ADMIN_STORE.bootstrap_users(AUTH)
    db_url = os.getenv("AGX_DB_URL", "").strip()
    #db_url = "dbname=agx user=admin password= host=localhost port=5432"

    print(db_url, "DB")
    if not db_url:
        return
    RUN_STORE = PostgresRunStore(db_url)
    for record in RUN_STORE.list_runs():
        try:
            resolved = _resolve_requested_config(record.config_path)
            config = resolved["project_config"]
        except Exception:
            continue
        state = RunState(
            config=config,
            engine=record.engine,
            owner_user_id=record.owner_user_id,
            owner_username=record.owner_username,
            config_path=record.config_path,
            requested_path=record.requested_path,
            total_tasks=record.total_tasks,
            completed_tasks=record.completed_tasks,
            remote_execution=bool(resolved.get("remote_execution")),
            assigned_worker_id=resolved.get("assigned_worker_id"),
            remote_agent_slug=resolved.get("remote_agent_slug"),
        )
        state.completed = record.completed
        state.stop_requested = record.stop_requested
        state.started_at = record.started_at
        events = RUN_STORE.list_events(record.run_id)
        state.history = list(events)
        state.event_seq = len(events)
        RUNS[record.run_id] = state


@dataclass
class AgentInfo:
    id: str
    name: str
    description: str
    icon: str
    config_path: str
    source: str = "local"
    worker_id: Optional[str] = None
    owner_username: Optional[str] = None
    llm_host: Optional[str] = None
    tool_host: Optional[str] = None
    inputs: Optional[Any] = None
    outputs: Optional[Any] = None
    capabilities: Optional[List[str]] = None
    version: Optional[str] = None
    compatibility: Optional[Dict[str, Any]] = None
    pricing: Optional[Dict[str, Any]] = None


def _load_manifest(path: Path, *, validate: bool = True) -> Optional[Dict[str, Any]]:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return None
            if validate:
                errors = validate_manifest(data)
                if errors:
                    msg = "; ".join(errors)
                    print(f"[agx] Invalid agent manifest {path}: {msg}")
                    return None
                return normalize_manifest(data)
            return data
    except Exception:
        return None


def _make_remote_config_token(worker_id: str, agent_slug: str) -> str:
    return f"worker://{worker_id}/{agent_slug}"


def _parse_remote_config_token(value: str) -> Optional[tuple[str, str]]:
    if not isinstance(value, str) or not value.startswith("worker://"):
        return None
    payload = value[len("worker://") :]
    if "/" not in payload:
        return None
    worker_id, agent_slug = payload.split("/", 1)
    worker_id = worker_id.strip()
    agent_slug = agent_slug.strip()
    if not worker_id or not agent_slug:
        return None
    return worker_id, agent_slug


def scan_for_agents() -> List[AgentInfo]:
    """Scan the 'agents' directory for agent packages."""
    agents: List[AgentInfo] = []
    if not AGENTS_DIR.exists():
        return agents

    registry_path = AGENT_REGISTRY
    registry_data = _load_manifest(registry_path, validate=False) if registry_path.exists() else None
    allowed = set(registry_data.get("agents", [])) if registry_data else set()
    if not allowed:
        return agents

    for agent_dir in AGENTS_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        if agent_dir.name not in allowed:
            continue

        manifest_path = agent_dir / "agent.yaml"
        if not manifest_path.exists():
            manifest_path = agent_dir / "agent.yml"
        if not manifest_path.exists():
            continue

        manifest = _load_manifest(manifest_path)
        if not manifest:
            continue

        icon_field = manifest.get("icon")
        icon_candidate = agent_dir / icon_field if icon_field else None
        icon_path = (
            f"/agents/{agent_dir.name}/{icon_field}"
            if AGENTS_DIR.exists() and icon_candidate and icon_candidate.exists()
            else "/static/img/robot.svg"
        )

        config_path_value = manifest.get("config_path") or manifest.get("config")
        config_path = (agent_dir / config_path_value) if config_path_value else manifest_path
        # Prefer an absolute path for downstream consumers (/api/run expects a real file)
        config_path = config_path.resolve()
        if not config_path.exists():
            # Skip invalid entries; keep UI clean for creators
            continue

        agents.append(
            AgentInfo(
                id=agent_dir.name,
                name=manifest.get("name", agent_dir.name.replace("_", " ").title()),
                description=manifest.get("description", ""),
                icon=icon_path,
                config_path=str(config_path),
                source="local",
                llm_host=manifest.get("llm_host"),
                tool_host=manifest.get("tool_host"),
                inputs=manifest.get("inputs"),
                outputs=manifest.get("outputs"),
                capabilities=manifest.get("capabilities"),
                version=manifest.get("version"),
                compatibility=manifest.get("compatibility"),
                pricing=manifest.get("pricing"),
            )
        )

    for remote in ADMIN_STORE.list_worker_agents():
        manifest = _load_structured(remote.manifest_json) or {}
        agents.append(
            AgentInfo(
                id=f"{remote.worker_id}:{remote.agent_slug}",
                name=str(manifest.get("name") or remote.agent_name or remote.agent_slug),
                description=str(manifest.get("description") or ""),
                icon="/static/img/robot.svg",
                config_path=_make_remote_config_token(remote.worker_id, remote.agent_slug),
                source="remote",
                worker_id=remote.worker_id,
                owner_username=remote.owner_username,
                llm_host=manifest.get("llm_host"),
                tool_host=manifest.get("tool_host"),
                inputs=manifest.get("inputs"),
                outputs=manifest.get("outputs"),
                capabilities=manifest.get("capabilities"),
                version=manifest.get("version"),
                compatibility=manifest.get("compatibility"),
                pricing=manifest.get("pricing"),
            )
        )

    return agents


@app.get("/api/agents")
async def list_agents(request: Request) -> JSONResponse:
    """Return a list of discoverable agents."""
    user = AUTH.read_session(request.cookies.get(AUTH_COOKIE_NAME, ""))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    owner_user_id = _scoped_owner_user_id(user)
    agents = [
        agent
        for agent in scan_for_agents()
        if agent.source != "remote" or owner_user_id is None or agent.owner_username == user.username
    ]
    return JSONResponse([agent.__dict__ for agent in agents])


@app.get("/api/meta")
async def meta() -> Dict[str, Any]:
    return {"name": "AGX Framework", "version": agx_version}


@dataclass
class RunState:
    config: ProjectConfig
    engine: str
    config_path: str
    owner_user_id: Optional[str] = None
    owner_username: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    subscribers: List[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None
    completed: bool = False
    total_tasks: int = 0
    completed_tasks: int = 0
    started_at: float = field(default_factory=time.time)
    stop_requested: bool = False
    requested_path: str = ""
    pending_input: "PendingInput | None" = None
    pending_approval: "PendingApproval | None" = None
    pending_remote_task: "PendingRemoteTask | None" = None
    remote_execution: bool = False
    assigned_worker_id: Optional[str] = None
    remote_agent_slug: Optional[str] = None
    event_seq: int = 0


RUNS: Dict[str, RunState] = {}
RUN_STORE: PostgresRunStore | None = None


class RunRequest(BaseModel):
    config_path: str
    engine: str = "autogen"


class WorkerLoginRequest(BaseModel):
    username: str
    password: str


class WorkerRegistrationRequest(BaseModel):
    worker_id: str
    hostname: str
    runtime_url: str
    capabilities: Dict[str, Any] = {}
    agents: List[Dict[str, Any]] = []


class WorkerPollRequest(BaseModel):
    worker_id: str


class WorkerTaskCompleteRequest(BaseModel):
    worker_id: str
    output: str = ""
    duration: float = 0.0
    parsed_output: Optional[Any] = None
    metadata: Dict[str, Any] = {}
    console: List[str] = []
    error: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class GoogleFedCMLoginRequest(BaseModel):
    credential: str
    select_by: Optional[str] = None


class CreateUserRequest(BaseModel):
    tenant_name: Optional[str] = None
    username: str
    email: str
    password: str
    role: str = "developer"
    display_name: Optional[str] = None


class InputSubmit(BaseModel):
    fields: Dict[str, Any]


class ApprovalSubmit(BaseModel):
    approved: bool
    reason: Optional[str] = None


@dataclass
class PendingInput:
    task_id: str
    spec: Any
    event: asyncio.Event = field(default_factory=asyncio.Event)
    response: Optional[Dict[str, Any]] = None


@dataclass
class PendingApproval:
    task_id: str
    reason: str = ""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: Optional[bool] = None
    response_reason: Optional[str] = None


@dataclass
class PendingRemoteTask:
    lease_id: str
    worker_id: str
    task_id: str
    spec: Any
    payload: Dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    delivered_at: float = 0.0
    output: str = ""
    duration: float = 0.0
    parsed_output: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    console: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def _artifacts_dir(run_id: str) -> Path:
    return _run_dir(run_id) / "artifacts"


def _manifest_path(run_id: str) -> Path:
    return _run_dir(run_id) / "manifest.json"


def _ensure_run_dirs(run_id: str) -> None:
    run_dir = _run_dir(run_id)
    artifacts = _artifacts_dir(run_id)
    artifacts.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(run_id)
    if not manifest_path.exists():
        manifest = {
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "inputs": [],
            "approvals": [],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))


def _append_manifest_entry(run_id: str, key: str, entry: Dict[str, Any]) -> None:
    manifest_path = _manifest_path(run_id)
    if not manifest_path.exists():
        _ensure_run_dirs(run_id)
    data = json.loads(manifest_path.read_text())
    items = data.get(key)
    if not isinstance(items, list):
        data[key] = []
    data[key].append(entry)
    manifest_path.write_text(json.dumps(data, indent=2))


def _extract_json_payload(text: str) -> Any:
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if not stripped:
        return None
    # Remove leading FINAL: marker if present
    if stripped.upper().startswith("FINAL:"):
        stripped = stripped[6:].strip()
    # Try direct JSON
    try:
        return json.loads(stripped)
    except Exception:
        pass
    # Try to locate first JSON object/array in text
    for start in ("{", "["):
        idx = stripped.find(start)
        if idx != -1:
            try:
                return json.loads(stripped[idx:])
            except Exception:
                continue
    return None


def _require_user(request: Request) -> SessionUser:
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    user = AUTH.read_session(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def _read_bearer_session(authorization: Optional[str]) -> Optional[SessionUser]:
    header = str(authorization or "").strip()
    if not header.lower().startswith("bearer "):
        return None
    return AUTH.read_session(header.split(" ", 1)[1].strip())


def _require_api_user(authorization: Optional[str] = Header(default=None)) -> SessionUser:
    user = _read_bearer_session(authorization)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def _require_admin(user: SessionUser = Depends(_require_user)) -> SessionUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def _current_user_or_none(request: Request) -> Optional[SessionUser]:
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    return AUTH.read_session(token)


def _scoped_owner_user_id(user: SessionUser) -> Optional[str]:
    return None if user.role == "admin" else user.user_id


def _derive_role_from_claims(email: str) -> str:
    email_value = email.strip().lower()
    admin_emails = {item.strip().lower() for item in os.getenv("AGX_ADMIN_EMAILS", "").split(",") if item.strip()}
    manager_emails = {item.strip().lower() for item in os.getenv("AGX_MANAGER_EMAILS", "").split(",") if item.strip()}
    allowed_domains = {item.strip().lower() for item in os.getenv("AGX_ALLOWED_LOGIN_DOMAINS", "").split(",") if item.strip()}
    if allowed_domains and email_value and "@" in email_value:
        domain = email_value.split("@", 1)[1]
        if domain not in allowed_domains:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email domain is not allowed")
    if email_value in admin_emails:
        return "admin"
    if email_value in manager_emails:
        return "manager"
    return "developer"


def _tenant_for_email(email: str, *, tenant_name: str = ""):
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Email address is required for tenant mapping")
    domain = email.split("@", 1)[1].strip().lower()
    if not domain:
        raise HTTPException(status_code=400, detail="Email domain is required for tenant mapping")
    return ADMIN_STORE.ensure_tenant(
        name=tenant_name.strip() or domain.split(".", 1)[0].replace("-", " ").title(),
        primary_domain=domain,
        contact_email=email,
    )


def _upsert_external_user(*, provider_name: str, subject: str, email: str, username: str, display_name: str) -> SessionUser:
    identity = ADMIN_STORE.get_identity(provider_name, subject)
    if identity is not None:
        record = ADMIN_STORE.get_user_by_id(identity["user_id"])
        if record is None:
            raise HTTPException(status_code=500, detail="Linked user record is missing")
        return SessionUser(
            user_id=record.user_id,
            tenant_id=record.tenant_id or "",
            tenant_name=record.tenant_name or "",
            username=record.username,
            email=record.email,
            role=record.role,
            display_name=record.display_name,
        )

    role = _derive_role_from_claims(email)
    tenant = _tenant_for_email(email)
    safe_username = _sanitize_slug(username or email.split("@", 1)[0] or f"{provider_name}_{subject[:8]}")
    existing = ADMIN_STORE.get_user_by_email(email) or ADMIN_STORE.get_user_by_username(safe_username)
    if existing is None:
        record = ADMIN_STORE.create_sso_user(
            tenant_id=tenant.tenant_id,
            username=safe_username,
            email=email,
            display_name=display_name or safe_username,
            role=role,
        )
    else:
        record = existing
    ADMIN_STORE.link_identity(
        provider=provider_name,
        subject=subject,
        user_id=record.user_id,
        tenant_id=record.tenant_id,
        email=email,
        display_name=display_name or safe_username,
    )
    return SessionUser(
        user_id=record.user_id,
        tenant_id=record.tenant_id or "",
        tenant_name=record.tenant_name or "",
        username=record.username,
        email=record.email,
        role=record.role,
        display_name=record.display_name,
    )


def _sanitize_slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return text or f"agent_{uuid.uuid4().hex[:8]}"


def _update_registry_with_slug(slug: str) -> None:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = AGENT_REGISTRY
    registry_data: Dict[str, Any] = {"agents": []}
    if registry_path.exists():
        loaded = _load_manifest(registry_path, validate=False)
        if isinstance(loaded, dict):
            registry_data = loaded
    items = registry_data.get("agents")
    if not isinstance(items, list):
        items = []
    if slug not in items:
        items.append(slug)
    registry_data["agents"] = sorted({str(item) for item in items})
    registry_path.write_text(yaml.safe_dump(registry_data, sort_keys=False))


def _collect_package_preview(agent_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    config_value = manifest.get("config_path") or manifest.get("config") or "config.yaml"
    config_path = (agent_dir / str(config_value)).resolve()
    config = ProjectConfig.from_file(config_path) if config_path.exists() else None
    return {
        "slug": agent_dir.name,
        "name": manifest.get("name", agent_dir.name),
        "description": manifest.get("description", ""),
        "version": manifest.get("version", ""),
        "inputs": manifest.get("inputs") or [],
        "outputs": manifest.get("outputs") or [],
        "capabilities": manifest.get("capabilities") or [],
        "config_path": str(config_path),
        "task_count": len(config.tasks) if config else 0,
        "agent_count": len(config.agents) if config else 0,
        "tool_count": len(config.tool_specs) if config else 0,
        "tasks": [task.id for task in config.tasks] if config else [],
        "agents": list(config.agents.keys()) if config else [],
        "tools": list(config.tool_specs.keys()) if config else [],
    }


def _safe_extract_zip(upload_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(upload_path) as archive:
        for member in archive.infolist():
            extracted = (dest_dir / member.filename).resolve()
            if not str(extracted).startswith(str(dest_dir.resolve())):
                raise HTTPException(status_code=400, detail="Invalid package archive path")
        archive.extractall(dest_dir)


def _find_uploaded_agent_dir(root: Path) -> Path:
    for candidate in [root] + sorted([item for item in root.rglob("*") if item.is_dir()], key=lambda item: len(item.parts)):
        if (candidate / "agent.yaml").exists() or (candidate / "agent.yml").exists():
            return candidate
    raise HTTPException(status_code=400, detail="Uploaded package is missing agent.yaml")


def _install_agent_dir(agent_dir: Path, manifest: Dict[str, Any], user: SessionUser) -> tuple[PackageRecord, Dict[str, Any]]:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _sanitize_slug(str(manifest.get("id") or agent_dir.name))
    existing = ADMIN_STORE.get_package_by_slug(slug)
    if existing is not None and user.role != "admin" and existing.owner_user_id != user.user_id:
        raise HTTPException(status_code=403, detail="This agent slug is owned by another user")
    target_dir = AGENTS_DIR / slug
    restarted = target_dir.exists()
    if restarted:
        shutil.rmtree(target_dir)
    shutil.copytree(agent_dir, target_dir)
    _update_registry_with_slug(slug)
    preview = _collect_package_preview(target_dir, manifest)
    stored = ADMIN_STORE.upsert_package(
        owner_user_id=user.user_id,
        owner_username=user.username,
        slug=slug,
        name=str(preview["name"]),
        version=str(preview["version"]),
        description=str(preview["description"]),
        manifest=manifest,
        config_path=str(preview["config_path"]),
        package_path=str(target_dir),
        restarted=restarted,
    )
    return stored, preview


def _parse_builder_json_field(raw: str, *, field_name: str) -> Any:
    value = raw.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON for {field_name}: {exc}") from exc


def _normalize_builder_file_entries(files: List[UploadFile], file_paths: List[str]) -> List[tuple[UploadFile, str]]:
    entries: List[tuple[UploadFile, str]] = []
    for idx, upload in enumerate(files):
        raw_path = file_paths[idx] if idx < len(file_paths) else (upload.filename or f"file_{idx}")
        path_text = str(raw_path or upload.filename or f"file_{idx}").strip().replace("\\", "/")
        candidate = Path(path_text)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise HTTPException(status_code=400, detail=f"Invalid uploaded file path: {path_text}")
        entries.append((upload, path_text))
    top_levels = {
        parts[0]
        for _, rel_path in entries
        if len([part for part in Path(rel_path).parts if part]) > 1
        for parts in ([part for part in Path(rel_path).parts if part],)
    }
    normalized: List[tuple[UploadFile, str]] = []
    strip_root = len(top_levels) == 1
    for upload, rel_path in entries:
        parts = [part for part in Path(rel_path).parts if part]
        if strip_root and len(parts) > 1:
            parts = parts[1:]
        normalized_path = "/".join(parts) if parts else (upload.filename or "file")
        base_name = Path(normalized_path).name.lower()
        if base_name in {"agent.yaml", "agent.yml"}:
            raise HTTPException(status_code=400, detail="Do not upload agent.yaml in builder mode; AGX generates it.")
        normalized.append((upload, normalized_path))
    return normalized


async def _build_agent_package_from_upload(
    *,
    slug: str,
    name: str,
    description: str,
    version: str,
    config_path: str,
    icon_path: str,
    capabilities: Any,
    inputs: Any,
    outputs: Any,
    files: List[UploadFile],
    file_paths: List[str],
) -> tuple[Path, Dict[str, Any]]:
    safe_slug = _sanitize_slug(slug or name)
    safe_config_path = str(Path(config_path.strip() or "config.yaml")).replace("\\", "/")
    if Path(safe_config_path).is_absolute() or ".." in Path(safe_config_path).parts:
        raise HTTPException(status_code=400, detail="config_path must stay inside the package root")
    normalized_entries = _normalize_builder_file_entries(files, file_paths)
    with tempfile.TemporaryDirectory(prefix="agx_builder_") as tmpdir:
        tmp_root = Path(tmpdir)
        agent_dir = tmp_root / safe_slug
        agent_dir.mkdir(parents=True, exist_ok=True)
        written_paths: List[str] = []
        for upload, rel_path in normalized_entries:
            target = (agent_dir / rel_path).resolve()
            if not str(target).startswith(str(agent_dir.resolve())):
                raise HTTPException(status_code=400, detail=f"Unsafe file path: {rel_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(await upload.read())
            written_paths.append(rel_path)
        if safe_config_path not in written_paths:
            raise HTTPException(status_code=400, detail=f"Uploaded files must include {safe_config_path}")
        manifest: Dict[str, Any] = {
            "id": safe_slug,
            "name": name.strip() or safe_slug,
            "description": description.strip(),
            "config_path": safe_config_path,
            "version": version.strip() or "1.0.0",
        }
        if icon_path.strip():
            manifest["icon"] = icon_path.strip()
        if capabilities is not None:
            manifest["capabilities"] = capabilities
        if inputs is not None:
            manifest["inputs"] = inputs
        if outputs is not None:
            manifest["outputs"] = outputs
        errors = validate_manifest(manifest)
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))
        manifest = normalize_manifest(manifest)
        (agent_dir / "agent.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
        try:
            ProjectConfig.from_file(agent_dir / safe_config_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid workflow config: {exc}") from exc
        final_dir = Path(tempfile.mkdtemp(prefix="agx_builder_result_")) / safe_slug
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(agent_dir, final_dir)
    return final_dir, manifest


def _package_to_payload(package: PackageRecord) -> Dict[str, Any]:
    manifest = _load_structured(package.manifest_json) or {}
    payload = {
        "package_id": package.package_id,
        "owner_user_id": package.owner_user_id,
        "owner_username": package.owner_username,
        "slug": package.slug,
        "name": package.name,
        "version": package.version,
        "description": package.description,
        "status": package.status,
        "config_path": package.config_path,
        "package_path": package.package_path,
        "uploaded_at": package.uploaded_at,
        "updated_at": package.updated_at,
        "restart_count": package.restart_count,
        "traffic_count": package.traffic_count,
        "last_run_at": package.last_run_at,
        "manifest": manifest,
    }
    payload["preview"] = {
        "inputs": manifest.get("inputs") or [],
        "outputs": manifest.get("outputs") or [],
        "capabilities": manifest.get("capabilities") or [],
    }
    return payload


def _resolve_worker_agent(worker_id: str, agent_slug: str) -> Optional[Dict[str, Any]]:
    items = ADMIN_STORE.list_worker_agents(worker_id=worker_id)
    for item in items:
        if item.agent_slug == agent_slug:
            return {
                "worker_id": item.worker_id,
                "owner_user_id": item.owner_user_id,
                "owner_username": item.owner_username,
                "agent_slug": item.agent_slug,
                "agent_name": item.agent_name,
                "manifest": _load_structured(item.manifest_json) or {},
                "config": _load_structured(item.config_json) or {},
                "config_path": item.config_path,
            }
    return None


def _resolve_requested_config(config_path: str) -> Dict[str, Any]:
    remote = _parse_remote_config_token(config_path)
    if remote is None:
        path = Path(config_path)
        if not path.exists():
            raise HTTPException(status_code=400, detail=f"Config not found: {path}")
        return {
            "project_config": ProjectConfig.from_file(str(path)),
            "resolved_path": str(path.resolve()),
            "requested_path": config_path,
            "remote_execution": False,
            "assigned_worker_id": None,
            "remote_agent_slug": None,
            "owner_user_id": None,
            "owner_username": None,
        }
    worker_id, agent_slug = remote
    worker_agent = _resolve_worker_agent(worker_id, agent_slug)
    if worker_agent is None:
        raise HTTPException(status_code=404, detail=f"Remote agent not found: {config_path}")
    config_data = worker_agent.get("config")
    if not isinstance(config_data, dict):
        raise HTTPException(status_code=400, detail=f"Remote agent config is invalid: {config_path}")
    return {
        "project_config": ProjectConfig.from_mapping(dict(config_data)),
        "resolved_path": config_path,
        "requested_path": config_path,
        "remote_execution": True,
        "assigned_worker_id": worker_id,
        "remote_agent_slug": agent_slug,
        "owner_user_id": worker_agent.get("owner_user_id"),
        "owner_username": worker_agent.get("owner_username"),
    }


def _login_redirect_target(next_path: str = "/") -> RedirectResponse:
    safe_next = next_path if next_path.startswith("/") else "/"
    return RedirectResponse(url=f"/login?next={safe_next}", status_code=status.HTTP_302_FOUND)


def _set_session_cookie(response: Response, user: SessionUser) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        AUTH.issue_session(user),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=AUTH.session_ttl_seconds,
    )


def _ensure_run_access(state: RunState, user: SessionUser, run_id: str) -> None:
    owner_user_id = _scoped_owner_user_id(user)
    if owner_user_id is not None and state.owner_user_id != owner_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")


async def _broadcast_run_event(
    run_id: str,
    event: Dict[str, Any],
    *,
    integrations: Any = None,
) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    envelope = dict(event)
    envelope.setdefault("run_id", run_id)
    envelope.setdefault("project", state.config.name)
    envelope.setdefault("engine", state.engine)
    state.history.append(envelope)
    state.event_seq += 1
    if integrations is not None:
        try:
            integrations.emit(envelope)
        except Exception:
            pass
    if RUN_STORE is not None:
        RUN_STORE.append_event(run_id, state.event_seq, envelope)
    for queue in list(state.subscribers):
        await queue.put(envelope)
    return envelope


def _persist_run_state(run_id: str) -> None:
    if RUN_STORE is None:
        return
    state = RUNS.get(run_id)
    if state is None:
        return
    RUN_STORE.update_run(
        run_id=run_id,
        completed_tasks=state.completed_tasks,
        completed=state.completed,
        stop_requested=state.stop_requested,
    )


@app.get("/api/auth/me")
async def auth_me(user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    return {
        "user_id": user.user_id,
        "tenant_id": user.tenant_id,
        "tenant_name": user.tenant_name,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
    }


@app.get("/api/auth/providers")
async def auth_providers() -> Dict[str, Any]:
    cards = visible_provider_cards(OAUTH_PROVIDERS)
    for item in cards:
        if item["name"] == "google":
            if item.get("fedcm_enabled") and (google_id_token is None or GoogleAuthRequest is None):
                item["fedcm_enabled"] = False
            if item.get("redirect_enabled") and OAuth is None:
                item["redirect_enabled"] = False
            item["enabled"] = bool(item.get("fedcm_enabled") or item.get("redirect_enabled"))
            if not item["enabled"]:
                item["reason"] = "Install google-auth for FedCM or configure OAuth redirect with authlib and a Google client secret."
        elif item.get("flow") == "redirect" and item.get("enabled") and OAuth is None:
            item["enabled"] = False
            item["redirect_enabled"] = False
            item["reason"] = "Install authlib to enable this external identity provider."
    return {"providers": cards}


@app.post("/api/auth/login")
async def auth_login(payload: LoginRequest, response: Response) -> Dict[str, Any]:
    login_value = payload.username.strip()
    record = ADMIN_STORE.get_user_by_email(login_value) or ADMIN_STORE.get_user_by_username(login_value)
    if record is None or not record.active or not AUTH.verify_password(payload.password, record.password_hash, record.salt):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    user = SessionUser(
        user_id=record.user_id,
        tenant_id=record.tenant_id or "",
        tenant_name=record.tenant_name or "",
        username=record.username,
        email=record.email,
        role=record.role,
        display_name=record.display_name,
    )
    _set_session_cookie(response, user)
    return {
        "user_id": user.user_id,
        "tenant_id": user.tenant_id,
        "tenant_name": user.tenant_name,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
    }


@app.post("/api/auth/google/fedcm")
async def auth_google_fedcm(payload: GoogleFedCMLoginRequest, response: Response) -> Dict[str, Any]:
    provider = OAUTH_PROVIDERS.get("google")
    if provider is None:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured on this AGX deployment.")
    if google_id_token is None or GoogleAuthRequest is None:
        raise HTTPException(status_code=503, detail="Google FedCM requires the google-auth package.")
    credential = payload.credential.strip()
    if not credential:
        raise HTTPException(status_code=400, detail="Google credential is required")
    try:
        claims = google_id_token.verify_oauth2_token(credential, GoogleAuthRequest(), provider.client_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Google credential: {exc}") from exc

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Google account did not provide an email address")
    if not claims.get("email_verified"):
        raise HTTPException(status_code=403, detail="Google account email is not verified")
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="Google account did not provide a stable subject")

    user = _upsert_external_user(
        provider_name="google",
        subject=subject,
        email=email,
        username=str(claims.get("email") or email).split("@", 1)[0],
        display_name=str(claims.get("name") or claims.get("given_name") or email.split("@", 1)[0]),
    )
    _set_session_cookie(response, user)
    return {
        "user_id": user.user_id,
        "tenant_id": user.tenant_id,
        "tenant_name": user.tenant_name,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
        "provider": "google",
        "select_by": payload.select_by or "",
    }


@app.post("/api/auth/logout")
async def auth_logout(response: Response) -> Dict[str, Any]:
    response.delete_cookie(AUTH_COOKIE_NAME)
    return {"ok": True}


@app.get("/auth/oauth/{provider_name}/login")
async def oauth_login(provider_name: str, request: Request):
    if OAUTH is None:
        raise HTTPException(status_code=503, detail="External auth support requires authlib")
    provider = OAUTH_PROVIDERS.get(provider_name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name}")
    request.session["oauth_next"] = request.query_params.get("next", "/admin")
    client = OAUTH.create_client(provider_name)
    redirect_uri = request.url_for("oauth_callback", provider_name=provider_name)
    return await client.authorize_redirect(request, redirect_uri)


@app.get("/auth/oauth/{provider_name}/callback", name="oauth_callback")
async def oauth_callback(provider_name: str, request: Request):
    if OAUTH is None:
        raise HTTPException(status_code=503, detail="External auth support requires authlib")
    provider = OAUTH_PROVIDERS.get(provider_name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name}")
    client = OAUTH.create_client(provider_name)
    token = await client.authorize_access_token(request)
    if provider.kind == "oidc":
        userinfo = token.get("userinfo") or await client.userinfo(token=token)
        email = str(userinfo.get("email") or "").strip().lower()
        subject = str(userinfo.get("sub") or email or "")
        username = str(userinfo.get("preferred_username") or email.split("@", 1)[0] or subject)
        display_name = str(userinfo.get("name") or userinfo.get("preferred_username") or username)
    else:
        user_resp = await client.get("user", token=token)
        profile = user_resp.json()
        email = str(profile.get("email") or "").strip().lower()
        if not email:
            email_resp = await client.get("user/emails", token=token)
            emails = email_resp.json()
            if isinstance(emails, list):
                primary = next((item for item in emails if isinstance(item, dict) and item.get("primary")), None)
                if isinstance(primary, dict):
                    email = str(primary.get("email") or "").strip().lower()
                elif emails and isinstance(emails[0], dict):
                    email = str(emails[0].get("email") or "").strip().lower()
        subject = str(profile.get("id") or profile.get("node_id") or email or "")
        username = str(profile.get("login") or email.split("@", 1)[0] or subject)
        display_name = str(profile.get("name") or profile.get("login") or username)
    if not subject:
        raise HTTPException(status_code=400, detail="Provider did not return a stable subject")
    user = _upsert_external_user(
        provider_name=provider_name,
        subject=subject,
        email=email,
        username=username,
        display_name=display_name,
    )
    next_url = str(request.session.pop("oauth_next", "/admin"))
    redirect = RedirectResponse(url=(next_url if next_url.startswith("/") else "/admin"), status_code=status.HTTP_302_FOUND)
    _set_session_cookie(redirect, user)
    return redirect


@app.post("/api/worker/login")
async def worker_login(payload: WorkerLoginRequest) -> Dict[str, Any]:
    login_value = payload.username.strip()
    record = ADMIN_STORE.get_user_by_email(login_value) or ADMIN_STORE.get_user_by_username(login_value)
    if record is None or not record.active or not AUTH.verify_password(payload.password, record.password_hash, record.salt):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    user = SessionUser(
        user_id=record.user_id,
        tenant_id=record.tenant_id or "",
        tenant_name=record.tenant_name or "",
        username=record.username,
        email=record.email,
        role=record.role,
        display_name=record.display_name,
    )
    return {
        "session_token": AUTH.issue_session(user),
        "worker_id": _sanitize_slug(f"{record.username}-{worker_hostname()}"),
        "user": {
            "user_id": user.user_id,
            "username": user.username,
            "role": user.role,
            "display_name": user.display_name,
        },
    }


@app.post("/api/worker/register")
async def worker_register(
    payload: WorkerRegistrationRequest,
    user: SessionUser = Depends(_require_api_user),
) -> Dict[str, Any]:
    worker = ADMIN_STORE.upsert_worker(
        worker_id=_sanitize_slug(payload.worker_id),
        owner_user_id=user.user_id,
        owner_username=user.username,
        hostname=payload.hostname.strip() or worker_hostname(),
        runtime_url=payload.runtime_url.strip() or "",
        status="online",
        capabilities=dict(payload.capabilities or {}),
    )
    ADMIN_STORE.upsert_worker_agents(
        worker_id=worker.worker_id,
        owner_user_id=user.user_id,
        owner_username=user.username,
        agents=list(payload.agents or []),
    )
    return {
        "worker_id": worker.worker_id,
        "hostname": worker.hostname,
        "registered_agents": len(payload.agents or []),
        "status": worker.status,
    }


@app.post("/api/worker/poll")
async def worker_poll(
    payload: WorkerPollRequest,
    user: SessionUser = Depends(_require_api_user),
) -> Dict[str, Any]:
    worker_id = _sanitize_slug(payload.worker_id)
    worker = ADMIN_STORE.get_worker(worker_id)
    if worker is None or worker.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Worker not found: {worker_id}")
    ADMIN_STORE.upsert_worker(
        worker_id=worker.worker_id,
        owner_user_id=worker.owner_user_id,
        owner_username=worker.owner_username,
        hostname=worker.hostname,
        runtime_url=worker.runtime_url,
        status="online",
        capabilities=_load_structured(worker.capabilities_json) or {},
    )
    now = time.time()
    for run_id, state in RUNS.items():
        pending = state.pending_remote_task
        if pending is None or pending.worker_id != worker_id or pending.event.is_set():
            continue
        if pending.delivered_at and now - pending.delivered_at < 120:
            continue
        pending.delivered_at = now
        await _broadcast_run_event(
            run_id,
            {"type": "worker_task_leased", "task_id": pending.task_id, "worker_id": worker_id, "lease_id": pending.lease_id},
        )
        return {"assignment": pending.payload}
    return {"assignment": None}


@app.post("/api/worker/tasks/{lease_id}/complete")
async def worker_complete_task(
    lease_id: str,
    payload: WorkerTaskCompleteRequest,
    user: SessionUser = Depends(_require_api_user),
) -> Dict[str, Any]:
    worker_id = _sanitize_slug(payload.worker_id)
    worker = ADMIN_STORE.get_worker(worker_id)
    if worker is None or worker.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Worker not found: {worker_id}")
    for run_id, state in RUNS.items():
        pending = state.pending_remote_task
        if pending is None or pending.lease_id != lease_id:
            continue
        if pending.worker_id != worker_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lease does not belong to this worker")
        pending.output = payload.output
        pending.duration = float(payload.duration or 0.0)
        pending.parsed_output = payload.parsed_output
        pending.metadata = dict(payload.metadata or {})
        pending.console = [str(item) for item in (payload.console or [])]
        pending.error = payload.error
        pending.event.set()
        await _broadcast_run_event(
            run_id,
            {
                "type": "worker_task_reported",
                "task_id": pending.task_id,
                "worker_id": worker_id,
                "lease_id": lease_id,
                "error": payload.error,
            },
        )
        return {"ok": True, "run_id": run_id, "task_id": pending.task_id}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Lease not found: {lease_id}")


@app.get("/api/admin/users")
async def admin_list_users(_: SessionUser = Depends(_require_admin)) -> Dict[str, Any]:
    users = [
        {
            "user_id": item.user_id,
            "tenant_id": item.tenant_id,
            "tenant_name": item.tenant_name,
            "username": item.username,
            "email": item.email,
            "display_name": item.display_name,
            "role": item.role,
            "created_at": item.created_at,
            "active": item.active,
        }
        for item in ADMIN_STORE.list_users()
    ]
    return {"users": users}


@app.post("/api/admin/users")
async def admin_create_user(payload: CreateUserRequest, _: SessionUser = Depends(_require_admin)) -> Dict[str, Any]:
    username = payload.username.strip()
    email = payload.email.strip().lower()
    if ADMIN_STORE.get_user_by_username(username) is not None:
        raise HTTPException(status_code=409, detail="Username already exists")
    if ADMIN_STORE.get_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="Email already exists")
    tenant = _tenant_for_email(email, tenant_name=payload.tenant_name or "")
    password_hash, salt = AUTH.hash_password(payload.password)
    record = ADMIN_STORE.create_user(
        tenant_id=tenant.tenant_id,
        username=username,
        email=email,
        display_name=(payload.display_name or payload.username).strip(),
        role=payload.role.strip().lower() or "developer",
        password_hash=password_hash,
        salt=salt,
    )
    return {
        "user_id": record.user_id,
        "tenant_id": record.tenant_id,
        "tenant_name": record.tenant_name,
        "username": record.username,
        "email": record.email,
        "display_name": record.display_name,
        "role": record.role,
        "created_at": record.created_at,
    }


@app.get("/api/admin/discovery")
async def admin_discovery(user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    owner_user_id = _scoped_owner_user_id(user)
    payload = ADMIN_STORE.build_discovery_map(owner_user_id)
    payload["runtime"] = {
        "base_dir": str(BASE_DIR),
        "agents_dir": str(AGENTS_DIR),
        "registry_path": str(AGENT_REGISTRY),
        "runs_dir": str(RUNS_DIR),
        "url_hint": os.getenv("AGX_RUNTIME_URL", "").strip(),
    }
    return payload


@app.get("/login")
async def login_page() -> FileResponse:
    if not LOGIN_HTML.exists():
        raise HTTPException(status_code=500, detail="Login UI not found")
    return FileResponse(LOGIN_HTML)


@app.get("/")
async def root(request: Request):
    if _current_user_or_none(request) is None:
        return _login_redirect_target("/")
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=500, detail="UI not found")
    return FileResponse(INDEX_HTML)


@app.get("/admin")
async def admin_page(request: Request):
    if _current_user_or_none(request) is None:
        return _login_redirect_target("/admin")
    if not ADMIN_HTML.exists():
        raise HTTPException(status_code=500, detail="Admin UI not found")
    return FileResponse(ADMIN_HTML)


@app.post("/api/run")
async def start_run(request: RunRequest, owner: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
  print(f"[server] /api/run called with config_path={request.config_path} engine={request.engine}")
  resolved = _resolve_requested_config(request.config_path)
  resolved_path = str(resolved["resolved_path"])
  # If a run for the same config is already active, reuse it instead of starting a duplicate.
  # Treat "not completed but no live task" as stale and allow a new run.
  for existing_id, existing_state in RUNS.items():
    if existing_state.config_path != resolved_path:
      continue
    active_task = existing_state.task is not None and not existing_state.task.done()
    if active_task and not existing_state.completed:
      return {"run_id": existing_id, "project": existing_state.config.name, "already_running": True}
    if not active_task and not existing_state.completed:
      existing_state.completed = True
      if RUN_STORE is not None:
        RUN_STORE.update_run(
            run_id=existing_id,
            completed_tasks=existing_state.completed_tasks,
            completed=True,
            stop_requested=existing_state.stop_requested,
        )
  config = resolved["project_config"]
  remote_owner_user_id = resolved.get("owner_user_id")
  if resolved.get("remote_execution") and owner.role != "admin" and remote_owner_user_id not in {None, owner.user_id}:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This remote agent belongs to another user")
  run_id = str(uuid.uuid4())
  _ensure_run_dirs(run_id)
  state = RunState(
      config=config,
      engine=request.engine,
      config_path=resolved_path,
      owner_user_id=owner.user_id,
      owner_username=owner.username,
      requested_path=request.config_path,
      total_tasks=len(config.tasks),
      completed_tasks=0,
      remote_execution=bool(resolved.get("remote_execution")),
      assigned_worker_id=resolved.get("assigned_worker_id"),
      remote_agent_slug=resolved.get("remote_agent_slug"),
  )
  RUNS[run_id] = state
  if RUN_STORE is not None:
    RUN_STORE.create_run(
        run_id=run_id,
        project=config.name,
        engine=request.engine,
        owner_user_id=owner.user_id,
        owner_username=owner.username,
        config_path=resolved_path,
        requested_path=request.config_path,
        total_tasks=len(config.tasks),
        completed_tasks=0,
        completed=False,
        stop_requested=False,
        started_at=state.started_at,
    )
  if not state.remote_execution:
      ADMIN_STORE.bump_package_traffic(resolved_path)
  state.task = asyncio.create_task(execute_run(run_id, config, request.engine))
  return {"run_id": run_id, "project": config.name}


@app.post("/api/run/{run_id}/stop")
async def stop_run(run_id: str, user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    _ensure_run_access(state, user, run_id)
    state.stop_requested = True
    # Unblock any pending human-gated task so execute_run can exit quickly.
    if state.pending_input is not None:
        state.pending_input.event.set()
    if state.pending_approval is not None:
        state.pending_approval.approved = False
        state.pending_approval.event.set()
    if state.pending_remote_task is not None:
        state.pending_remote_task.error = "Run stopped"
        state.pending_remote_task.event.set()
    if state.task and not state.task.done():
        state.task.cancel()
    state.completed = True
    stop_event = {
        "type": "complete",
        "results": {},
        "duration": 0,
        "stopped": True,
    }
    state.history.append(stop_event)
    state.event_seq += 1
    if RUN_STORE is not None:
        RUN_STORE.append_event(run_id, state.event_seq, stop_event)
    for queue in list(state.subscribers):
        await queue.put(stop_event)
    if RUN_STORE is not None:
        RUN_STORE.update_run(
            run_id=run_id,
            completed_tasks=state.completed_tasks,
            completed=True,
            stop_requested=True,
        )
    return {"run_id": run_id, "stopped": True}


@app.post("/api/run/{run_id}/input/{task_id}")
async def submit_input(run_id: str, task_id: str, payload: InputSubmit, user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    _ensure_run_access(state, user, run_id)
    pending = state.pending_input
    if not pending or pending.task_id != task_id:
        raise HTTPException(status_code=409, detail="No pending input for this task")

    fields = payload.fields or {}
    ui = getattr(pending.spec, "ui", None) or {}
    field_defs = ui.get("fields") if isinstance(ui, dict) else None
    field_defs = field_defs if isinstance(field_defs, list) else []
    errors = []
    for field in field_defs:
        if not isinstance(field, dict):
            continue
        field_id = field.get("id")
        if not field_id:
            continue
        required = bool(field.get("required", False))
        kind = str(field.get("kind") or "text").lower()
        value = fields.get(field_id)
        if required and (value is None or (isinstance(value, str) and not value.strip())):
            errors.append(f"{field_id} is required")
            continue
        if value is None or value == "":
            continue
        if kind in {"path", "file", "folder", "dir", "directory"} and field.get("must_exist", True):
            path = Path(str(value)).expanduser()
            if not path.exists():
                errors.append(f"{field_id} path not found")
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    pending.response = fields
    _append_manifest_entry(
        run_id,
        "inputs",
        {
            "task_id": task_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fields": fields,
            "ui": ui,
        },
    )
    pending.event.set()
    return {"run_id": run_id, "task_id": task_id, "received": True}


@app.post("/api/run/{run_id}/approve/{task_id}")
async def submit_approval(run_id: str, task_id: str, payload: ApprovalSubmit, user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    _ensure_run_access(state, user, run_id)
    pending = state.pending_approval
    if not pending or pending.task_id != task_id:
        raise HTTPException(status_code=409, detail="No pending approval for this task")

    pending.approved = bool(payload.approved)
    pending.response_reason = payload.reason
    _append_manifest_entry(
        run_id,
        "approvals",
        {
            "task_id": task_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "approved": pending.approved,
            "reason": payload.reason or pending.reason,
        },
    )
    pending.event.set()
    return {"run_id": run_id, "task_id": task_id, "approved": pending.approved}


async def execute_run(run_id: str, config: ProjectConfig, engine: str) -> None:
    state = RUNS[run_id]
    state.total_tasks = len(config.tasks)
    state.completed_tasks = 0
    integrations = build_runtime_integrations(
        {
            "middleware": config.defaults.middleware,
            "observability": config.defaults.observability,
        }
    )
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    tool_registry.configure_from_specs(config.tool_specs)
    input_store: Dict[str, Dict[str, Any]] = {}
    result_store: Dict[str, Dict[str, Any]] = {}

    async def broadcast(event: Dict[str, Any]) -> None:
        envelope = dict(event)
        envelope.setdefault("run_id", run_id)
        envelope.setdefault("project", config.name)
        envelope.setdefault("engine", engine)
        state.history.append(envelope)
        state.event_seq += 1
        integrations.emit(envelope)
        if RUN_STORE is not None:
            RUN_STORE.append_event(run_id, state.event_seq, envelope)
        for queue in list(state.subscribers):
            await queue.put(envelope)

    def persist_run() -> None:
        if RUN_STORE is None:
            return
        RUN_STORE.update_run(
            run_id=run_id,
            completed_tasks=state.completed_tasks,
            completed=state.completed,
            stop_requested=state.stop_requested,
        )

    await broadcast(
        {
            "type": "plan",
            "project": config.name,
            "engine": engine,
            "tasks": [
                {"id": task.id, "agent": task.agent, "description": task.description}
                for task in config.tasks
            ],
        }
    )

    task_specs = TaskRunner.order_tasks(list(config.tasks))
    results: Dict[str, Any] = {}
    run_start = time.perf_counter()
    stopped_early = False

    if engine == "legacy":
        orchestrator = Orchestrator(config, integrations=integrations)
        task_lookup = {task.id: task for task in orchestrator.tasks}

        def run_single(task_spec):
            task_obj = task_lookup[task_spec.id]
            task_obj.input = task_spec.input
            task_obj.context = task_spec.context
            return orchestrator.runner.run(task_obj).output

    else:
        orchestrator = AutogenOrchestrator(config, integrations=integrations)

        def run_single(task_spec):
            return orchestrator.run_task(task_spec)

    for spec in task_specs:
      await broadcast({"type": "status", "task_id": spec.id, "status": "pending"})

    try:
      for spec in task_specs:
        if state.stop_requested:
          stopped_early = True
          break

        spec.input = resolve_bindings(spec.input, input_store=input_store, result_store=result_store)
        spec.context = resolve_bindings(spec.context, input_store=input_store, result_store=result_store)

        task_type = getattr(spec, "task_type", None)
        if task_type == "agent_handoff":
          source_task = getattr(spec, "source_task", None) or (spec.depends_on[0] if getattr(spec, "depends_on", None) else "")
          if not source_task:
            await broadcast({"type": "error", "message": f"Agent handoff task {spec.id} missing source_task"})
            stopped_early = True
            break
          handoff = build_handoff_payload(
            source_task=source_task,
            result_store=result_store,
            target_agent=getattr(spec, "agent", None),
          )
          output = json.dumps(handoff, indent=2)
          result_store[spec.id] = {"output": output, "duration": 0, "handoff": handoff}
          results[spec.id] = result_store[spec.id]
          state.completed_tasks += 1
          persist_run()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": output,
              "duration": 0,
            }
          )
          continue

        if task_type == "human_input":
          wait_msg = "WAITING_INPUT"
          state.pending_input = PendingInput(task_id=spec.id, spec=spec)
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "WAITING_HUMAN",
              "output": wait_msg,
              "duration": 0,
            }
          )
          await broadcast(
            {
              "type": "input_request",
              "task_id": spec.id,
              "title": getattr(spec, "description", "Input required"),
              "description": getattr(spec, "description", ""),
              "ui": getattr(spec, "ui", None),
            }
          )
          await state.pending_input.event.wait()
          if state.stop_requested:
            stopped_early = True
            break
          payload = state.pending_input.response or {}
          input_store[spec.id] = dict(payload)
          output = json.dumps(payload)
          duration = 0
          result_store[spec.id] = {
            "output": output,
            "duration": duration,
            "input": payload,
            "parsed_output": payload,
          }
          results[spec.id] = result_store[spec.id]
          state.completed_tasks += 1
          persist_run()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": output,
              "duration": duration,
            }
          )
          state.pending_input = None
          continue

        if task_type == "human_approval":
          reason = getattr(spec, "reason", "") or getattr(spec, "description", "")
          state.pending_approval = PendingApproval(task_id=spec.id, reason=reason)
          wait_msg = f"WAITING_HUMAN: {reason}".strip()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "WAITING_HUMAN",
              "output": wait_msg,
              "duration": 0,
            }
          )
          await broadcast(
            {
              "type": "approval_request",
              "task_id": spec.id,
              "title": "Approval required",
              "reason": reason,
            }
          )
          await state.pending_approval.event.wait()
          if state.stop_requested:
            stopped_early = True
            break
          approved = bool(state.pending_approval.approved)
          output = "Approved" if approved else "Rejected"
          duration = 0
          result_store[spec.id] = {
            "output": output,
            "duration": duration,
            "approved": approved,
            "parsed_output": approved,
          }
          results[spec.id] = result_store[spec.id]
          if approved:
            state.completed_tasks += 1
            persist_run()
            await broadcast(
              {
                "type": "status",
                "task_id": spec.id,
                "status": "completed",
                "output": output,
                "duration": duration,
              }
            )
          else:
            await broadcast(
              {
                "type": "status",
                "task_id": spec.id,
                "status": "failed",
                "output": output,
                "duration": duration,
              }
            )
            stopped_early = True
            state.pending_approval = None
            break
          state.pending_approval = None
          continue

        if task_type == "action_approval":
          source_task = getattr(spec, "source_task", None)
          if not source_task:
            await broadcast({"type": "error", "message": f"Action approval task {spec.id} missing source_task"})
            stopped_early = True
            break
          source_result = result_store.get(source_task, {})
          raw_output = source_result.get("output") if isinstance(source_result, dict) else None
          parsed = _extract_json_payload(raw_output or "")
          actions = []
          if isinstance(parsed, dict):
            actions = parsed.get("proposed_actions") or parsed.get("actions") or []
          if not isinstance(actions, list):
            actions = []
          if not actions and isinstance(raw_output, str):
            text = raw_output
            if "FINAL:" in text:
              text = text.split("FINAL:", 1)[1]
            pattern = r"(Delete|Remove|Compress)\\s+`?([^`\\n]+?)`?\\s*\\((\\d+(?:\\.\\d+)?)\\s*(MiB|MB)\\)"
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            for action_type, path, size, unit in matches:
              size_mb = float(size)
              actions.append(
                {
                  "type": action_type.lower(),
                  "path": path.strip(),
                  "size_mb": size_mb,
                  "reason": "Parsed from recommendations text",
                }
              )

          fields = []
          for idx, action in enumerate(actions):
            if not isinstance(action, dict):
              continue
            path = action.get("path") or action.get("target") or f"action_{idx}"
            action_type = action.get("type") or "action"
            size = action.get("size_mb") or action.get("size") or ""
            reason = action.get("reason") or ""
            label_parts = [str(action_type), str(path)]
            if size:
              label_parts.append(f"({size} MiB)")
            if reason:
              label_parts.append(f"- {reason}")
            fields.append(
              {
                "id": f"action_{idx}",
                "label": " ".join(label_parts),
                "kind": "consent",
                "required": False,
              }
            )

          state.pending_input = PendingInput(task_id=spec.id, spec=spec)
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "WAITING_HUMAN",
              "output": "WAITING_ACTION_APPROVAL",
              "duration": 0,
            }
          )
          await broadcast(
            {
              "type": "input_request",
              "task_id": spec.id,
              "title": "Approve cleanup actions",
              "description": "Select the actions you want to approve.",
              "ui": {"fields": fields},
            }
          )
          await state.pending_input.event.wait()
          if state.stop_requested:
            stopped_early = True
            break
          payload = state.pending_input.response or {}
          approvals = []
          for idx, action in enumerate(actions):
            if payload.get(f"action_{idx}"):
              approvals.append(action)
          output = json.dumps({"approved_actions": approvals}, indent=2)
          duration = 0
          result_store[spec.id] = {
            "output": output,
            "duration": duration,
            "approved_actions": approvals,
            "parsed_output": {"approved_actions": approvals},
          }
          results[spec.id] = result_store[spec.id]
          state.completed_tasks += 1
          persist_run()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": output,
              "duration": duration,
            }
          )
          state.pending_input = None
          continue

        if state.remote_execution and state.assigned_worker_id:
          lease_id = str(uuid.uuid4())
          payload = {
            "lease_id": lease_id,
            "run_id": run_id,
            "engine": engine,
            "worker_id": state.assigned_worker_id,
            "agent_slug": state.remote_agent_slug,
            "task_id": spec.id,
            "task_type": task_type or "",
            "agent": getattr(spec, "agent", ""),
            "description": getattr(spec, "description", ""),
            "input": getattr(spec, "input", None),
            "context": getattr(spec, "context", {}),
          }
          state.pending_remote_task = PendingRemoteTask(
            lease_id=lease_id,
            worker_id=state.assigned_worker_id,
            task_id=spec.id,
            spec=spec,
            payload=payload,
          )
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "queued_for_worker",
              "worker_id": state.assigned_worker_id,
              "duration": 0,
            }
          )
          await state.pending_remote_task.event.wait()
          pending = state.pending_remote_task
          if state.stop_requested:
            stopped_early = True
            break
          if pending is None:
            await broadcast({"type": "error", "message": f"Remote worker state missing for task {spec.id}"})
            stopped_early = True
            break
          for line in pending.console:
            if line:
              await broadcast({"type": "console", "message": line, "task_id": spec.id, "worker_id": pending.worker_id})
          if pending.error:
            await broadcast(
              {
                "type": "status",
                "task_id": spec.id,
                "status": "failed",
                "output": pending.error,
                "duration": pending.duration,
                "worker_id": pending.worker_id,
              }
            )
            stopped_early = True
            state.pending_remote_task = None
            break
          item: Dict[str, Any] = {
            "output": pending.output,
            "duration": pending.duration,
            "parsed_output": pending.parsed_output if pending.parsed_output is not None else parse_output_text(pending.output),
          }
          if pending.metadata:
            item["metadata"] = pending.metadata
            for k, v in pending.metadata.items():
              if isinstance(v, (str, int, float, bool)):
                item[k] = v
          result_store[spec.id] = item
          results[spec.id] = item
          state.completed_tasks += 1
          persist_run()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": pending.output,
              "duration": pending.duration,
              "worker_id": pending.worker_id,
            }
          )
          state.pending_remote_task = None
          continue

        if task_type == "tool_run":
          tool_name = getattr(spec, "tool", None)
          if not tool_name:
            await broadcast({"type": "error", "message": f"Tool run task {spec.id} missing tool name"})
            stopped_early = True
            break
          try:
            tool = tool_registry.get(tool_name)
          except Exception as exc:
            await broadcast({"type": "error", "message": f"Tool {tool_name} not found: {exc}"})
            stopped_early = True
            break
          await broadcast({"type": "status", "task_id": spec.id, "status": "thinking"})
          t0 = time.perf_counter()
          resolved_input = resolve_bindings(
            getattr(spec, "input", None),
            input_store=input_store,
            result_store=result_store,
          )
          if isinstance(resolved_input, (dict, list)):
            input_text = json.dumps(resolved_input)
          elif resolved_input is None:
            input_text = ""
          else:
            input_text = str(resolved_input)

          if tool_name == "disk_usage_triage":
            payload = _load_structured(input_text) or {}
            if not isinstance(payload, dict):
              payload = {}
            path_value = payload.get("path") or "/"
            timeout = int(payload.get("timeout", 40))
            min_mb = int(payload.get("min_mb", 100))
            top_n = int(payload.get("top_n", 5))
            top_files = int(payload.get("top_files", 10))

            output_sections: List[str] = []
            df_cmd = ["df", "-P", "-k", str(path_value)]
            await broadcast({"type": "console", "message": f"Running: {' '.join(df_cmd)}"})
            df_result = await asyncio.to_thread(_run_command, df_cmd, timeout=timeout)
            output_sections.append(_summarize("df -P -k", df_result))
            await broadcast({"type": "console", "message": _summarize("df -P -k", df_result, limit=400)})

            percent_used: Optional[int] = None
            stdout_lines = df_result.get("stdout", "").splitlines()
            if len(stdout_lines) >= 2:
              for line in stdout_lines[1:]:
                columns = [col for col in line.split() if col]
                percent_column = next((col for col in columns if col.endswith("%")), None)
                if percent_column:
                  try:
                    percent_used = int(percent_column.strip("%"))
                  except ValueError:
                    percent_used = None
                if percent_used is not None:
                  break

            status = "unknown"
            if percent_used is not None:
              if percent_used >= 85:
                status = "critical"
              elif percent_used >= 70:
                status = "warning"
              else:
                status = "ok"

            if status in {"warning", "critical"}:
              du_cmd = ["du", "-x", "-k", "-d", "1", str(path_value)]
              await broadcast({"type": "console", "message": f"Running: {' '.join(du_cmd)}"})
              du_result = await asyncio.to_thread(_run_command, du_cmd, timeout=timeout)
              output_sections.append(_summarize("du -x -k -d 1", du_result))
              await broadcast({"type": "console", "message": _summarize("du -x -k -d 1", du_result, limit=400)})
              largest: List[tuple[int, str]] = []
              for line in du_result.get("stdout", "").splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2:
                  continue
                try:
                  size_kb = int(parts[0])
                except ValueError:
                  continue
                path = parts[1]
                if path == str(path_value):
                  continue
                largest.append((size_kb, path))
              largest.sort(key=lambda item: item[0], reverse=True)
              if largest:
                report_lines = []
                for size_kb, path in largest[:top_n]:
                  size_mb = size_kb / 1024
                  report_lines.append(f"- {path}: {size_mb:.1f} MiB")
                output_sections.append("Top directories by size:\n" + "\n".join(report_lines))
            else:
              output_sections.append("Disk usage within acceptable thresholds; no cleanup required.")

            find_cmd = [
              "find",
              str(path_value),
              "-xdev",
              "-type",
              "f",
              "-size",
              f"+{min_mb}M",
              "-printf",
              "%s %p\\n",
            ]
            await broadcast({"type": "console", "message": f"Running: {' '.join(find_cmd)}"})
            find_result = await asyncio.to_thread(_run_command, find_cmd, timeout=timeout)
            output_sections.append(_summarize(f"find files > {min_mb}M", find_result))
            await broadcast({"type": "console", "message": _summarize(f"find files > {min_mb}M", find_result, limit=400)})

            large_files: List[tuple[int, str]] = []
            if find_result.get("code") == 0:
              for line in find_result.get("stdout", "").splitlines():
                parts = line.split(" ", 1)
                if len(parts) != 2:
                  continue
                try:
                  size_bytes = int(parts[0])
                except ValueError:
                  continue
                large_files.append((size_bytes, parts[1]))
            else:
              output_sections.append(
                f"find -printf unavailable; falling back to Python scan for files > {min_mb} MiB."
              )
              try:
                root_dev = Path(path_value).stat().st_dev
                for root, _, files in os.walk(path_value):
                  try:
                    if Path(root).stat().st_dev != root_dev:
                      continue
                  except OSError:
                    continue
                  for fname in files:
                    fpath = Path(root) / fname
                    try:
                      stat = fpath.stat()
                    except OSError:
                      continue
                    if stat.st_dev != root_dev:
                      continue
                    if stat.st_size >= min_mb * 1024 * 1024:
                      large_files.append((stat.st_size, str(fpath)))
              except Exception:
                large_files = []
            large_files.sort(key=lambda item: item[0], reverse=True)
            if large_files:
              lines = []
              for size_bytes, path in large_files[:top_files]:
                size_mb = size_bytes / (1024 * 1024)
                lines.append(f"- {path}: {size_mb:.1f} MiB")
              output_sections.append(f"Largest files (> {min_mb} MiB):\n" + "\n".join(lines))
            else:
              output_sections.append(f"No files larger than {min_mb} MiB found under {path_value}.")

            output = "\n\n".join(output_sections)
            tool_metadata = {}
          else:
            def run_tool():
              result = tool.run(
                input_text=input_text,
                context=ToolContext(
                  agent_name=getattr(spec, "agent", "tool"),
                  task_id=spec.id,
                  iteration=0,
                  metadata={"tool": tool_name, "host": "local", "run_id": run_id},
                ),
              )
              return result.content, (result.metadata or {})
            output, tool_metadata = await asyncio.to_thread(run_tool)
          t1 = time.perf_counter()
          duration = t1 - t0
          item: Dict[str, Any] = {"output": output, "duration": duration}
          item["parsed_output"] = parse_output_text(output)
          if isinstance(tool_metadata, dict):
            item["metadata"] = tool_metadata
            for k, v in tool_metadata.items():
              if isinstance(v, (str, int, float, bool)):
                item[k] = v
          result_store[spec.id] = item
          results[spec.id] = result_store[spec.id]
          if isinstance(tool_metadata, dict) and tool_metadata.get("error") and not getattr(spec, "continue_on_error", False):
            await broadcast(
              {
                "type": "status",
                "task_id": spec.id,
                "status": "failed",
                "output": output,
                "duration": duration,
              }
            )
            stopped_early = True
            break
          state.completed_tasks += 1
          persist_run()
          if output:
            await broadcast({"type": "console", "message": str(output), "task_id": spec.id})
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": output,
              "duration": duration,
            }
          )
          continue

        await broadcast({"type": "status", "task_id": spec.id, "status": "thinking"})
        t0 = time.perf_counter()
        if getattr(spec, "input", None) is not None:
          spec.input = resolve_bindings(spec.input, input_store=input_store, result_store=result_store)
        output, captured = await asyncio.to_thread(_run_with_capture, run_single, spec)
        if captured and engine != "autogen":
          await broadcast({"type": "console", "message": captured})
        t1 = time.perf_counter()
        duration = t1 - t0
        result_store[spec.id] = {
          "output": output,
          "duration": duration,
          "parsed_output": parse_output_text(output),
        }
        results[spec.id] = result_store[spec.id]
        state.completed_tasks += 1
        persist_run()
        await broadcast(
          {
            "type": "status",
            "task_id": spec.id,
            "status": "completed",
            "output": output,
            "duration": duration,
          }
        )
    except asyncio.CancelledError:
      stopped_early = True
    except Exception as exc:  # safety: still surface errors
      stopped_early = True
      await broadcast({"type": "error", "message": f"Run failed: {exc}"})

    # If any task output contains a FINAL: summary, also broadcast it as a console message
    for task_id, obj in results.items():
        raw = obj["output"] if isinstance(obj, dict) and "output" in obj else obj
        try:
            if raw and isinstance(raw, str) and "FINAL:" in raw:
                await broadcast({"type": "console", "message": raw, "task_id": task_id})
        except Exception:
            pass

    run_end = time.perf_counter()
    overall = run_end - run_start

    already_completed = bool(state.history and state.history[-1].get("type") == "complete")
    if not already_completed:
        await broadcast({"type": "complete", "results": results, "duration": overall, "stopped": stopped_early or state.stop_requested})
    state.completed = True
    persist_run()
    integrations.close()


@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str) -> None:
    if run_id not in RUNS:
        await websocket.close(code=1008)
        return
    token = websocket.cookies.get(AUTH_COOKIE_NAME, "")
    user = AUTH.read_session(token)
    if user is None:
        await websocket.close(code=1008)
        return
    state = RUNS[run_id]
    owner_user_id = _scoped_owner_user_id(user)
    if owner_user_id is not None and state.owner_user_id != owner_user_id:
        await websocket.close(code=1008)
        return
    queue: asyncio.Queue = asyncio.Queue()
    state.subscribers.append(queue)
    await websocket.accept()
    closed = False
    try:
        for event in state.history:
            await websocket.send_text(json.dumps(event))
        completed = state.completed
        while not completed:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
            if event.get("type") == "complete":
                completed = True
    except WebSocketDisconnect:
        closed = True
    finally:
        if queue in state.subscribers:
            state.subscribers.remove(queue)
        if not closed and websocket.client_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except RuntimeError:
                pass


@app.get("/api/runs")
async def list_runs(user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    summary = []
    owner_user_id = _scoped_owner_user_id(user)
    for run_id, state in RUNS.items():
        if owner_user_id is not None and state.owner_user_id != owner_user_id:
            continue
        summary.append(_serialize_run_summary(run_id, state))
    return {"runs": summary}


def _serialize_run_summary(run_id: str, state: RunState) -> Dict[str, Any]:
    progress = 0
    if state.total_tasks:
        progress = int((state.completed_tasks / state.total_tasks) * 100)
    event_types = sorted(
        {
            str(event.get("type"))
            for event in state.history
            if isinstance(event, dict) and event.get("type") is not None
        }
    )
    return {
        "run_id": run_id,
        "project": state.config.name,
        "engine": state.engine,
        "owner_user_id": state.owner_user_id,
        "owner_username": state.owner_username,
        "completed": state.completed,
        "progress": progress,
        "tasks_total": state.total_tasks,
        "tasks_completed": state.completed_tasks,
        "started_at": state.started_at,
        "config_path": state.config_path,
        "request_path": state.requested_path or state.config_path,
        "event_count": len(state.history),
        "event_types": event_types,
        "has_artifacts": _run_dir(run_id).exists(),
        "source": "runtime",
    }


def _load_orphan_run_dirs() -> List[Dict[str, Any]]:
    orphan_runs: List[Dict[str, Any]] = []
    if not RUNS_DIR.exists():
        return orphan_runs
    known = set(RUNS.keys())
    for path in sorted(RUNS_DIR.iterdir(), key=lambda item: item.name):
        if not path.is_dir() or path.name in known:
            continue
        manifest = _manifest_path(path.name)
        created_at = path.stat().st_mtime
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text())
                created_text = data.get("created_at")
                if isinstance(created_text, str):
                    created_at = time.mktime(time.strptime(created_text, "%Y-%m-%dT%H:%M:%SZ"))
            except Exception:
                pass
        orphan_runs.append(
            {
                "run_id": path.name,
                "project": "(artifacts only)",
                "engine": "unknown",
                "completed": True,
                "progress": 0,
                "tasks_total": 0,
                "tasks_completed": 0,
                "started_at": created_at,
                "config_path": "",
                "request_path": "",
                "event_count": 0,
                "event_types": [],
                "has_artifacts": True,
                "source": "artifacts",
            }
        )
    return orphan_runs


def _apply_run_filters(
    runs: List[Dict[str, Any]],
    *,
    run_id: str = "",
    project: str = "",
    engine: str = "",
    event_type: str = "",
    completed: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    filtered = []
    run_id = run_id.strip().lower()
    project = project.strip().lower()
    engine = engine.strip().lower()
    event_type = event_type.strip().lower()
    for item in runs:
        if run_id and run_id not in str(item.get("run_id", "")).lower():
            continue
        if project and project not in str(item.get("project", "")).lower():
            continue
        if engine and engine != str(item.get("engine", "")).lower():
            continue
        if completed is not None and bool(item.get("completed")) is not completed:
            continue
        event_types = [str(value).lower() for value in item.get("event_types", [])]
        if event_type and event_type not in event_types:
            continue
        filtered.append(item)
    return filtered


@app.get("/api/admin/runs")
async def admin_list_runs(
    run_id: str = "",
    project: str = "",
    engine: str = "",
    event_type: str = "",
    completed: str = "",
    limit: int = 200,
    user: SessionUser = Depends(_require_user),
) -> Dict[str, Any]:
    runs = [_serialize_run_summary(item_id, state) for item_id, state in RUNS.items()]
    runs.extend(_load_orphan_run_dirs())
    owner_user_id = _scoped_owner_user_id(user)
    if owner_user_id is not None:
        runs = [item for item in runs if item.get("owner_user_id") == owner_user_id]
    completed_filter: Optional[bool] = None
    completed_text = completed.strip().lower()
    if completed_text in {"true", "1", "yes", "completed"}:
        completed_filter = True
    elif completed_text in {"false", "0", "no", "active"}:
        completed_filter = False
    runs = _apply_run_filters(
        runs,
        run_id=run_id,
        project=project,
        engine=engine,
        event_type=event_type,
        completed=completed_filter,
    )
    runs.sort(key=lambda item: float(item.get("started_at") or 0), reverse=True)
    trimmed = runs[: max(1, min(limit, 1000))]
    event_types = sorted(
        {
            value
            for item in runs
            for value in item.get("event_types", [])
            if isinstance(value, str) and value
        }
    )
    return {"runs": trimmed, "total": len(runs), "event_types": event_types}


@app.get("/api/admin/runs/{run_id}/events")
async def admin_list_run_events(
    run_id: str,
    event_type: str = "",
    q: str = "",
    limit: int = 500,
    offset: int = 0,
    user: SessionUser = Depends(_require_user),
) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if state is None:
        if _run_dir(run_id).exists():
            return {"run_id": run_id, "events": [], "total": 0, "event_types": []}
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    owner_user_id = _scoped_owner_user_id(user)
    if owner_user_id is not None and state.owner_user_id != owner_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
    items = list(state.history)
    event_filter = event_type.strip().lower()
    query = q.strip().lower()
    filtered: List[Dict[str, Any]] = []
    for event in items:
        if not isinstance(event, dict):
            continue
        if event_filter and str(event.get("type", "")).lower() != event_filter:
            continue
        if query:
            haystack = json.dumps(event, default=str).lower()
            if query not in haystack:
                continue
        filtered.append(event)
    all_types = sorted(
        {
            str(event.get("type"))
            for event in items
            if isinstance(event, dict) and event.get("type") is not None
        }
    )
    safe_offset = max(0, offset)
    safe_limit = max(1, min(limit, 2000))
    return {
        "run_id": run_id,
        "events": filtered[safe_offset:safe_offset + safe_limit],
        "total": len(filtered),
        "event_types": all_types,
    }


@app.delete("/api/admin/runs/{run_id}")
async def admin_delete_run(run_id: str, user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    owner_user_id = _scoped_owner_user_id(user)
    if state is not None and owner_user_id is not None and state.owner_user_id != owner_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
    if state is not None and state.task is not None and not state.task.done() and not state.completed:
        raise HTTPException(status_code=409, detail="Cannot delete an active run")

    run_dir = _run_dir(run_id)
    had_artifacts = run_dir.exists()
    deleted_store = False
    if RUN_STORE is not None:
        try:
            deleted_store = RUN_STORE.delete_run(run_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete persisted run: {exc}") from exc

    if state is not None:
        RUNS.pop(run_id, None)

    if had_artifacts:
        try:
            shutil.rmtree(run_dir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete run artifacts: {exc}") from exc

    if state is None and not deleted_store and not had_artifacts:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    return {
        "run_id": run_id,
        "deleted": True,
        "deleted_store": deleted_store,
        "deleted_artifacts": had_artifacts,
    }


@app.get("/api/admin/packages")
async def admin_list_packages(user: SessionUser = Depends(_require_user)) -> Dict[str, Any]:
    owner_user_id = _scoped_owner_user_id(user)
    packages = [_package_to_payload(item) for item in ADMIN_STORE.list_packages(owner_user_id)]
    return {"packages": packages}


@app.post("/api/admin/packages/upload")
async def admin_upload_package(
    package: UploadFile = File(...),
    user: SessionUser = Depends(_require_user),
) -> Dict[str, Any]:
    if not package.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip agent packages are supported")
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agx_upload_") as tmpdir:
        tmp_root = Path(tmpdir)
        upload_path = tmp_root / package.filename
        upload_path.write_bytes(await package.read())
        extract_dir = tmp_root / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(upload_path, extract_dir)
        agent_dir = _find_uploaded_agent_dir(extract_dir)
        manifest_path = agent_dir / "agent.yaml"
        if not manifest_path.exists():
            manifest_path = agent_dir / "agent.yml"
        manifest = _load_manifest(manifest_path)
        if not manifest:
            raise HTTPException(status_code=400, detail="Invalid agent manifest")
        stored, preview = _install_agent_dir(agent_dir, manifest, user)
    payload = _package_to_payload(stored)
    payload["preview"] = preview
    return payload


@app.post("/api/admin/packages/build")
async def admin_build_package(
    slug: str = Form(""),
    name: str = Form(""),
    description: str = Form(""),
    version: str = Form("1.0.0"),
    config_path: str = Form("config.yaml"),
    icon_path: str = Form(""),
    capabilities_json: str = Form(""),
    inputs_json: str = Form(""),
    outputs_json: str = Form(""),
    action: str = Form("download"),
    files: List[UploadFile] = File(...),
    file_paths: List[str] = Form([]),
    user: SessionUser = Depends(_require_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one file or folder entry")
    capabilities = _parse_builder_json_field(capabilities_json, field_name="capabilities_json")
    inputs = _parse_builder_json_field(inputs_json, field_name="inputs_json")
    outputs = _parse_builder_json_field(outputs_json, field_name="outputs_json")
    built_dir, manifest = await _build_agent_package_from_upload(
        slug=slug,
        name=name,
        description=description,
        version=version,
        config_path=config_path,
        icon_path=icon_path,
        capabilities=capabilities,
        inputs=inputs,
        outputs=outputs,
        files=files,
        file_paths=file_paths,
    )
    try:
        if action.strip().lower() == "install":
            stored, preview = _install_agent_dir(built_dir, manifest, user)
            payload = _package_to_payload(stored)
            payload["preview"] = preview
            payload["built"] = True
            return JSONResponse(payload)

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in built_dir.rglob("*"):
                if path.is_file():
                    bundle.write(path, arcname=str(path.relative_to(built_dir.parent)))
        headers = {
            "Content-Disposition": f'attachment; filename="{built_dir.name}.zip"',
            "X-AGX-Package-Slug": built_dir.name,
        }
        return Response(content=archive.getvalue(), media_type="application/zip", headers=headers)
    finally:
        shutil.rmtree(built_dir.parent, ignore_errors=True)
