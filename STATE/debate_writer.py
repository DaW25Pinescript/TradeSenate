from __future__ import annotations

import json
import os
import time
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _now_iso_utc() -> str:
    # ISO8601 UTC with Z suffix
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _as_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]


def atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    """
    Windows-safe atomic write:
      - write to temp file in same directory
      - os.replace() to swap in-place
    """
    directory = os.path.dirname(path) or "."
    tmp_path = os.path.join(directory, f".{os.path.basename(path)}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


@dataclass(frozen=True)
class AgentOpinion:
    name: str
    role: str
    stance: str  # LONG | SHORT | NO_TRADE
    summary: str
    paragraphs: List[str]
    reasoning: List[str]
    confidence: float


def build_debate_json(
    *,
    symbol: str,
    timeframe: str,
    last_price: float,
    session: str,
    htf_bias: str,
    volatility: str,
    pdh: Optional[float] = None,
    pdl: Optional[float] = None,
    active_fvgs: Optional[List[str]] = None,
    agents: Optional[List[AgentOpinion]] = None,
    decision_action: str = "NO_TRADE",
    decision_reason: str = "",
    decision_next_check: str = "Next candle",
    decision_rule: str = "Let the market come to you.",
    sequence: int = 1,
) -> Dict[str, Any]:
    agents = agents or []
    active_fvgs = active_fvgs or []

    # Stable ordering by name so UI doesn't jump
    agents_sorted = sorted(agents, key=lambda a: (a.name or "").lower())

    ctx = {
        "symbol": symbol,
        "timeframe": timeframe,
        "lastPrice": last_price,
        "session": session,
        "htfBias": htf_bias,
        "volatility": volatility,
        "pdh": pdh,
        "pdl": pdl,
        "activeFvgs": active_fvgs,
    }
    state_hash = _stable_hash(ctx)

    out = {
        "meta": {
            "schemaVersion": "1.0",
            "title": f"Trade Senate · {symbol} {timeframe}",
            "generatedAt": _now_iso_utc(),
            "stateHash": state_hash,
            "sequence": sequence,
        },
        "context": ctx,
        "agents": [
            {
                "name": a.name,
                "role": a.role,
                "stance": a.stance,
                "summary": a.summary,
                "paragraphs": _as_list(a.paragraphs),
                "reasoning": _as_list(a.reasoning),
                "confidence": float(a.confidence),
            }
            for a in agents_sorted
        ],
        "decision": {
            "action": decision_action,
            "reason": decision_reason,
            "nextCheck": decision_next_check,
            "rule": decision_rule,
        },
    }
    return out


def write_demo(path: str = os.path.join("STATE", "debate.json")) -> None:
    agents = [
        AgentOpinion(
            name="Magnus",
            role="Chief of Staff",
            stance="NO_TRADE",
            summary="No-trade until structure aligns with HTF bias.",
            paragraphs=["Conflicting signals. Require a clean MSS→BOS and retest before committing risk."],
            reasoning=["HTF bias bullish but LTF structure not confirmed.", "Volatility normal → no need to force entries."],
            confidence=0.62,
        ),
        AgentOpinion(
            name="Atlas",
            role="Technical Analyst",
            stance="LONG",
            summary="Ascending support holding; higher lows.",
            paragraphs=["Price respecting a rising trendline. Risk can be defined below last swing low."],
            reasoning=["Two-touch trendline with clean reaction.", "PDH overhead may act as magnet / take-profit area."],
            confidence=0.58,
        ),
        AgentOpinion(
            name="Vela",
            role="Price Action Analyst",
            stance="NO_TRADE",
            summary="Needs MSS→BOS confirmation.",
            paragraphs=["Market is still overlapping; wait for displacement + BOS."],
            reasoning=["No clean MSS yet; pivots overlap → range conditions."],
            confidence=0.66,
        ),
        AgentOpinion(
            name="Nyx",
            role="News/Sentiment",
            stance="NO_TRADE",
            summary="No catalyst; headlines neutral.",
            paragraphs=["No high-impact event expected in the next hour."],
            reasoning=["Neutral sentiment → chop risk elevated."],
            confidence=0.55,
        ),
    ]

    payload = build_debate_json(
        symbol="EURUSD",
        timeframe="M15",
        last_price=1.0842,
        session="London",
        htf_bias="Bullish",
        volatility="Normal",
        pdh=1.0871,
        pdl=1.0798,
        active_fvgs=["1.0820–1.0830"],
        agents=agents,
        decision_action="NO_TRADE",
        decision_reason="Await MSS/BOS confirmation in direction of HTF bias.",
        decision_next_check="Next 1–2 candles",
        decision_rule="Let the market come to you.",
        sequence=int(time.time()),
    )
    atomic_write_json(path, payload)


if __name__ == "__main__":
    os.makedirs("STATE", exist_ok=True)
    write_demo()
    print("Wrote STATE/debate.json")