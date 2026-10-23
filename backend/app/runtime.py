from dataclasses import dataclass

from .cache import SupabaseCache
from .config import Settings, load_settings
from .providers.base import MarketDataProvider
from .providers.factory import build_provider
from .service import MarketDataService
from .analysis.analysis_service import AnalysisService
from .stream import FeedHub
from .setup_store import SupabaseSetupStore
from .backtest_store import SupabaseBacktestStore


@dataclass
class Runtime:
    settings: Settings
    provider: MarketDataProvider
    cache: SupabaseCache | None
    service: MarketDataService
    hub: FeedHub
    analysis: AnalysisService
    setup_store: SupabaseSetupStore | None
    backtest_store: SupabaseBacktestStore | None = None


_runtime: Runtime | None = None


def build_runtime(settings: Settings, provider: MarketDataProvider | None = None,
                  cache: SupabaseCache | None = None) -> Runtime:
    provider = provider or build_provider(settings)
    # Cached rows are shared by instrument/timeframe only. Do not mix data from different providers.
    if settings.market_data_provider == "biquote":
        cache = None
    if cache is None and settings.supabase_configured:
        if settings.market_data_provider != "biquote":
            cache = SupabaseCache(settings.supabase_url, settings.supabase_key)
    setup_store = SupabaseSetupStore(settings.supabase_url, settings.supabase_key) if settings.supabase_configured else None
    backtest_store = SupabaseBacktestStore(settings.supabase_url, settings.supabase_key) if settings.supabase_configured else None
    service = MarketDataService(provider, cache)
    return Runtime(settings, provider, cache, service, FeedHub(service, provider), AnalysisService(service), setup_store, backtest_store)


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime(load_settings())
    return _runtime


def set_runtime(rt: Runtime | None) -> None:
    global _runtime
    _runtime = rt
