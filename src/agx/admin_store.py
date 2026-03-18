"""SQLite-backed admin storage for users and uploaded agent packages."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class UserRecord:
    user_id: str
    tenant_id: Optional[str]
    tenant_name: Optional[str]
    username: str
    email: str
    display_name: str
    role: str
    password_hash: str
    salt: str
    active: bool
    created_at: str


@dataclass
class TenantRecord:
    tenant_id: str
    name: str
    slug: str
    primary_domain: str
    contact_email: str
    created_at: str


@dataclass
class PackageRecord:
    package_id: str
    owner_user_id: str
    owner_username: str
    slug: str
    name: str
    version: str
    description: str
    manifest_json: str
    config_path: str
    package_path: str
    status: str
    uploaded_at: str
    updated_at: str
    restart_count: int
    traffic_count: int
    last_run_at: Optional[str]


@dataclass
class WorkerRecord:
    worker_id: str
    owner_user_id: str
    owner_username: str
    hostname: str
    runtime_url: str
    status: str
    capabilities_json: str
    last_seen_at: str
    created_at: str
    updated_at: str


@dataclass
class WorkerAgentRecord:
    worker_id: str
    owner_user_id: str
    owner_username: str
    agent_slug: str
    agent_name: str
    manifest_json: str
    config_json: str
    config_path: str
    last_seen_at: str


class AdminStore:
    """Persistence layer for web admin users and packages."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    primary_domain TEXT NOT NULL UNIQUE,
                    contact_email TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_users (
                    user_id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_packages (
                    package_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    owner_username TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    description TEXT,
                    manifest_json TEXT NOT NULL,
                    config_path TEXT NOT NULL,
                    package_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    restart_count INTEGER NOT NULL DEFAULT 0,
                    traffic_count INTEGER NOT NULL DEFAULT 0,
                    last_run_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS external_identities (
                    provider TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    tenant_id TEXT,
                    email TEXT,
                    display_name TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (provider, subject)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_nodes (
                    worker_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    owner_username TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    runtime_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_agents (
                    worker_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    owner_username TEXT NOT NULL,
                    agent_slug TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    config_path TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (worker_id, agent_slug)
                )
                """
            )
            self._ensure_column(conn, "admin_users", "tenant_id", "TEXT")
            self._ensure_column(conn, "admin_users", "email", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "external_identities", "tenant_id", "TEXT")
            conn.commit()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in columns}
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def bootstrap_users(self, auth_manager) -> None:
        if self.count_users() > 0:
            return
        raw = os.getenv("AGX_BOOTSTRAP_USERS", "").strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, list):
            return
        for item in payload:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            email = str(item.get("email") or username).strip().lower()
            password = str(item.get("password") or "")
            if not username or not password or not email:
                continue
            role = str(item.get("role") or "developer").strip().lower() or "developer"
            display_name = str(item.get("display_name") or username)
            tenant_name = str(item.get("tenant_name") or "").strip()
            tenant_domain = str(item.get("tenant_domain") or email.split("@", 1)[1] if "@" in email else "").strip().lower()
            password_hash, salt = auth_manager.hash_password(password)
            tenant = self.ensure_tenant(
                name=tenant_name or tenant_domain or "Default Tenant",
                primary_domain=tenant_domain or "local.invalid",
                contact_email=email,
            )
            self.create_user(
                tenant_id=tenant.tenant_id,
                username=username,
                email=email,
                display_name=display_name,
                role=role,
                password_hash=password_hash,
                salt=salt,
            )

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM admin_users").fetchone()
        return int(row["count"]) if row else 0

    def create_user(
        self,
        *,
        tenant_id: Optional[str],
        username: str,
        email: str,
        display_name: str,
        role: str,
        password_hash: str,
        salt: str,
    ) -> UserRecord:
        now = _utc_now()
        user = UserRecord(
            user_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            tenant_name=self.get_tenant_name(tenant_id),
            username=username,
            email=email,
            display_name=display_name,
            role=role,
            password_hash=password_hash,
            salt=salt,
            active=True,
            created_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_users (user_id, tenant_id, username, email, display_name, role, password_hash, salt, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.user_id,
                    user.tenant_id,
                    user.username,
                    user.email,
                    user.display_name,
                    user.role,
                    user.password_hash,
                    user.salt,
                    1,
                    user.created_at,
                ),
            )
            conn.commit()
        return user

    def create_sso_user(
        self,
        *,
        tenant_id: Optional[str],
        username: str,
        email: str,
        display_name: str,
        role: str,
    ) -> UserRecord:
        return self.create_user(
            tenant_id=tenant_id,
            username=username,
            email=email,
            display_name=display_name,
            role=role,
            password_hash="",
            salt="",
        )

    def ensure_tenant(self, *, name: str, primary_domain: str, contact_email: str) -> TenantRecord:
        slug = _slugify(name or primary_domain)
        existing = self.get_tenant_by_domain(primary_domain)
        if existing is not None:
            return existing
        tenant = TenantRecord(
            tenant_id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            primary_domain=primary_domain,
            contact_email=contact_email,
            created_at=_utc_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tenants (tenant_id, name, slug, primary_domain, contact_email, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant.tenant_id,
                    tenant.name,
                    tenant.slug,
                    tenant.primary_domain,
                    tenant.contact_email,
                    tenant.created_at,
                ),
            )
            conn.commit()
        return tenant

    def get_tenant_by_domain(self, primary_domain: str) -> Optional[TenantRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenants WHERE primary_domain = ?",
                (primary_domain.strip().lower(),),
            ).fetchone()
        return _row_to_tenant(row)

    def get_tenant_name(self, tenant_id: Optional[str]) -> Optional[str]:
        if not tenant_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT name FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        return str(row["name"]) if row else None

    def get_user_by_username(self, username: str) -> Optional[UserRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM admin_users WHERE username = ?",
                (username,),
            ).fetchone()
        return self._with_tenant_name(_row_to_user(row))

    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM admin_users WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
        return self._with_tenant_name(_row_to_user(row))

    def get_user_by_id(self, user_id: str) -> Optional[UserRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM admin_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._with_tenant_name(_row_to_user(row))

    def list_users(self) -> List[UserRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM admin_users ORDER BY created_at ASC"
            ).fetchall()
        return [self._with_tenant_name(_row_to_user(row)) for row in rows if row is not None]

    def _with_tenant_name(self, user: Optional[UserRecord]) -> Optional[UserRecord]:
        if user is None:
            return None
        user.tenant_name = self.get_tenant_name(user.tenant_id)
        return user

    def get_identity(self, provider: str, subject: str) -> Optional[Dict[str, str]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT provider, subject, user_id, tenant_id, email, display_name, created_at FROM external_identities WHERE provider = ? AND subject = ?",
                (provider, subject),
            ).fetchone()
        if row is None:
            return None
        return {
            "provider": str(row["provider"]),
            "subject": str(row["subject"]),
            "user_id": str(row["user_id"]),
            "tenant_id": str(row["tenant_id"] or ""),
            "email": str(row["email"] or ""),
            "display_name": str(row["display_name"] or ""),
            "created_at": str(row["created_at"]),
        }

    def link_identity(self, *, provider: str, subject: str, user_id: str, tenant_id: Optional[str], email: str, display_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO external_identities (provider, subject, user_id, tenant_id, email, display_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, subject) DO UPDATE SET
                    user_id = excluded.user_id,
                    tenant_id = excluded.tenant_id,
                    email = excluded.email,
                    display_name = excluded.display_name
                """,
                (provider, subject, user_id, tenant_id, email, display_name, _utc_now()),
            )
            conn.commit()

    def get_package_by_slug(self, slug: str) -> Optional[PackageRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_packages WHERE slug = ?",
                (slug,),
            ).fetchone()
        return _row_to_package(row)

    def upsert_package(
        self,
        *,
        owner_user_id: str,
        owner_username: str,
        slug: str,
        name: str,
        version: str,
        description: str,
        manifest: Dict[str, Any],
        config_path: str,
        package_path: str,
        restarted: bool,
    ) -> PackageRecord:
        existing = self.get_package_by_slug(slug)
        now = _utc_now()
        manifest_json = json.dumps(manifest)
        if existing is None:
            package = PackageRecord(
                package_id=str(uuid.uuid4()),
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                slug=slug,
                name=name,
                version=version,
                description=description,
                manifest_json=manifest_json,
                config_path=config_path,
                package_path=package_path,
                status="active",
                uploaded_at=now,
                updated_at=now,
                restart_count=0,
                traffic_count=0,
                last_run_at=None,
            )
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_packages (
                        package_id, owner_user_id, owner_username, slug, name, version, description,
                        manifest_json, config_path, package_path, status, uploaded_at, updated_at,
                        restart_count, traffic_count, last_run_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package.package_id,
                        package.owner_user_id,
                        package.owner_username,
                        package.slug,
                        package.name,
                        package.version,
                        package.description,
                        package.manifest_json,
                        package.config_path,
                        package.package_path,
                        package.status,
                        package.uploaded_at,
                        package.updated_at,
                        package.restart_count,
                        package.traffic_count,
                        package.last_run_at,
                    ),
                )
                conn.commit()
            return package

        restart_count = existing.restart_count + (1 if restarted else 0)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_packages
                SET owner_user_id = ?,
                    owner_username = ?,
                    name = ?,
                    version = ?,
                    description = ?,
                    manifest_json = ?,
                    config_path = ?,
                    package_path = ?,
                    status = ?,
                    updated_at = ?,
                    restart_count = ?
                WHERE slug = ?
                """,
                (
                    owner_user_id,
                    owner_username,
                    name,
                    version,
                    description,
                    manifest_json,
                    config_path,
                    package_path,
                    "active",
                    now,
                    restart_count,
                    slug,
                ),
            )
            conn.commit()
        updated = self.get_package_by_slug(slug)
        if updated is None:
            raise RuntimeError(f"Failed to update package '{slug}'")
        return updated

    def list_packages(self, owner_user_id: Optional[str] = None) -> List[PackageRecord]:
        with self._connect() as conn:
            if owner_user_id:
                rows = conn.execute(
                    "SELECT * FROM agent_packages WHERE owner_user_id = ? ORDER BY updated_at DESC",
                    (owner_user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_packages ORDER BY updated_at DESC"
                ).fetchall()
        return [_row_to_package(row) for row in rows if row is not None]

    def bump_package_traffic(self, config_path: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_packages
                SET traffic_count = traffic_count + 1,
                    last_run_at = ?,
                    updated_at = ?
                WHERE config_path = ?
                """,
                (now, now, config_path),
            )
            conn.commit()

    def upsert_worker(
        self,
        *,
        worker_id: str,
        owner_user_id: str,
        owner_username: str,
        hostname: str,
        runtime_url: str,
        status: str,
        capabilities: Dict[str, Any],
    ) -> WorkerRecord:
        existing = self.get_worker(worker_id)
        now = _utc_now()
        capabilities_json = json.dumps(capabilities or {}, sort_keys=True)
        with self._connect() as conn:
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO worker_nodes (
                        worker_id, owner_user_id, owner_username, hostname, runtime_url, status,
                        capabilities_json, last_seen_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        worker_id,
                        owner_user_id,
                        owner_username,
                        hostname,
                        runtime_url,
                        status,
                        capabilities_json,
                        now,
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE worker_nodes
                    SET owner_user_id = ?,
                        owner_username = ?,
                        hostname = ?,
                        runtime_url = ?,
                        status = ?,
                        capabilities_json = ?,
                        last_seen_at = ?,
                        updated_at = ?
                    WHERE worker_id = ?
                    """,
                    (
                        owner_user_id,
                        owner_username,
                        hostname,
                        runtime_url,
                        status,
                        capabilities_json,
                        now,
                        now,
                        worker_id,
                    ),
                )
            conn.commit()
        worker = self.get_worker(worker_id)
        if worker is None:
            raise RuntimeError(f"Failed to upsert worker '{worker_id}'")
        return worker

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_nodes WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
        return _row_to_worker(row)

    def list_workers(self, owner_user_id: Optional[str] = None) -> List[WorkerRecord]:
        with self._connect() as conn:
            if owner_user_id:
                rows = conn.execute(
                    "SELECT * FROM worker_nodes WHERE owner_user_id = ? ORDER BY updated_at DESC",
                    (owner_user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM worker_nodes ORDER BY updated_at DESC"
                ).fetchall()
        return [_row_to_worker(row) for row in rows if row is not None]

    def upsert_worker_agents(
        self,
        *,
        worker_id: str,
        owner_user_id: str,
        owner_username: str,
        agents: List[Dict[str, Any]],
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("DELETE FROM worker_agents WHERE worker_id = ?", (worker_id,))
            for item in agents:
                slug = str(item.get("agent_slug") or "").strip()
                if not slug:
                    continue
                conn.execute(
                    """
                    INSERT INTO worker_agents (
                        worker_id, owner_user_id, owner_username, agent_slug, agent_name,
                        manifest_json, config_json, config_path, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        worker_id,
                        owner_user_id,
                        owner_username,
                        slug,
                        str(item.get("agent_name") or slug),
                        json.dumps(item.get("manifest") or {}, sort_keys=True),
                        json.dumps(item.get("config") or {}, sort_keys=True),
                        str(item.get("config_path") or ""),
                        now,
                    ),
                )
            conn.commit()

    def list_worker_agents(
        self,
        owner_user_id: Optional[str] = None,
        *,
        worker_id: Optional[str] = None,
    ) -> List[WorkerAgentRecord]:
        query = "SELECT * FROM worker_agents"
        clauses: List[str] = []
        params: List[str] = []
        if owner_user_id:
            clauses.append("owner_user_id = ?")
            params.append(owner_user_id)
        if worker_id:
            clauses.append("worker_id = ?")
            params.append(worker_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY last_seen_at DESC, agent_slug ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [_row_to_worker_agent(row) for row in rows if row is not None]

    def build_discovery_map(self, owner_user_id: Optional[str] = None) -> Dict[str, Any]:
        workers = self.list_workers(owner_user_id)
        worker_agents = self.list_worker_agents(owner_user_id)
        by_worker: Dict[str, List[WorkerAgentRecord]] = {}
        for item in worker_agents:
            by_worker.setdefault(item.worker_id, []).append(item)
        return {
            "workers": [
                {
                    "worker_id": worker.worker_id,
                    "owner_user_id": worker.owner_user_id,
                    "owner_username": worker.owner_username,
                    "hostname": worker.hostname,
                    "runtime_url": worker.runtime_url,
                    "status": worker.status,
                    "capabilities": json.loads(worker.capabilities_json or "{}"),
                    "last_seen_at": worker.last_seen_at,
                    "created_at": worker.created_at,
                    "updated_at": worker.updated_at,
                    "agents": [
                        {
                            "agent_slug": agent.agent_slug,
                            "agent_name": agent.agent_name,
                            "config_path": agent.config_path,
                            "manifest": json.loads(agent.manifest_json or "{}"),
                            "config": json.loads(agent.config_json or "{}"),
                            "last_seen_at": agent.last_seen_at,
                        }
                        for agent in by_worker.get(worker.worker_id, [])
                    ],
                }
                for worker in workers
            ]
        }


def _row_to_user(row: sqlite3.Row | None) -> Optional[UserRecord]:
    if row is None:
        return None
    return UserRecord(
        user_id=str(row["user_id"]),
        tenant_id=(str(row["tenant_id"]) if row["tenant_id"] else None),
        tenant_name=None,
        username=str(row["username"]),
        email=str(row["email"] or ""),
        display_name=str(row["display_name"]),
        role=str(row["role"]),
        password_hash=str(row["password_hash"]),
        salt=str(row["salt"]),
        active=bool(row["active"]),
        created_at=str(row["created_at"]),
    )


def _row_to_package(row: sqlite3.Row | None) -> Optional[PackageRecord]:
    if row is None:
        return None
    return PackageRecord(
        package_id=str(row["package_id"]),
        owner_user_id=str(row["owner_user_id"]),
        owner_username=str(row["owner_username"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        version=str(row["version"]),
        description=str(row["description"] or ""),
        manifest_json=str(row["manifest_json"]),
        config_path=str(row["config_path"]),
        package_path=str(row["package_path"]),
        status=str(row["status"]),
        uploaded_at=str(row["uploaded_at"]),
        updated_at=str(row["updated_at"]),
        restart_count=int(row["restart_count"]),
        traffic_count=int(row["traffic_count"]),
        last_run_at=(str(row["last_run_at"]) if row["last_run_at"] else None),
    )


def _row_to_worker(row: sqlite3.Row | None) -> Optional[WorkerRecord]:
    if row is None:
        return None
    return WorkerRecord(
        worker_id=str(row["worker_id"]),
        owner_user_id=str(row["owner_user_id"]),
        owner_username=str(row["owner_username"]),
        hostname=str(row["hostname"]),
        runtime_url=str(row["runtime_url"]),
        status=str(row["status"]),
        capabilities_json=str(row["capabilities_json"] or "{}"),
        last_seen_at=str(row["last_seen_at"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_worker_agent(row: sqlite3.Row | None) -> Optional[WorkerAgentRecord]:
    if row is None:
        return None
    return WorkerAgentRecord(
        worker_id=str(row["worker_id"]),
        owner_user_id=str(row["owner_user_id"]),
        owner_username=str(row["owner_username"]),
        agent_slug=str(row["agent_slug"]),
        agent_name=str(row["agent_name"]),
        manifest_json=str(row["manifest_json"] or "{}"),
        config_json=str(row["config_json"] or "{}"),
        config_path=str(row["config_path"] or ""),
        last_seen_at=str(row["last_seen_at"]),
    )


def _row_to_tenant(row: sqlite3.Row | None) -> Optional[TenantRecord]:
    if row is None:
        return None
    return TenantRecord(
        tenant_id=str(row["tenant_id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        primary_domain=str(row["primary_domain"]),
        contact_email=str(row["contact_email"]),
        created_at=str(row["created_at"]),
    )


def _slugify(value: str) -> str:
    allowed = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    slug = "-".join(part for part in allowed.split("-") if part)
    return slug or f"tenant-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
