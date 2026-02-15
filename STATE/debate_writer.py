from __future__ import annotations

import argparse
import json
import os
import time
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------------------------------------------------------
# Pathing: anchor to repo root so relative runs can't "write somewhere else"
# -----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
STATE_DIR = os.path.join(ROOT, "STATE")
DEFAULT_DEBATE_PATH = os.path.join(STATE_DIR, "debate.json")
DEFAULT_STATE_PATH = os.path.join(STATE_DIR, "canonical_state.json")


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
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
    except json.JSONDecodeError:
        return None


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def validate_debate_payload(payload: Dict[str, Any]) -> List[str]:
    """
    Lightweight schema checks for debate.json.
    Returns a list of validation error messages (empty list means valid).
    """
    errors: List[str] = []

    def _expect_dict(key: str) -> Optional[Dict[str, Any]]:
        value = payload.get(key)
        if not isinstance(value, dict):
            errors.append(f"'{key}' must be an object")
            return None
        return value

    meta = _expect_dict("meta")
    context = _expect_dict("context")
    decision = _expect_dict("decision")
    scorecard = _expect_dict("scorecard")
    ensemble = _expect_dict("ensemble")
    protocol = _expect_dict("protocol")

    agents = payload.get("agents")
    if not isinstance(agents, list) or not agents:
        errors.append("'agents' must be a non-empty array")

    if meta:
        for k in ("schemaVersion", "generatedAt", "stateHash", "sequence"):
            if k not in meta:
                errors.append(f"'meta.{k}' is required")

    if context:
        for k in ("symbol", "timeframe", "htfBias", "volatility"):
            if k not in context:
                errors.append(f"'context.{k}' is required")

    if decision:
        for k in ("action", "reason", "rule"):
            if k not in decision:
                errors.append(f"'decision.{k}' is required")

    if scorecard and not isinstance(scorecard.get("criteria"), list):
        errors.append("'scorecard.criteria' must be an array")

    if ensemble:
        for k in ("result", "score", "threshold"):
            if k not in ensemble:
                errors.append(f"'ensemble.{k}' is required")

    if protocol:
        rounds = protocol.get("rounds")
        if not isinstance(rounds, list) or len(rounds) < 3:
            errors.append("'protocol.rounds' must be an array with at least 3 rounds")

    return errors


# -----------------------------------------------------------------------------
# Core types
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentOpinion:
    name: str
    role: str
    stance: str  # LONG | SHORT | NO_TRADE
    summary: str
    paragraphs: List[str]
    reasoning: List[str]
    confidence: float


# -----------------------------------------------------------------------------
# Scorecard + Ensemble
# -----------------------------------------------------------------------------
SCORECARD_SPEC = [
    # key, label, agent, weight, min, max
    ("htf_context", "HTF Context", "Magnus", 1.2, 0, 2),
    ("execution_edge", "Execution Edge", "Vela", 1.0, 0, 3),
    ("confluence", "Confluence", "Atlas", 1.0, 0, 2),
    ("external_risk", "External Risk", "Nyx", 0.8, -3, 0),
    ("safety_gate", "Safety Gate", "Risk", 1.5, 0, 1),  # pass/fail (1=pass,0=fail)
]


def _agent_weights_default() -> Dict[str, float]:
    return {"Magnus": 1.2, "Atlas": 1.0, "Vela": 1.0, "Nyx": 0.8, "Risk": 1.5}


