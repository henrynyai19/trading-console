from dataclasses import dataclass

from .cache import SupabaseCache
from .config import Settings, load_settings
from .providers.base import MarketDataProvider
from .providers.factory import build_provider
from .service import MarketDataService
from .analysis.analysis_service import AnalysisService
from .stream import FeedHub


@dataclass
class Runtime:
    settings: Settings
    provider: MarketDataProvider
    cache: SupabaseCache | None
    service: MarketDataService
    hub: FeedHub
    analysis: AnalysisService


_runtime: Runtime | None = None


def build_runtime(settings: Settings, provider: MarketDataProvider | None = None,
                  cache: SupabaseCache | None = None) -> Runtime:
    provider = provider or build_provider(settings)
    if cache is None and settings.supabase_configured:
        cache = SupabaseCache(settings.supabase_url, settings.supabase_key)
    service = MarketDataService(provider, cache)
    return Runtime(settings, provider, cache, service, FeedHub(service, provider), AnalysisService(service))


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime(load_settings())
    return _runtime


def set_runtime(rt: Runtime | None) -> None:
    global _runtime
    _runtime = rt
