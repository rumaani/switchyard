"""Runtime configuration, read from environment variables (or a .env file).

Every setting has a safe development default. `Settings.validate_for_production`
refuses to start with development-only settings when ENVIRONMENT=production.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- general -----------------------------------------------------------
    app_name: str = "Switchyard"
    environment: str = "development"  # development | production
    public_url: str = "http://localhost:8000"  # browser-facing base URL of this app
    secret_key: str = "dev-only-change-me-dev-only-change-me"  # signs sessions + derives the encryption key
    database_url: str = "sqlite:///./switchyard.db"
    static_dir: str = ""  # built UI (frontend/dist); served at / when set
    seed_demo: bool = True  # create demo endpoints + workflow on an empty database
    demo_mcp_url: str = "http://localhost:9100"  # where the bundled mock vendor servers listen
    run_mode: str = "background"  # background | inline (inline = await the whole run; used by tests)

    # --- sign-in -----------------------------------------------------------
    auth_mode: str = "dev"  # dev | oidc
    admin_emails: str = ""  # comma list; always admin (bootstrap / break-glass)
    session_max_age_s: int = 8 * 3600

    oidc_discovery_url: str = ""  # e.g. https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_groups_claim: str = "groups"  # Entra app roles: "roles"; Okta / Keycloak: "groups"
    oidc_provider_name: str = "your company SSO"

    # --- outbound MCP --------------------------------------------------------
    mcp_timeout_s: float = 60.0
    mcp_verify_tls: bool = True

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def validate_for_production(self) -> None:
        if not self.is_production:
            return
        problems = []
        if self.auth_mode != "oidc":
            problems.append("AUTH_MODE must be 'oidc' in production")
        if "dev-only" in self.secret_key or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a random value of 32+ characters")
        if not self.public_url.startswith("https://"):
            problems.append("PUBLIC_URL must be https in production")
        if problems:
            raise RuntimeError("Refusing to start: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
