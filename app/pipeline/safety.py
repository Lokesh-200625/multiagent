"""Stage 0 - safety gate. Regex over data file; never depends on the LLM."""
from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = PROJECT_ROOT / "data" / "emergency" / "emergency_policy.json"

_COMPILED: list[re.Pattern[str]] | None = None
_REPLY: str | None = None


def _load() -> None:
    global _COMPILED, _REPLY

    if _COMPILED is not None:
        return

    if not POLICY_FILE.exists():
        raise RuntimeError(f"Emergency policy file missing: {POLICY_FILE}")

    data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))

    patterns = data.get("patterns", [])
    if not isinstance(patterns, list) or not patterns:
        raise RuntimeError("Emergency policy has no patterns")

    _COMPILED = [re.compile(p, re.IGNORECASE) for p in patterns]
    _REPLY = str(
        data.get("reply")
        or "This may be an emergency. Contact your local emergency services immediately (112 in India)."
    )


def detect(message: str) -> bool:
    if not message:
        return False

    _load()
    assert _COMPILED is not None
    return any(pattern.search(message) for pattern in _COMPILED)


def response() -> dict:
    _load()
    assert _REPLY is not None
    return {"status": "EMERGENCY", "reply": _REPLY, "clarification": None}
