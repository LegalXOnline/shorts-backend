import os
from pathlib import Path
from typing import Literal
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Dynamically locate the root legalx-shorts-backend directory
BASE_DIR = Path(__file__).resolve().parent.parent
DEV_ENV_FILE = BASE_DIR / ".env.development"
PROD_ENV_FILE = BASE_DIR / ".env"

# All on-disk state lives under a single absolute staging root, so the app does
# not silently read an empty feed when launched from a different CWD.
STAGING_DIR = str(BASE_DIR / "staging")

# The dev env file is opt-in, never a fallback. Previously `.env.development`
# was preferred whenever it existed, so shipping it in a deploy artifact
# silently downgraded production to development settings (docs exposed, no
# HTTPS redirect, dev-only spend cap). Production now always reads `.env`.
_declared_env = os.environ.get("ENVIRONMENT", "").strip().lower()
if _declared_env == "production":
    ENV_FILE_PATH = PROD_ENV_FILE
elif _declared_env == "development":
    ENV_FILE_PATH = DEV_ENV_FILE if DEV_ENV_FILE.exists() else PROD_ENV_FILE
else:
    # No explicit declaration: prefer a real .env, fall back to the dev file.
    ENV_FILE_PATH = PROD_ENV_FILE if PROD_ENV_FILE.exists() else DEV_ENV_FILE


class Settings(BaseSettings):
    environment: Literal["development", "production"] = "development"
    ikanoon_token: str = ""
    groq_api_key: str = ""
    supabase_url: str = "https://placeholder.supabase.co"

    # Service key: write path only (bypasses RLS). Never used for public reads.
    supabase_service_key: str = "placeholder_service_key"
    # Anon key: public read path. Subject to RLS, so the `is_published = true`
    # policy in shorts_schema.sql becomes a real second line of defence behind
    # the application-level filter in the repository.
    supabase_anon_key: str = ""

    groq_gate_model: str = "llama-3.1-8b-instant"
    groq_generator_model: str = "llama-3.3-70b-versatile"

    # Spend cap on the metered IndianKanoon API. Enforced in every environment.
    max_ikanoon_calls: int = 500
    dev_max_ikanoon_calls: int = 50

    # S-6: CORS origins driven by config, not hardcoded
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Rate limits (slowapi syntax). Applied per-endpoint in app/api/*.
    feed_rate_limit: str = "30/minute"
    preview_rate_limit: str = "60/minute"

    # Reverse-proxy trust. X-Forwarded-For is only honoured when the immediate
    # peer is in this list, otherwise any client can forge its rate-limit key.
    trusted_proxies: list[str] = []

    # ── Reviewer preview tool ────────────────────────────────────────────
    # The preview UI can publish and delete staged cards, so it is off unless
    # explicitly enabled, and credential-protected outside development.
    enable_preview_ui: bool | None = None
    reviewer_username: str = ""
    reviewer_password: str = ""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, v: str) -> str:
        if v and not v.startswith("https://"):
            raise ValueError("supabase_url must start with https://")
        return v

    @field_validator("allowed_origins", "trusted_proxies", mode="before")
    @classmethod
    def split_csv(cls, v):
        """Accept comma-separated values as well as JSON lists.

        pydantic-settings only parses JSON for complex types, so the natural
        `ALLOWED_ORIGINS=https://a.com,https://b.com` used to raise at import
        and take the whole app down before serving a single request.
        """
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("["):
                return v  # let pydantic parse it as JSON
            return [item.strip() for item in s.split(",") if item.strip()]
        return v

    @field_validator("allowed_origins")
    @classmethod
    def reject_wildcard_with_credentials(cls, v: list[str]) -> list[str]:
        """`*` plus allow_credentials=True is not a valid CORS configuration.

        Starlette would send `Access-Control-Allow-Origin: *` alongside
        credentials, which every browser rejects. Failing loudly at startup is
        clearer than debugging silent CORS failures in the client.
        """
        if "*" in v:
            raise ValueError(
                "allowed_origins cannot contain '*' because CORS is configured with "
                "allow_credentials=True. List explicit origins instead."
            )
        return v

    @model_validator(mode="after")
    def resolve_preview_defaults(self) -> "Settings":
        """Default the preview UI on in development, off in production."""
        if self.enable_preview_ui is None:
            self.enable_preview_ui = self.environment == "development"
        return self

    @property
    def preview_requires_auth(self) -> bool:
        """Auth is mandatory outside development, honoured if set in development."""
        return self.environment == "production"

    @property
    def preview_credentials_set(self) -> bool:
        return bool(self.reviewer_username and len(self.reviewer_password) >= 12)

    @property
    def ikanoon_call_cap(self) -> int:
        """Effective per-run cap on paid IndianKanoon calls."""
        if self.environment == "development":
            return self.dev_max_ikanoon_calls
        return self.max_ikanoon_calls


settings = Settings()
