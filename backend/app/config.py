import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    deployment_mode: str = "local"
    market_data_provider: str = ""
    oanda_api_key: str = field(default="", repr=False)  # repr=False: never printed by accident
    oanda_account_id: str = ""
    oanda_env: str = "practice"
    supabase_url: str = ""
    supabase_key: str = field(default="", repr=False)
    allowed_origin: str = "http://localhost:3000"
    # Test-only overrides (point the adapter at scripts/mock_oanda.py). Leave empty in real use.
    oanda_rest_url: str = ""
    oanda_stream_url: str = ""

    @property
    def oanda_configured(self) -> bool:
        return bool(self.oanda_api_key and self.oanda_account_id)

    @property
    def env_valid(self) -> bool:
        return self.oanda_env in ("practice", "live")

    @property
    def rest_host(self) -> str:
        if self.oanda_rest_url:
            return self.oanda_rest_url
        return "https://api-fxtrade.oanda.com" if self.oanda_env == "live" else "https://api-fxpractice.oanda.com"

    @property
    def stream_host(self) -> str:
        if self.oanda_stream_url:
            return self.oanda_stream_url
        return "https://stream-fxtrade.oanda.com" if self.oanda_env == "live" else "https://stream-fxpractice.oanda.com"

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def supabase_incomplete(self) -> bool:
        return bool(self.supabase_url) != bool(self.supabase_key)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.allowed_origin.split(",") if origin.strip()]


def validate_settings(settings: Settings) -> None:
    """Reject unsafe or contradictory server configuration before serving requests."""
    errors: list[str] = []
    if settings.app_env not in {"development", "test", "production"}:
        errors.append("APP_ENV must be development, test, or production")
    if settings.deployment_mode not in {"local", "vercel"}:
        errors.append("DEPLOYMENT_MODE must be local or vercel")
    if settings.deployment_mode == "vercel" and settings.app_env != "production":
        errors.append("DEPLOYMENT_MODE=vercel requires APP_ENV=production")
    if settings.market_data_provider not in {"", "biquote", "oanda"}:
        errors.append("MARKET_DATA_PROVIDER must be biquote or oanda")
    if settings.app_env == "production" and not settings.market_data_provider:
        errors.append("MARKET_DATA_PROVIDER is required in production")
    if settings.market_data_provider == "oanda":
        if not settings.oanda_api_key or not settings.oanda_account_id:
            errors.append("OANDA_API_KEY and OANDA_ACCOUNT_ID are both required when MARKET_DATA_PROVIDER=oanda")
        if settings.oanda_env not in {"practice", "live"}:
            errors.append("OANDA_ENV must be practice or live")
    if settings.app_env == "production" and settings.supabase_incomplete:
        errors.append("SUPABASE_URL and SUPABASE_SERVICE_KEY must either both be set or both be empty")
    if settings.supabase_url:
        parsed = urlparse(settings.supabase_url)
        if parsed.scheme not in ({"https"} if settings.app_env == "production" else {"https", "http"}) or not parsed.netloc:
            errors.append("SUPABASE_URL must be an absolute HTTPS URL in production")
    if settings.deployment_mode == "local" and not settings.allowed_origins:
        errors.append("ALLOWED_ORIGIN(S) must contain at least one browser origin")
    for origin in settings.allowed_origins:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            errors.append("ALLOWED_ORIGIN(S) must contain only absolute origins without paths or query strings")
        if settings.app_env == "production" and parsed.scheme != "https":
            errors.append("ALLOWED_ORIGIN(S) must use HTTPS in production")
    if settings.app_env == "production" and (settings.oanda_rest_url or settings.oanda_stream_url):
        errors.append("OANDA test endpoint overrides are forbidden in production")
    if errors:
        raise ValueError("Invalid server configuration: " + "; ".join(errors))


def load_settings() -> Settings:
    load_dotenv()
    g = lambda k, d="": os.getenv(k, d).strip()  # noqa: E731
    deployment_mode = g("DEPLOYMENT_MODE", "local").lower() or "local"
    allowed_origin = g("ALLOWED_ORIGINS", g("ALLOWED_ORIGIN"))
    if not allowed_origin and deployment_mode == "local":
        allowed_origin = "http://localhost:3000"
    return Settings(
        app_env=g("APP_ENV", "development").lower() or "development",
        deployment_mode=deployment_mode,
        market_data_provider=g("MARKET_DATA_PROVIDER", "biquote").lower() or "biquote",
        oanda_api_key=g("OANDA_API_KEY"),
        oanda_account_id=g("OANDA_ACCOUNT_ID"),
        oanda_env=g("OANDA_ENV", "practice").lower() or "practice",
        supabase_url=g("SUPABASE_URL").rstrip("/"),
        supabase_key=g("SUPABASE_SERVICE_KEY"),
        allowed_origin=allowed_origin,
        oanda_rest_url=g("OANDA_REST_URL").rstrip("/"),
        oanda_stream_url=g("OANDA_STREAM_URL").rstrip("/"),
    )
