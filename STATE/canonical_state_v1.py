from __future__ import annotations

import pandas as pd
from dataclasses import dataclass
from typing import Dict, Any, Optional


REQUIRED_COLS = ["open", "high", "low", "close", "volume"]


def require_ohlcv(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame index must be a DatetimeIndex (timezone-aware preferred).")


@dataclass(frozen=True)
class CanonicalState:
    symbol: str
    timeframe: str
    asof: str  # ISO timestamp (UTC recommended)
    last_price: float
    pdh: Optional[float]
    pdl: Optional[float]
    htf_bias: str
    volatility: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "asof": self.asof,
            "lastPrice": self.last_price,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "htfBias": self.htf_bias,
            "volatility": self.volatility,
        }


def build_minimal_state(df: pd.DataFrame, *, symbol: str, timeframe: str) -> CanonicalState:
    """
    Minimal deterministic state builder (v1).
    Extend later with:
      - pivots/swings (no-lookahead confirmation)
      - MSS/BOS state
      - FVG list
      - session context
      - regime / compression
    """
    require_ohlcv(df)
    last = df.iloc[-1]
    asof = df.index[-1].tz_convert("UTC").isoformat().replace("+00:00", "Z") if df.index[-1].tzinfo else df.index[-1].isoformat()

    # Simple PDH/PDL placeholders (real version uses prior day boundaries)
    pdh = float(df["high"].tail(96).max()) if len(df) >= 2 else None
    pdl = float(df["low"].tail(96).min()) if len(df) >= 2 else None

    # Basic volatility heuristic placeholder
    rng = (df["high"] - df["low"]).tail(48)
    volatility = "High" if rng.mean() > (rng.median() * 1.5) else "Normal"

    return CanonicalState(
        symbol=symbol,
        timeframe=timeframe,
        asof=asof,
        last_price=float(last["close"]),
        pdh=pdh,
        pdl=pdl,
        htf_bias="Unknown",
        volatility=volatility,
    )