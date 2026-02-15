from __future__ import annotations

import argparse
import json
import os
import time
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# Pathing: anchor to repo root so relative runs can't "write somewhere else"
# -----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
STATE_DIR = os.path.join(ROOT, "STATE")
DEFAULT_DEBATE_PATH = os.path.join(STATE_DIR, "debate.json")
DEFAULT_STATE_PATH = os.path.join(STATE_DIR, "canonical_state.json")


def _now_iso_utc() -> str:
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
    os.makedirs(directory, exist_ok=True)
    tmp_path = os.path.join(directory, f".{os.path.basename(path)}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


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

    return {
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


# -----------------------------------------------------------------------------
# Deterministic "stub agents" driven by canonical_state.json (no LLM yet)
# -----------------------------------------------------------------------------
def stub_agents_from_state(state: Dict[str, Any]) -> List[AgentOpinion]:
    htf = str(state.get("htfBias", "Unknown"))
    vol = str(state.get("volatility", "Normal"))

    # Simple deterministic stances for plumbing validation
    if htf.lower() in ("bullish", "up", "long"):
        atlas_stance = "LONG"
        atlas_conf = 0.58
    elif htf.lower() in ("bearish", "down", "short"):
        atlas_stance = "SHORT"
        atlas_conf = 0.58
    else:
        atlas_stance = "NO_TRADE"
        atlas_conf = 0.52

    chop_risk = "elevated" if vol.lower() in ("high", "elevated") else "normal"

    agents: List[AgentOpinion] = [
        AgentOpinion(
            name="Magnus",
            role="Chief of Staff",
            stance="NO_TRADE",
            summary="No-trade until structure aligns with HTF bias.",
            paragraphs=["Require a clean confirmation (MSS→BOS + retest) before committing risk."],
            reasoning=[f"HTF bias = {htf}.", f"Volatility = {vol} → chop risk {chop_risk}."],
            confidence=0.62,
        ),
        AgentOpinion(
            name="Atlas",
            role="Technical Analyst",
            stance=atlas_stance,
            summary="Trend/levels bias follows HTF until invalidated.",
            paragraphs=["Treat PDH/PDL as magnets; prefer entries after clear structure confirmation."],
            reasoning=[f"Bias derived from HTF = {htf}.", "Waiting for clean level interaction / confirmation."],
            confidence=atlas_conf,
        ),
        AgentOpinion(
            name="Vela",
            role="Price Action Analyst",
            stance="NO_TRADE",
            summary="Needs MSS→BOS confirmation.",
            paragraphs=["If market is overlapping/ranging, wait for displacement + BOS."],
            reasoning=["Without MSS/BOS, entries are lower quality."],
            confidence=0.66,
        ),
        AgentOpinion(
            name="Nyx",
            role="News/Sentiment",
            stance="NO_TRADE",
            summary="No catalyst (stub).",
            paragraphs=["(Stub) Wire real news feed later."],
            reasoning=["Sentiment module not connected yet."],
            confidence=0.55,
        ),
    ]
    return agents


def build_from_canonical_state(state: Dict[str, Any]) -> Dict[str, Any]:
    symbol = str(state.get("symbol", "UNKNOWN"))
    timeframe = str(state.get("timeframe", ""))
    last_price = float(state.get("lastPrice", state.get("last_price", 0.0)) or 0.0)
    pdh = state.get("pdh", None)
    pdl = state.get("pdl", None)
    htf_bias = str(state.get("htfBias", "Unknown"))
    volatility = str(state.get("volatility", "Normal"))

    # Session/FVG are not in minimal canonical_state_v1 yet :contentReference[oaicite:2]{index=2}
    session = str(state.get("session", "Unknown"))
    active_fvgs = state.get("activeFvgs", []) or []

    agents = stub_agents_from_state(state)

    decision_action = "NO_TRADE"
    decision_reason = "Await MSS/BOS confirmation in direction of HTF bias."
    decision_next = "Next 1–2 candles"
    decision_rule = "Let the market come to you."

    return build_debate_json(
        symbol=symbol,
        timeframe=timeframe,
        last_price=last_price,
        session=session,
        htf_bias=htf_bias,
        volatility=volatility,
        pdh=float(pdh) if pdh is not None else None,
        pdl=float(pdl) if pdl is not None else None,
        active_fvgs=[str(x) for x in active_fvgs],
        agents=agents,
        decision_action=decision_action,
        decision_reason=decision_reason,
        decision_next_check=decision_next,
        decision_rule=decision_rule,
        sequence=int(time.time()),
    )


def write_debate(out_path: str, state_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    state = read_json(state_path)
    if state is None:
        # Fallback: minimal demo state if canonical state not present yet
        state = {
            "schemaVersion": "1.0",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "asof": _now_iso_utc(),
            "lastPrice": 1.0842,
            "pdh": 1.0871,
            "pdl": 1.0798,
            "htfBias": "Bullish",
            "volatility": "Normal",
            "session": "London",
            "activeFvgs": ["1.0820–1.0830"],
        }

    payload = build_from_canonical_state(state)
    atomic_write_json(out_path, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write UI debate.json (canonical-state aware).")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH, help="Path to canonical_state.json")
    parser.add_argument("--out", default=DEFAULT_DEBATE_PATH, help="Path to debate.json")
    args = parser.parse_args()

    write_debate(args.out, args.state)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
