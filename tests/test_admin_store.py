from pathlib import Path

from agx.admin_store import AdminStore
from agx.security import AuthManager


def test_admin_store_bootstrap_and_package_tracking(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGX_BOOTSTRAP_USERS",
        '[{"tenant_name":"Emerson","tenant_domain":"emerson.com","username":"admin","email":"admin@emerson.com","password":"pw","role":"admin","display_name":"Admin"}]',
    )
    store = AdminStore(Path(tmp_path) / "admin.db")
    auth = AuthManager(secret="test")
    store.bootstrap_users(auth)

    user = store.get_user_by_username("admin")
    assert user is not None
    assert user.role == "admin"
    assert user.email == "admin@emerson.com"
    assert user.tenant_name == "Emerson"

    package = store.upsert_package(
        owner_user_id=user.user_id,
        owner_username=user.username,
        slug="sample_agent",
        name="Sample Agent",
        version="1.0.0",
        description="demo",
        manifest={"name": "Sample Agent"},
        config_path="/srv/agents/sample_agent/config.yaml",
        package_path="/srv/agents/sample_agent",
        restarted=False,
    )
    assert package.slug == "sample_agent"

    store.bump_package_traffic("/srv/agents/sample_agent/config.yaml")
    refreshed = store.get_package_by_slug("sample_agent")

    assert refreshed is not None
    assert refreshed.traffic_count == 1


def test_admin_store_worker_discovery_mapping(tmp_path):
    store = AdminStore(Path(tmp_path) / "admin.db")

    worker = store.upsert_worker(
        worker_id="worker-a",
        owner_user_id="user-1",
        owner_username="alice",
        hostname="alice-laptop",
        runtime_url="http://host-a:8000",
        status="online",
        capabilities={"agent_execution": True},
    )
    assert worker.worker_id == "worker-a"

    store.upsert_worker_agents(
        worker_id="worker-a",
        owner_user_id="user-1",
        owner_username="alice",
        agents=[
            {
                "agent_slug": "edge_inference",
                "agent_name": "Edge Inference",
                "manifest": {"name": "Edge Inference"},
                "config": {"name": "edge", "agents": {"a": {"tools": []}}, "tasks": [{"id": "t1", "agent": "a", "description": "d"}]},
                "config_path": "/Users/alice/agents/edge_inference/config.yaml",
            }
        ],
    )

    discovery = store.build_discovery_map()
    assert len(discovery["workers"]) == 1
    assert discovery["workers"][0]["worker_id"] == "worker-a"
    assert discovery["workers"][0]["agents"][0]["agent_slug"] == "edge_inference"
