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


# -----------------------------------------------------------------------------
# Debate types
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
    tag: Optional[str] = None


@dataclass(frozen=True)
class AgentTurn:
    name: str
    role: str
    round: int
    stance: str  # LONG | SHORT | NO_TRADE
    confidence: float
    claim: str
    evidence: List[Dict[str, Any]]
    counterpoints: List[str]
    changedMind: bool = False
    rebuttals: List[str] = None
    veto: bool = False
    risk_sizing_hint: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "agent": {"name": self.name, "role": self.role},
            "round": self.round,
            "stance": self.stance,
            "confidence": float(self.confidence),
            "claim": self.claim,
            "evidence": self.evidence,
            "counterpoints": _as_list(self.counterpoints),
            "changedMind": bool(self.changedMind),
            "rebuttals": _as_list(self.rebuttals),
            "veto": bool(self.veto),
            "riskSizingHint": self.risk_sizing_hint,
        }


# -----------------------------------------------------------------------------
# Minimal State Pack builder (from canonical_state.json if present)
# -----------------------------------------------------------------------------
def build_state_pack(state: Dict[str, Any]) -> Dict[str, Any]:
    # Accept either minimal canonical or your current stub format
    symbol = str(state.get("symbol", "UNKNOWN"))
    timeframe = str(state.get("timeframe", state.get("tf", "")))
    last_price = float(state.get("lastPrice", state.get("last_price", 0.0)) or 0.0)

    # Optional (may not exist yet)
    htf_bias = str(state.get("htfBias", state.get("htf_bias", "Unknown")))
    volatility = str(state.get("volatility", state.get("volatility_regime", "Normal")))
    session = str(state.get("session", "Unknown"))

    pdh = state.get("pdh", None)
    pdl = state.get("pdl", None)

    # Placeholder signal fields (wire real indicators later)
    signals = state.get("signals", {}) or {}
    rsi = signals.get("rsi", None)
    macd = signals.get("macd", None)

    vol = state.get("volatilityDetail", {}) or {}
    atr = vol.get("atr", None)
    atr_pct = vol.get("atr_pct", None)

    news = state.get("news", []) or []

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "now": str(state.get("asof", state.get("asofUtc", _now_iso_utc()))),
        "price": {"last": last_price},
        "structure": {
            "htf_bias": htf_bias,
            # These can be wired from canonical later; default False for now
            "mss": bool(state.get("mss", False)),
            "bos": bool(state.get("bos", False)),
        },
        "levels": {"pdh": pdh, "pdl": pdl},
        "volatility": {"atr": atr, "atr_pct": atr_pct, "label": volatility},
        "signals": {"rsi": rsi, "macd": macd},
        "news": news,
        "session": session,
        "activeFvgs": state.get("activeFvgs", state.get("active_fvgs", [])) or [],
    }


# -----------------------------------------------------------------------------
# Stub agents (deterministic) — replace with LLM later
# -----------------------------------------------------------------------------
def _as_num(x: Any) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:
        return None


def technical_analyst(state_pack: Dict[str, Any], rnd: int, prior: List[AgentTurn]) -> AgentTurn:
    lp = _as_num(state_pack["price"]["last"]) or 0.0
    htf = str(state_pack["structure"]["htf_bias"])
    pdh = _as_num(state_pack["levels"].get("pdh"))
    pdl = _as_num(state_pack["levels"].get("pdl"))

    # Simple bias: follow HTF if it’s explicit
    htf_l = htf.lower()
    stance = "NO_TRADE"
    conf = 0.52
    claim = "No clean technical edge yet."

    if htf_l in ("bullish", "up", "long"):
        stance, conf, claim = "LONG", 0.58, "HTF bias bullish; prefer longs after confirmation."
    elif htf_l in ("bearish", "down", "short"):
        stance, conf, claim = "SHORT", 0.58, "HTF bias bearish; prefer shorts after confirmation."

    evidence = [
        {"key": "htf_bias", "value": htf, "why": "Base directional context"},
        {"key": "pdh", "value": pdh, "why": "Overhead magnet/target"} if pdh is not None else {"key":"pdh","value":None,"why":"PDH not provided"},
        {"key": "pdl", "value": pdl, "why": "Below support/stop context"} if pdl is not None else {"key":"pdl","value":None,"why":"PDL not provided"},
    ]
    counter = ["Invalidated if HTF bias flips or price fails to confirm structure."]

    # Round 2 rebuttal: if PA agent says "no BOS", reduce confidence
    changed = False
    rebuttals: List[str] = []
    if rnd == 2 and prior:
        pa_turn = next((t for t in prior if t.role == "Price Action Analyst"), None)
        if pa_turn and pa_turn.stance == "NO_TRADE":
            conf2 = max(0.45, conf - 0.06)
            if conf2 != conf:
                changed = True
                rebuttals.append("Reducing confidence until MSS/BOS confirms.")
                conf = conf2

    return AgentTurn(
        name="Atlas",
        role="Technical Analyst",
        round=rnd,
        stance=stance,
        confidence=conf,
        claim=claim,
        evidence=evidence,
        counterpoints=counter,
        changedMind=changed,
        rebuttals=rebuttals,
    )


