import json

import pytest

from app.providers.oanda import parse_candle, parse_candles, parse_stream_line, parse_time
from app.providers.base import Heartbeat, PriceTick
from tests.helpers import NOW, mk, raw


def test_parse_time_formats():
    assert parse_time("1700000040.000000000") == 1700000040.0
    assert parse_time("2023-11-14T22:14:00.000000000Z") == 1700000040.0
    with pytest.raises(ValueError):
        parse_time("2023-11-14T22:14:00")  # no timezone


def test_parse_candle_ok_and_flags():
    c = parse_candle(raw(mk(0, complete=False)), NOW)
    assert (c.complete, c.provisional, c.time) == (False, False, 1700000040)


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("mid"), lambda r: r.update(complete="yes"), lambda r: r["mid"].update(h="NaN"),
    lambda r: r["mid"].update(o="-1"), lambda r: r["mid"].update(h="0.5"), lambda r: r.update(time="abc"),
    lambda r: r.update(time=f"{NOW + 99999}.0"), lambda r: r.update(time="5.0"), lambda r: r.pop("time"),
])
def test_parse_candle_rejects_malformed(mutate):
    r = raw(mk(1))
    mutate(r)
    with pytest.raises(ValueError):
        parse_candle(r, NOW)


def test_parse_candles_orders_dedupes_and_drops_invalid():
    bad = raw(mk(3)); bad["mid"]["h"] = "0.1"
    payload = {"candles": [raw(mk(2)), raw(mk(0)), bad, raw(mk(2, complete=False)), raw(mk(1))]}
    out = parse_candles(payload, NOW)
    assert [c.time for c in out] == [mk(0).time, mk(1).time, mk(2).time]
    assert out[2].complete is True  # the complete duplicate wins


def test_parse_candles_rejects_non_candle_payload():
    with pytest.raises(ValueError):
        parse_candles({"oops": 1}, NOW)


def test_stream_price_and_heartbeat():
    p = parse_stream_line(json.dumps({"type": "PRICE", "time": "1700000050.0", "tradeable": True,
                                      "bids": [{"price": "1.1000"}], "asks": [{"price": "1.1002"}]}))
    assert isinstance(p, PriceTick) and p.tradeable and abs(p.mid - 1.1001) < 1e-9
    assert isinstance(parse_stream_line('{"type":"HEARTBEAT","time":"1700000055.0"}'), Heartbeat)
    assert parse_stream_line('{"type":"SOMETHING"}') is None


@pytest.mark.parametrize("line", ["not json", '{"type":"PRICE"}', '{"type":"PRICE","time":"1.0","bids":[{"price":"2"}],"asks":[{"price":"1"}]}',
                                  '{"type":"HEARTBEAT"}'])
def test_stream_malformed(line):
    with pytest.raises(ValueError):
        parse_stream_line(line)
