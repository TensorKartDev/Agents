from pathlib import Path

from agx.workspace import resolve_workspace_paths


def test_workspace_paths_default_to_repo_layout(monkeypatch):
    monkeypatch.delenv("AGX_AGENTS_DIR", raising=False)
    monkeypatch.delenv("AGX_AGENT_REGISTRY", raising=False)
    monkeypatch.delenv("AGX_RUNS_DIR", raising=False)

    base_dir = Path("/tmp/agx-runtime")
    paths = resolve_workspace_paths(base_dir)

    assert paths.base_dir == base_dir
    assert paths.agents_dir == base_dir / "agents"
    assert paths.registry_path == base_dir / "agents" / "agents.yaml"
    assert paths.runs_dir == base_dir / ".agx" / "runs"


def test_workspace_paths_support_external_agent_and_run_locations(monkeypatch):
    monkeypatch.setenv("AGX_AGENTS_DIR", "/srv/tenant-a/agent-packs")
    monkeypatch.setenv("AGX_AGENT_REGISTRY", "/srv/tenant-a/registries/active-agents.yaml")
    monkeypatch.setenv("AGX_RUNS_DIR", "/var/lib/agx/runs")

    paths = resolve_workspace_paths(Path("/tmp/ignored-base"))

    assert paths.agents_dir == Path("/srv/tenant-a/agent-packs")
    assert paths.registry_path == Path("/srv/tenant-a/registries/active-agents.yaml")
    assert paths.runs_dir == Path("/var/lib/agx/runs")
