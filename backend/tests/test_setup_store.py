import httpx

from app.providers.base import Candle
from app.setup_store import SupabaseSetupStore
from app.setups import TradeSetup, advance_setup


def _setup(**extra):
    value = dict(id="setup-1", instrument="XAUUSD", provider_symbol="XAU_USD", timeframe="15m",
                 setup_type="support_retest", direction="long", status="active", quality_score=70,
                 score_explanation=["test"], created_at=1000, expires_at=5000, invalidation_price=98,
                 reference_level=99.5, confirmation_explanation="closed-candle confirmation", invalidation_explanation="stop level",
                 confirmed_at=1000, confirmed_snapshot={"confirmed_at": 1000, "planned_entry": 99.5, "stop_loss": 98},
                 entry_lower=99, entry_upper=100, planned_entry=99.5, stop_loss=98, targets=[
                     {"r_multiple": 1.5, "price": 101.75}, {"r_multiple": 2, "price": 102.5}, {"r_multiple": 3, "price": 104}],
                 risk_distance=1.5, explanation="analytical only", source_zone_id="z1", source_event_id=None)
    value.update(extra)
    return TradeSetup(**value)


async def test_supabase_setup_store_upserts_reads_and_uses_server_key_only():
    saved = []

    def respond(request):
        assert request.headers["Authorization"] == "Bearer private-server-key"
        if request.method == "POST":
            saved.extend(request.read() and __import__("json").loads(request.content))
            return httpx.Response(204)
        return httpx.Response(200, json=[{"payload": _setup().model_dump(mode="json")}])

    store = SupabaseSetupStore("https://example.supabase.co", "private-server-key", httpx.MockTransport(respond))
    assert await store.upsert([_setup()])
    active = await store.active("XAUUSD", "15m")
    history = await store.history("XAUUSD", "15m", 10)
    assert saved[0]["id"] == "setup-1"
    assert active and active[0].id == "setup-1"
    assert history and history[0].status == "active"
    await store.close()


def test_setup_lifecycle_marks_invalidation_and_expiry_from_closed_candles():
    setup = _setup()
    stopped = advance_setup(setup, Candle(1200, 99, 100, 97.5, 99, 1, True), now=1300)
    expired = advance_setup(setup, Candle(1200, 99, 100, 99, 99, 1, True), now=5000)
    target = advance_setup(_setup(status="entry_touched"), Candle(1200, 100.5, 102, 100.2, 101, 1, True), now=1300)
    assert stopped.status == "invalidated"
    assert expired.status == "expired"
    assert target.status == "target_1_reached"
    assert target.confirmed_snapshot is not None
    assert target.lifecycle_events[-1].status == "target_1_reached"