def price_action_analyst(state_pack: Dict[str, Any], rnd: int, prior: List[AgentTurn]) -> AgentTurn:
    mss = bool(state_pack["structure"].get("mss", False))
    bos = bool(state_pack["structure"].get("bos", False))
    stance = "NO_TRADE"
    conf = 0.66
    claim = "Wait for MSS→BOS confirmation before taking risk."

    if mss and bos:
        stance = "LONG" if str(state_pack["structure"]["htf_bias"]).lower() in ("bullish","up","long") else "SHORT"
        conf = 0.62
        claim = "Structure confirmed (MSS→BOS). Entry allowed with defined risk."

    evidence = [
        {"key": "mss", "value": mss, "why": "Market Structure Shift gate"},
        {"key": "bos", "value": bos, "why": "Break of Structure confirmation"},
    ]
    counter = ["If MSS/BOS are false, entries are low quality / range prone."]

    changed = False
    rebuttals: List[str] = []
    if rnd == 2 and prior:
        tech_turn = next((t for t in prior if t.role == "Technical Analyst"), None)
        if tech_turn and tech_turn.stance in ("LONG","SHORT") and (not (mss and bos)):
            rebuttals.append("Counter: HTF bias alone is insufficient without displacement + BOS.")
    return AgentTurn(
        name="Vela",
        role="Price Action Analyst",
        round=rnd,
        stance=stance,
        confidence=conf,
        claim=claim,
        evidence=evidence,
        counterpoints=counter,
        changedMind=changed,
        rebuttals=rebuttals,
    )


def risk_manager(state_pack: Dict[str, Any], rnd: int, prior: List[AgentTurn]) -> AgentTurn:
    vol_label = str(state_pack["volatility"].get("label", "Normal"))
    atr = _as_num(state_pack["volatility"].get("atr"))
    atr_pct = _as_num(state_pack["volatility"].get("atr_pct"))
    session = str(state_pack.get("session", "Unknown"))

    # Deterministic sizing hint
    size_hint = "Max 1.0% risk"  # default
    if vol_label.lower() in ("high", "elevated"):
        size_hint = "Max 0.5% risk (high volatility)"

    veto = False
    claim = "Risk gate check: allow only if constraints pass."
    conf = 0.80
    stance = "NO_TRADE"

    # If other agents are split, prefer NO_TRADE
    if prior:
        stances = [t.stance for t in prior if t.round == 1]
        if "LONG" in stances and "SHORT" in stances:
            stance = "NO_TRADE"
            claim = "Disagreement present; default to NO_TRADE."
        elif "LONG" in stances and "SHORT" not in stances:
            stance = "LONG"
        elif "SHORT" in stances and "LONG" not in stances:
            stance = "SHORT"

    # Hard veto example: missing last price or absurd ATR
    lp = _as_num(state_pack["price"]["last"])
    if lp is None or lp <= 0:
        veto = True
        stance = "NO_TRADE"
        claim = "VETO: missing/invalid price."
    if atr is not None and atr <= 0:
        veto = True
        stance = "NO_TRADE"
        claim = "VETO: invalid ATR."

    evidence = [
        {"key": "volatility", "value": vol_label, "why": "Position sizing scales with regime"},
        {"key": "atr", "value": atr, "why": "Stop distance / noise floor"} if atr is not None else {"key":"atr","value":None,"why":"ATR not provided"},
        {"key": "session", "value": session, "why": "Session risk rules (later)"},
    ]
    counter = ["Never debate around risk limits: if a gate fails, we stand down."]

    rebuttals: List[str] = []
    if rnd == 2 and prior:
        # If PA says no structure, recommend stand down even if tech wants trade
        pa = next((t for t in prior if t.role == "Price Action Analyst"), None)
        if pa and pa.stance == "NO_TRADE":
            rebuttals.append("PA gate not satisfied → reduce exposure / prefer NO_TRADE.")

    return AgentTurn(
        name="Nyx",
        role="Risk Manager",
        round=rnd,
        stance=stance,
        confidence=conf,
        claim=claim,
        evidence=evidence,
        counterpoints=counter,
        changedMind=False,
        rebuttals=rebuttals,
        veto=veto,
        risk_sizing_hint=size_hint,
    )


