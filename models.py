"""Typed response schemas. Validators reject non-finite or inconsistent output before it reaches the client."""
import math
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Direction = Literal["bullish", "bearish"]
TrendClass = Literal["Strong Bullish", "Bullish", "Sideways", "Bearish", "Strong Bearish", "Transitional"]


def _finite_pos(v: float) -> float:
    if not (math.isfinite(v) and v > 0):
        raise ValueError("price must be finite and positive")
    return v


class SwingPoint(BaseModel):
    id: str
    kind: Literal["high", "low"]
    label: Optional[Literal["HH", "LH", "EQH", "HL", "LL", "EQL"]] = None
    price: float
    time: int
    confirmed_at: int  # close time of the candle that confirmed the pivot
    timeframe: str
    strength: float

    _p = field_validator("price")(_finite_pos)

    @model_validator(mode="after")
    def _order(self):
        if self.confirmed_at <= self.time:
            raise ValueError("a swing cannot be confirmed before its own candle closes")
        return self


class Zone(BaseModel):
    id: str
    instrument: str
    timeframe: str
    type: Literal["support", "resistance"]
    origin_type: Literal["support", "resistance"]
    upper: float
    lower: float
    strength: float = Field(ge=0, le=100)
    touches: int = Field(ge=0)
    start_time: int
    created_at: int
    last_interaction: int
    status: Literal["active", "invalidated"]
    role_reversed: bool = False
    role_reversal_time: Optional[int] = None

    _u = field_validator("upper")(_finite_pos)
    _l = field_validator("lower")(_finite_pos)

    @model_validator(mode="after")
    def _bounds(self):
        if self.upper <= self.lower:
            raise ValueError("zone upper must exceed lower")
        return self


class StructureEvent(BaseModel):
    id: str
    kind: Literal["BOS", "CHoCH"]
    direction: Direction
    level: float
    broken_swing_time: int
    swing_confirmed_at: int
    break_time: int
    confirmed_at: int  # close of the break candle: the moment the event became knowable
    instrument: str
    timeframe: str

    _p = field_validator("level")(_finite_pos)

    @model_validator(mode="after")
    def _order(self):
        if not (self.broken_swing_time < self.swing_confirmed_at <= self.break_time < self.confirmed_at):
            raise ValueError("structure event timestamps are inconsistent")
        return self


class TrendlinePoint(BaseModel):
    time: int
    price: float


class Trendline(BaseModel):
    id: str
    timeframe: str
    direction: Direction
    anchors: list[TrendlinePoint]
    start_time: int
    start_price: float
    end_time: int
    end_price: float
    slope_per_bar: float
    touches: int
    strength: float = Field(ge=0, le=100)
    status: Literal["active", "broken"]
    break_time: Optional[int] = None
    created_at: int

    @model_validator(mode="after")
    def _ok(self):
        if len(self.anchors) < 2 or self.end_time <= self.start_time:
            raise ValueError("trendline needs two anchors and a positive span")
        if not all(math.isfinite(x) for x in (self.start_price, self.end_price, self.slope_per_bar)):
            raise ValueError("non-finite trendline")
        return self


class TrendComponent(BaseModel):
    name: str
    points: float
    max_points: float
    detail: str


class TrendInfo(BaseModel):
    classification: TrendClass
    score: float = Field(ge=-100, le=100)  # rule-based, NOT a probability
    components: list[TrendComponent]
    explanation: list[str]
    structure_state: Literal["bullish", "bearish", "undetermined"]
    bars_since_last_event: Optional[int] = None


class Freshness(BaseModel):
    status: Literal["fresh", "stale", "market_closed", "unknown"]
    last_closed_time: Optional[int] = None
    last_close_at: Optional[int] = None
    age_seconds: Optional[float] = None
    trading_age_seconds: Optional[float] = None  # age with the weekend closure removed


class TFTrend(BaseModel):
    timeframe: str
    classification: Optional[TrendClass] = None
    direction: Literal["bullish", "bearish", "neutral", "unavailable"]
    score: Optional[float] = None
    structure_state: Literal["bullish", "bearish", "undetermined"] = "undetermined"
    reason: Optional[str] = None


class MTF(BaseModel):
    primary: TFTrend
    higher: list[TFTrend]
    alignment: Literal["aligned", "partial", "conflicting", "neutral", "unavailable"]
    structural_alignment: Literal["aligned", "partial", "conflicting", "undetermined"]
    bias: Literal["Bullish", "Leaning Bullish", "Bearish", "Leaning Bearish", "Mixed", "Neutral", "Unavailable"]
    conflicts: list[str]
    summary: str


class AnalysisResponse(BaseModel):
    instrument: str
    provider_symbol: str
    timeframe: str
    analysis_time: int
    status: Literal["ok", "insufficient_data", "stale"]
    reason: Optional[str] = None
    freshness: Freshness
    candles_used: int
    warnings: list[str] = []
    config: dict
    trend: Optional[TrendInfo] = None
    swings: list[SwingPoint] = []
    zones: list[Zone] = []
    nearest_support: Optional[str] = None
    nearest_resistance: Optional[str] = None
    events: list[StructureEvent] = []
    trendlines: list[Trendline] = []
    mtf: Optional[MTF] = None
    commentary: list[str] = []

    @model_validator(mode="after")
    def _consistent(self):
        t = [s.time for s in self.swings]
        if t != sorted(t):
            raise ValueError("swings must be ascending")
        e = [x.break_time for x in self.events]
        if e != sorted(e):
            raise ValueError("events must be ascending")
        ids = {z.id for z in self.zones}
        for ref in (self.nearest_support, self.nearest_resistance):
            if ref is not None and ref not in ids:
                raise ValueError("nearest zone reference is not in zones")
        if self.status != "ok" and (self.swings or self.zones or self.events or self.trendlines or self.trend):
            raise ValueError("confirmed analysis must not be returned for insufficient or stale data")
        return self
