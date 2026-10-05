import re


EMERGENCY_PATTERNS = [
    # English
    r"\bambulance\b",
    r"\bemergency\b",
    r"\bmedical emergency\b",
    r"\bcall police\b",
    r"\bpolice emergency\b",
    r"\bfire emergency\b",
    r"\bfire\b",
    r"\baccident\b",
    r"\bmajor accident\b",
    r"\bserious accident\b",
    r"\bsevere bleeding\b",
    r"\bheavy bleeding\b",
    r"\bunconscious\b",
    r"\bnot breathing\b",
    r"\bcan't breathe\b",
    r"\bcannot breathe\b",
    r"\bchest pain\b",
    r"\bheart attack\b",
    r"\bstroke\b",
    r"\bpoisoned\b",
    r"\bpoisoning\b",
    r"\btrapped\b",
    r"\bmissing person\b",
    r"\bdanger\b",
    r"\bimmediate danger\b",
    r"\bsuicide\b",
    r"\bself harm\b",
    r"\bself-harm\b",

    # Telugu
    r"అత్యవసర",
    r"అంబులెన్స్",
    r"ప్రమాదం",
    r"రక్తస్రావం",
    r"స్పృహలో లేరు",
    r"ఊపిరి తీసుకోలేక",
    r"గుండె నొప్పి",
    r"గుండెపోటు",
    r"మంటలు",
    r"పోలీసులు కావాలి",
]


COMPILED_PATTERNS = [
    re.compile(
        pattern,
        re.IGNORECASE,
    )
    for pattern in EMERGENCY_PATTERNS
]


def detect_emergency(
    message: str,
) -> bool:
    if not message:
        return False

    text = message.strip()

    return any(
        pattern.search(text)
        for pattern in COMPILED_PATTERNS
    )


def emergency_response() -> dict:
    return {
        "status": "EMERGENCY",
        "reply": (
            "This may be an emergency. "
            "Contact your local emergency service immediately "
            "or ask someone nearby for urgent help. "
            "If you are in immediate danger, prioritize getting "
            "to a safe place and contacting emergency responders."
        ),
        "clarification": None,
    }