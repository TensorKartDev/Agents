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
