from agx.security import AuthManager, SessionUser


def test_auth_manager_hash_and_verify():
    auth = AuthManager(secret="test-secret")
    password_hash, salt = auth.hash_password("secret123")

    assert auth.verify_password("secret123", password_hash, salt)
    assert not auth.verify_password("wrong", password_hash, salt)


def test_auth_manager_round_trips_session():
    auth = AuthManager(secret="test-secret")
    user = SessionUser(
        user_id="u1",
        tenant_id="t1",
        tenant_name="Emerson",
        username="alice",
        email="alice@emerson.com",
        role="developer",
        display_name="Alice",
    )
    token = auth.issue_session(user)
    resolved = auth.read_session(token)

    assert resolved is not None
    assert resolved.user_id == "u1"
    assert resolved.tenant_id == "t1"
    assert resolved.username == "alice"
