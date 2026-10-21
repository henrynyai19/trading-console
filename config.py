import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
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


def load_settings() -> Settings:
    load_dotenv()
    g = lambda k, d="": os.getenv(k, d).strip()  # noqa: E731
    return Settings(
        oanda_api_key=g("OANDA_API_KEY"),
        oanda_account_id=g("OANDA_ACCOUNT_ID"),
        oanda_env=g("OANDA_ENV", "practice").lower() or "practice",
        supabase_url=g("SUPABASE_URL").rstrip("/"),
        supabase_key=g("SUPABASE_SERVICE_KEY"),
        allowed_origin=g("ALLOWED_ORIGIN", "http://localhost:3000"),
        oanda_rest_url=g("OANDA_REST_URL").rstrip("/"),
        oanda_stream_url=g("OANDA_STREAM_URL").rstrip("/"),
    )