def chief_of_staff(state_pack: Dict[str, Any], rnd: int, prior: List[AgentTurn]) -> AgentTurn:
    htf = str(state_pack["structure"]["htf_bias"])
    vol_label = str(state_pack["volatility"].get("label", "Normal"))
    stance = "NO_TRADE"
    conf = 0.62
    claim = "No-trade until structure aligns with HTF bias."

    evidence = [
        {"key": "htf_bias", "value": htf, "why": "Direction context"},
        {"key": "volatility", "value": vol_label, "why": "Avoid forcing entries in chop"},
    ]
    counter = ["If BOS + retest aligns with HTF, stance may shift to trade."]

    rebuttals: List[str] = []
    changed = False
    if rnd == 2 and prior:
        # If both Tech and PA agree on same direction, Magnus reduces caution
        tech = next((t for t in prior if t.role == "Technical Analyst"), None)
        pa = next((t for t in prior if t.role == "Price Action Analyst"), None)
        if tech and pa and tech.stance == pa.stance and tech.stance in ("LONG","SHORT"):
            stance = tech.stance
            conf = 0.58
            claim = "Consensus emerging; allow trade if risk gates pass."
            changed = True
            rebuttals.append("Upgrading from NO_TRADE due to cross-agent alignment.")

    return AgentTurn(
        name="Magnus",
        role="Chief of Staff",
        round=rnd,
        stance=stance,
        confidence=conf,
        claim=claim,
        evidence=evidence,
        counterpoints=counter,
        changedMind=changed,
        rebuttals=rebuttals,
    )


AGENTS = [chief_of_staff, technical_analyst, price_action_analyst, risk_manager]


# -----------------------------------------------------------------------------
# Debate protocol: Round 1, Round 2, Round 3 (arbiter)
# -----------------------------------------------------------------------------
def run_round(state_pack: Dict[str, Any], rnd: int, prior_round: List[AgentTurn]) -> List[AgentTurn]:
    turns: List[AgentTurn] = []
    for fn in AGENTS:
        turns.append(fn(state_pack, rnd, prior_round))
    # stable ordering
    turns.sort(key=lambda t: (t.name.lower(), t.role.lower()))
    return turns


def build_vote(round2: List[AgentTurn]) -> Dict[str, Any]:
    # Weighted tally (simple). Risk Manager has slightly higher weight.
    weights = {}
    tally = {"LONG": 0.0, "SHORT": 0.0, "NO_TRADE": 0.0}
    for t in round2:
        w = 1.2 if t.role == "Risk Manager" else 1.0
        weights[t.name] = w
        if t.stance in tally:
            tally[t.stance] += w * float(t.confidence)
    winner = max(tally.items(), key=lambda kv: kv[1])[0]
    return {"weights": weights, "tally": tally, "winner": winner}


def extract_disagreements(round1: List[AgentTurn]) -> List[Dict[str, Any]]:
    stances = {t.name: t.stance for t in round1}
    longs = [n for n,s in stances.items() if s == "LONG"]
    shorts = [n for n,s in stances.items() if s == "SHORT"]
    notrades = [n for n,s in stances.items() if s == "NO_TRADE"]

    out: List[Dict[str, Any]] = []
    if longs and shorts:
        out.append({"topic": "direction", "for": longs, "against": shorts, "impact": "blocks_entries"})
    if notrades and (longs or shorts):
        out.append({"topic": "structure_confirmed", "for": (longs or shorts), "against": notrades, "impact": "requires_confirmation"})
    return out


def risk_gates(state_pack: Dict[str, Any], round2: List[AgentTurn]) -> Tuple[List[Dict[str, Any]], bool, str]:
    gates: List[Dict[str, Any]] = []

    # Gate: price valid
    lp = _as_num(state_pack["price"]["last"])
    gates.append({"gate": "price_present", "pass": (lp is not None and lp > 0), "details": "last price must be present"})
    ok = (lp is not None and lp > 0)

    # Gate: Risk veto
    veto = any(t.veto for t in round2 if t.role == "Risk Manager")
    gates.append({"gate": "risk_veto", "pass": (not veto), "details": "Risk Manager veto blocks trading"})
    if veto:
        ok = False

    # Gate: structure (if not present, we can still NO_TRADE)
    mss = bool(state_pack["structure"].get("mss", False))
    bos = bool(state_pack["structure"].get("bos", False))
    gates.append({"gate": "structure_confirmed", "pass": (mss and bos), "details": "MSS and BOS must be true for entries"})

    return gates, ok, ("Risk veto" if veto else "")


