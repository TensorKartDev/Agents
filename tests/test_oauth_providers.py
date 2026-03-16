from agx.oauth_providers import OAuthProvider, load_oauth_providers, visible_provider_cards


def test_load_oauth_providers_from_env(monkeypatch):
    monkeypatch.setenv("AGX_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("AGX_GOOGLE_CLIENT_SECRET", "gsecret")
    monkeypatch.setenv("AGX_GITHUB_CLIENT_ID", "ghid")
    monkeypatch.setenv("AGX_GITHUB_CLIENT_SECRET", "ghsecret")

    providers = load_oauth_providers()

    assert "google" in providers
    assert "github" in providers
    assert providers["google"].kind == "oidc"
    assert providers["github"].kind == "oauth2"


def test_visible_provider_cards_reflect_configured_providers_only():
    cards = visible_provider_cards(
        {
            "google": OAuthProvider(
                name="google",
                label="Google",
                kind="oidc",
                flow="fedcm",
                client_id="x",
                client_secret="y",
                scopes="openid email profile",
            )
        }
    )

    google = next(item for item in cards if item["name"] == "google")
    github = next(item for item in cards if item["name"] == "github")

    assert google["flow"] == "fedcm"
    assert google["enabled"] is True
    assert google["fedcm_enabled"] is True
    assert google["redirect_enabled"] is True
    assert github["enabled"] is False