def build_scorecard(state: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Deterministic v1 rubric derived from canonical_state.json (when available).
    If keys are missing, we fallback to conservative defaults.
    """
    # Try to read structured hints if your canonical_state builder adds them later:
    htf_bias = str(state.get("htfBias", "Unknown")).lower()
    volatility = str(state.get("volatility", state.get("volatilityRegime", "Normal"))).lower()
    news_risk = str(state.get("newsRisk", "neutral")).lower()
    spread_pips = float(state.get("spreadPips", state.get("spread_pips", 0.0)) or 0.0)
    atr_pips = float(state.get("atrPips", state.get("atr_pips", 0.0)) or 0.0)

    # Placeholder structure flags (upgrade later from your state builder)
    mss = bool(state.get("mss", state.get("hasMSS", False)))
    bos = bool(state.get("bos", state.get("hasBOS", False)))

    # --- Category scoring (simple but deterministic) ---
    # HTF Context: bullish/bearish known -> 2, unknown -> 1
    htf_score = 2 if htf_bias in ("bullish", "bearish") else 1

    # Execution Edge: MSS + BOS = 3, one = 2, none = 0
    exec_score = 3 if (mss and bos) else (2 if (mss or bos) else 0)

    # Confluence: use activeFvgs count and proximity flags later. For now: if any FVGs -> 1, plus if PDH/PDL exist -> +1
    active_fvgs = state.get("activeFvgs", state.get("active_fvgs", [])) or []
    if not isinstance(active_fvgs, list):
        active_fvgs = [str(active_fvgs)]
    confluence_score = 0
    if len(active_fvgs) > 0:
        confluence_score += 1
    if state.get("pdh") is not None or state.get("pdl") is not None:
        confluence_score += 1
    confluence_score = int(clamp(confluence_score, 0, 2))

    # External Risk: red folder -> -3, orange -> -2, neutral -> -1, calm -> 0
    if news_risk in ("red", "high", "high_impact", "himpact"):
        ext_score = -3
    elif news_risk in ("orange", "medium", "med", "elevated"):
        ext_score = -2
    elif news_risk in ("neutral", "none", "low"):
        ext_score = -1
    else:
        ext_score = -1

    # Safety Gate: fail if spread too high relative to ATR or volatility is extreme
    # Rule of thumb: spread must be < 0.4 * ATR (if ATR known); else allow.
    gate_pass = True
    if atr_pips > 0 and spread_pips > 0:
        gate_pass = spread_pips <= 0.4 * atr_pips
    if "extreme" in volatility or "spike" in volatility:
        gate_pass = False

    rows = []
    total_weighted = 0.0
    total_possible = 0.0
    for key, label, agent, weight, mn, mx in SCORECARD_SPEC:
        if key == "htf_context":
            score = htf_score
            note = f"HTF bias: {state.get('htfBias','—')}"
        elif key == "execution_edge":
            score = exec_score
            note = f"MSS={mss} BOS={bos}"
        elif key == "confluence":
            score = confluence_score
            note = f"FVGs={len(active_fvgs)} PDH/PDL={'Y' if (state.get('pdh') is not None or state.get('pdl') is not None) else 'N'}"
        elif key == "external_risk":
            score = ext_score
            note = f"News risk: {news_risk}"
        elif key == "safety_gate":
            score = 1 if gate_pass else 0
            note = "PASS" if gate_pass else "FAIL"
        else:
            score = 0
            note = "—"

        rows.append({
            "key": key,
            "label": label,
            "agent": agent,
            "weight": weight,
            "score": int(score),
            "min": mn,
            "max": mx,
            "note": note,
        })

        # total possible: for negative ranges, treat "best" as max
        best = mx
        total_weighted += weight * float(score)
        total_possible += weight * float(best)

    summary = {
        "weightedTotal": round(total_weighted, 3),
        "weightedMax": round(total_possible, 3),
        "gate": {"passed": bool(gate_pass), "reason": ("Spread/volatility gate failed" if not gate_pass else "OK")},
    }
    return rows, summary


def direction_to_numeric(stance: str) -> int:
    s = str(stance or "NO_TRADE").upper()
    if s in ("LONG", "BUY"):
        return 1
    if s in ("SHORT", "SELL"):
        return -1
    return 0


def numeric_to_action(score: float, threshold: float) -> str:
    if abs(score) < threshold:
        return "NO_TRADE"
    return "LONG" if score > 0 else "SHORT"


def compute_ensemble(agents: List[AgentOpinion], weights: Dict[str, float], threshold: float, gate_passed: bool) -> Dict[str, Any]:
    votes = []
    score = 0.0
    for a in agents:
        w = float(weights.get(a.name, 1.0))
        v = direction_to_numeric(a.stance)
        contrib = w * float(a.confidence) * float(v)
        score += contrib
        votes.append({
            "name": a.name,
            "role": a.role,
            "stance": a.stance,
            "confidence": float(a.confidence),
            "weight": w,
            "contribution": round(contrib, 4),
        })

    # Apply gate: if failed, force NO_TRADE and clamp score toward 0 for display
    action = numeric_to_action(score, threshold)
    final_action = action if gate_passed else "NO_TRADE"
    final_score = score if gate_passed else (0.0 if abs(score) < 1e-9 else (0.25 * score))

    return {
        "method": "weighted_vote_v1",
        "threshold": float(threshold),
        "weights": weights,
        "agentVotes": votes,
        "score": round(final_score, 4),
        "rawScore": round(score, 4),
        "result": final_action,
        "gateApplied": (not gate_passed),
    }


# -----------------------------------------------------------------------------
# Protocol / Debate Rounds (deterministic transcript for now)
# -----------------------------------------------------------------------------
def build_protocol_rounds(state: Dict[str, Any], agents: List[AgentOpinion], scorecard_rows: List[Dict[str, Any]], ensemble: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produces 3-round transcript that the UI can render.
    R1: initial positions
    R2: cross-examination
    R3: final vote
    """
    # Round 1: one turn per agent (their summary + key evidence)
    r1_turns = []
    for a in agents:
        r1_turns.append({
            "speaker": a.name,
            "role": a.role,
            "stance": a.stance,
            "confidence": float(a.confidence),
            "claim": a.summary,
            "evidence": _as_list(a.reasoning),
        })

    # Round 2: deterministic challenges (Magnus cross-examines each, each responds)
    # We create a short "question" + "response" pair per non-Magnus agent.
    r2_turns = []
    # Pull external risk + safety gate notes for realistic pressure
    ext = next((r for r in scorecard_rows if r["key"] == "external_risk"), None)
    gate = next((r for r in scorecard_rows if r["key"] == "safety_gate"), None)
    ext_note = (ext["note"] if ext else "News risk: —")
    gate_note = (gate["note"] if gate else "Gate: —")

    for a in agents:
        if a.name.lower() == "magnus":
            continue
        question = f"{a.name}, you’re {a.stance}. How do you reconcile this with ({ext_note}) and safety ({gate_note})?"
        # Response: echo their strongest reasoning + acknowledge risk
        resp_bits = []
        if a.reasoning:
            resp_bits.append(a.reasoning[0])
        if a.stance == "LONG" and "bear" in str(state.get("htfBias","")).lower():
            resp_bits.append("HTF conflict acknowledged; downgrade until alignment.")
        if a.stance == "SHORT" and "bull" in str(state.get("htfBias","")).lower():
            resp_bits.append("HTF conflict acknowledged; downgrade until alignment.")
        resp_bits.append("If no clean confirmation, I will hold NO_TRADE.")
        response = " ".join(resp_bits)

        r2_turns.append({
            "speaker": "Magnus",
            "role": "Chief of Staff",
            "type": "question",
            "text": question,
        })
        r2_turns.append({
            "speaker": a.name,
            "role": a.role,
            "type": "response",
            "text": response,
        })

    # Round 3: vote snapshot
    r3 = {
        "vote": {
            "method": ensemble.get("method"),
            "threshold": ensemble.get("threshold"),
            "score": ensemble.get("score"),
            "rawScore": ensemble.get("rawScore"),
            "result": ensemble.get("result"),
            "gateApplied": ensemble.get("gateApplied"),
            "agentVotes": ensemble.get("agentVotes"),
        }
    }

    # Disagreements: list stance splits
    stance_groups: Dict[str, List[str]] = {}
    for a in agents:
        stance_groups.setdefault(a.stance, []).append(a.name)
    disagreements = []
    if len(stance_groups) > 1:
        disagreements.append({
            "type": "stance_split",
            "detail": {k: v for k, v in stance_groups.items()},
        })

    risk_gates = []
    risk_gates.append({
        "name": "Safety Gate",
        "status": "PASS" if (not ensemble.get("gateApplied")) else "FAIL",
        "reason": ("OK" if (not ensemble.get("gateApplied")) else "Gate forced NO_TRADE"),
    })

    return {
        "rounds": [
            {"id": "r1", "label": "Round 1 · Initial Bias", "turns": r1_turns},
            {"id": "r2", "label": "Round 2 · Cross-Examination", "turns": r2_turns},
            {"id": "r3", "label": "Final Vote", "turns": [], "final": r3},
        ],
        "disagreements": disagreements,
        "riskGates": risk_gates,
    }


# -----------------------------------------------------------------------------
# Deterministic "stub agents" driven by canonical_state.json (no LLM yet)
# -----------------------------------------------------------------------------
def stub_agents_from_state(state: Dict[str, Any]) -> List[AgentOpinion]:
    symbol = str(state.get("symbol", "EURUSD"))
    tf = str(state.get("timeframe", "M15"))
    htf = str(state.get("htfBias", "Unknown"))
    vol = str(state.get("volatility", state.get("volatilityRegime", "Normal")))

    # Basic opinions (placeholders until you wire real state fields)
    agents = [
        AgentOpinion(
            name="Magnus",
            role="Chief of Staff",
            stance="NO_TRADE",
            summary="No-trade until structure aligns with HTF bias.",
            paragraphs=[
                "Conflicting signals. Require a clean MSS→BOS and retest before committing risk.",
            ],
            reasoning=[
                f"HTF bias: {htf}.",
                f"Volatility: {vol} → avoid forcing entries.",
            ],
            confidence=0.62,
        ),
        AgentOpinion(
            name="Atlas",
            role="Technical Analyst",
            stance="LONG" if str(htf).lower().startswith("bull") else "NO_TRADE",
            summary="Ascending support holding; higher lows.",
            paragraphs=[
                "Price respecting a rising trendline. Risk can be defined below last swing low.",
            ],
            reasoning=[
                "Two-touch trendline with clean reaction.",
                "PDH/HTF liquidity overhead may act as magnet / take-profit area.",
            ],
            confidence=0.58,
        ),
        AgentOpinion(
            name="Vela",
            role="Price Action Analyst",
            stance="NO_TRADE",
            summary="Needs MSS→BOS confirmation.",
            paragraphs=[
                "Market is still overlapping; wait for displacement + BOS.",
            ],
            reasoning=[
                "No clean MSS yet; pivots overlap → range conditions.",
            ],
            confidence=0.66,
        ),
        AgentOpinion(
            name="Nyx",
            role="News/Sentiment",
            stance="NO_TRADE",
            summary="No catalyst; headlines neutral.",
            paragraphs=[
                "No high-impact event expected in the next hour.",
            ],
            reasoning=[
                "Neutral sentiment → chop risk elevated.",
            ],
            confidence=0.55,
        ),
        AgentOpinion(
            name="Risk",
            role="Risk Manager",
            stance="NO_TRADE",
            summary="Gate check: spread/volatility must pass.",
            paragraphs=[
                "If safety gate fails, force NO_TRADE regardless of bias.",
            ],
            reasoning=[
                "Stop must exceed noise; do not trade into illiquid conditions.",
            ],
            confidence=0.80,
        ),
    ]

    # Stable ordering by name (UI doesn't jump)
    agents_sorted = sorted(agents, key=lambda a: (a.name or "").lower())
    return agents_sorted


# -----------------------------------------------------------------------------
# Debate builder (schema v1.2)
# -----------------------------------------------------------------------------
def build_debate_json_from_state(state: Dict[str, Any], sequence: int) -> Dict[str, Any]:
    # Context normalization
    ctx = {
        "symbol": state.get("symbol", "EURUSD"),
        "timeframe": state.get("timeframe", "M15"),
        "lastPrice": state.get("lastPrice", state.get("last_price", 0.0)),
        "session": state.get("session", "—"),
        "htfBias": state.get("htfBias", "Unknown"),
        "volatility": state.get("volatility", state.get("volatilityRegime", "Normal")),
        "pdh": state.get("pdh"),
        "pdl": state.get("pdl"),
        "activeFvgs": state.get("activeFvgs", state.get("active_fvgs", [])) or [],
    }
    state_hash = _stable_hash(ctx)

    # Agents
    agents = stub_agents_from_state(state)

    # Scorecard + Gate
    scorecard_rows, scorecard_summary = build_scorecard({**state, **ctx})

    # Ensemble
    weights = _agent_weights_default()
    threshold = float(state.get("ensembleThreshold", 0.35))
    gate_passed = bool(scorecard_summary.get("gate", {}).get("passed", True))
    ensemble = compute_ensemble(agents, weights, threshold, gate_passed)

    # Decision: driven by ensemble + gate
    decision_action = ensemble.get("result", "NO_TRADE")
    decision_reason = "Weighted vote + scorecard confluence."
    if not gate_passed:
        decision_reason = "Safety gate failed → forced NO_TRADE."
    decision_next_check = "Next 1–2 candles"
    decision_rule = "Let the market come to you."

    # Protocol
    protocol = build_protocol_rounds({**state, **ctx}, agents, scorecard_rows, ensemble)

    return {
        "meta": {
            "schemaVersion": "1.2",
            "title": f"Trade Senate · {ctx['symbol']} {ctx['timeframe']}",
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
            for a in agents
        ],
        "decision": {
            "action": decision_action,
            "reason": decision_reason,
            "nextCheck": decision_next_check,
            "rule": decision_rule,
        },
        "scorecard": {
            "criteria": scorecard_rows,
            "summary": scorecard_summary,
        },
        "ensemble": ensemble,
        "protocol": protocol,
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Write STATE/debate.json for the UI (stub v1.2).")
    ap.add_argument("--state", default=DEFAULT_STATE_PATH, help="Path to canonical_state.json")
    ap.add_argument("--out", default=DEFAULT_DEBATE_PATH, help="Output path for debate.json")
    ap.add_argument("--sequence", type=int, default=None, help="Optional deterministic sequence id")
    ap.add_argument("--validate", action="store_true", help="Validate payload shape before writing")
    args = ap.parse_args()

    # Load canonical_state.json if present, else fallback minimal
    state = read_json(args.state) or {}
    # If the state file is your python source by mistake, warn silently by falling back.
    if not isinstance(state, dict):
        state = {}

    sequence = int(args.sequence) if args.sequence is not None else int(time.time())
    payload = build_debate_json_from_state(state, sequence=sequence)

    if args.validate:
        errors = validate_debate_payload(payload)
        if errors:
            print("Validation failed:")
            for err in errors:
                print(f"- {err}")
            return 2

    atomic_write_json(args.out, payload)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