def arbiter(state_pack: Dict[str, Any], round1: List[AgentTurn], round2: List[AgentTurn]) -> Dict[str, Any]:
    gates, ok, veto_reason = risk_gates(state_pack, round2)
    vote = build_vote(round2)

    # Default winner from vote, but enforce:
    # - if gates fail OR structure gate not met → NO_TRADE
    structure_pass = next((g["pass"] for g in gates if g["gate"] == "structure_confirmed"), False)
    action = vote["winner"]

    if not ok:
        action = "NO_TRADE"
    if action in ("LONG","SHORT") and not structure_pass:
        action = "NO_TRADE"

    # Require consensus threshold for trading
    tally = vote["tally"]
    best = float(tally.get(vote["winner"], 0.0))
    threshold = 0.65
    if action in ("LONG","SHORT") and best < threshold:
        action = "NO_TRADE"

    reason_bits = []
    if veto_reason:
        reason_bits.append(veto_reason)
    if action == "NO_TRADE" and not structure_pass:
        reason_bits.append("Await MSS/BOS confirmation.")
    if action == "NO_TRADE" and best < threshold:
        reason_bits.append("Consensus threshold not met.")
    if not reason_bits:
        reason_bits.append("Consensus achieved with risk gates passing.")

    next_check = "Next 1–2 candles"
    rule = "Let the market come to you."

    return {
        "decision": {
            "action": action,
            "reason": " ".join(reason_bits).strip(),
            "nextCheck": next_check,
            "rule": rule,
        },
        "protocol": {
            "rounds": [
                {"round": 1, "label": "Independent", "turns": [t.to_json() for t in round1]},
                {"round": 2, "label": "Rebuttal", "turns": [t.to_json() for t in round2]},
                {"round": 3, "label": "Vote", "vote": vote},
            ],
            "disagreements": extract_disagreements(round1),
            "riskGates": gates,
        },
    }


# -----------------------------------------------------------------------------
# Build final debate.json for UI
# -----------------------------------------------------------------------------
def build_debate_json(state_pack: Dict[str, Any], protocol_blob: Dict[str, Any]) -> Dict[str, Any]:
    ctx = {
        "symbol": state_pack["symbol"],
        "timeframe": state_pack["timeframe"],
        "lastPrice": state_pack["price"]["last"],
        "session": state_pack.get("session", "Unknown"),
        "htfBias": state_pack["structure"]["htf_bias"],
        "volatility": state_pack["volatility"].get("label", "Normal"),
        "pdh": state_pack["levels"].get("pdh"),
        "pdl": state_pack["levels"].get("pdl"),
        "activeFvgs": state_pack.get("activeFvgs", []),
    }
    state_hash = _stable_hash(ctx)

    # Top-level "agents" should reflect latest stances (Round 2)
    round2_turns = protocol_blob["protocol"]["rounds"][1]["turns"]
    agents_flat = []
    for t in round2_turns:
        agents_flat.append({
            "name": t["agent"]["name"],
            "role": t["agent"]["role"],
            "stance": t["stance"],
            "summary": t["claim"],
            "paragraphs": [],  # Keep clean; UI can read from protocol turns
            "reasoning": [e.get("why","") for e in (t.get("evidence") or []) if e.get("why")],
            "confidence": float(t.get("confidence", 0.0)),
            "tag": None,
        })
    agents_flat.sort(key=lambda a: (a["name"] or "").lower())

    out = {
        "meta": {
            "schemaVersion": "1.1",
            "title": f"Trade Senate · {ctx['symbol']} {ctx['timeframe']}",
            "generatedAt": _now_iso_utc(),
            "stateHash": state_hash,
            "sequence": int(time.time()),
        },
        "context": ctx,
        "agents": agents_flat,
        "decision": protocol_blob["decision"],
        "protocol": protocol_blob["protocol"],
    }
    return out


def simulate_and_write(out_path: str, state_path: str) -> None:
    state = read_json(state_path)
    if state is None:
        # Minimal demo state pack if canonical not present yet
        state = {
            "schemaVersion": "1.0",
            "symbol": "BTCUSDT",
            "timeframe": "1H",
            "asof": _now_iso_utc(),
            "lastPrice": 48250.5,
            "pdh": 48500.0,
            "pdl": 47000.0,
            "htfBias": "Bullish",
            "volatility": "Normal",
            "session": "NY",
            "signals": {"rsi": 36.2, "macd": -12.1},
            "volatilityDetail": {"atr": 320.0, "atr_pct": 0.62},
            "news": [{"headline": "ETF inflows steady", "sentiment": "neutral"}],
        }

    sp = build_state_pack(state)
    r1 = run_round(sp, 1, [])
    r2 = run_round(sp, 2, r1)
    protocol_blob = arbiter(sp, r1, r2)
    payload = build_debate_json(sp, protocol_blob)
    atomic_write_json(out_path, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write UI debate.json using a 3-round debate protocol.")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH, help="Path to canonical_state.json")
    parser.add_argument("--out", default=DEFAULT_DEBATE_PATH, help="Path to debate.json")
    args = parser.parse_args()

    simulate_and_write(args.out, args.state)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
