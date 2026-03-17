"""External OAuth/OIDC provider configuration for AGX."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    label: str
    kind: str
    flow: str
    client_id: str
    client_secret: str
    scopes: str
    server_metadata_url: Optional[str] = None
    authorize_url: Optional[str] = None
    access_token_url: Optional[str] = None
    api_base_url: Optional[str] = None
    userinfo_endpoint: Optional[str] = None


def load_oauth_providers() -> Dict[str, OAuthProvider]:
    providers: Dict[str, OAuthProvider] = {}

    google_id = os.getenv("AGX_GOOGLE_CLIENT_ID", "").strip()
    google_secret = os.getenv("AGX_GOOGLE_CLIENT_SECRET", "").strip()
    if google_id:
        providers["google"] = OAuthProvider(
            name="google",
            label="Google",
            kind="oidc",
            flow="fedcm",
            client_id=google_id,
            client_secret=google_secret,
            scopes="openid email profile",
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        )

    github_id = os.getenv("AGX_GITHUB_CLIENT_ID", "").strip()
    github_secret = os.getenv("AGX_GITHUB_CLIENT_SECRET", "").strip()
    if github_id and github_secret:
        providers["github"] = OAuthProvider(
            name="github",
            label="GitHub",
            kind="oauth2",
            flow="redirect",
            client_id=github_id,
            client_secret=github_secret,
            scopes="read:user user:email",
            authorize_url="https://github.com/login/oauth/authorize",
            access_token_url="https://github.com/login/oauth/access_token",
            api_base_url="https://api.github.com/",
            userinfo_endpoint="https://api.github.com/user",
        )

    okta_id = os.getenv("AGX_OKTA_CLIENT_ID", "").strip()
    okta_secret = os.getenv("AGX_OKTA_CLIENT_SECRET", "").strip()
    okta_issuer = os.getenv("AGX_OKTA_ISSUER", "").strip().rstrip("/")
    if okta_id and okta_secret and okta_issuer:
        providers["okta"] = OAuthProvider(
            name="okta",
            label="Okta",
            kind="oidc",
            flow="redirect",
            client_id=okta_id,
            client_secret=okta_secret,
            scopes="openid email profile",
            server_metadata_url=f"{okta_issuer}/.well-known/openid-configuration",
        )

    return providers


def visible_provider_cards(providers: Dict[str, OAuthProvider]) -> List[dict]:
    supported = {
        "google": {"label": "Google", "kind": "oidc", "flow": "fedcm"},
        "github": {"label": "GitHub", "kind": "oauth2", "flow": "redirect"},
        "okta": {"label": "Okta", "kind": "oidc", "flow": "redirect"},
    }
    cards = []
    for name, meta in supported.items():
        provider = providers.get(name)
        if provider is not None:
            card = {
                "name": provider.name,
                "label": provider.label,
                "kind": provider.kind,
                "flow": provider.flow,
                "enabled": True,
            }
            if provider.name == "google":
                card["client_id"] = provider.client_id
                card["fedcm_enabled"] = bool(provider.client_id)
                card["redirect_enabled"] = bool(provider.client_id and provider.client_secret)
            else:
                card["redirect_enabled"] = True
            cards.append(card)
            continue
        cards.append(
            {
                "name": name,
                "label": meta["label"],
                "kind": meta["kind"],
                "flow": meta["flow"],
                "enabled": False,
                "fedcm_enabled": False,
                "redirect_enabled": False,
                "reason": f"{meta['label']} sign-in is not configured on this AGX deployment.",
            }
        )
    return cards
