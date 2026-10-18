import re


def _clean(message):
    # Lowercase, drop apostrophes ("what's" -> "whats"), collapse spaces.
    text = message.lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", text).strip()


QUIT = {"quit", "exit", "bye"}

# Signs a child may be unsafe. Matching sends a fixed script and a disclosure to
# one human; the model never handles these messages.
SAFETY = [
    "hurting me",
    "hurts me",
    "hit me",
    "hits me",
    "not safe",
    "unsafe",
    "scared to go home",
    "want to die",
    "kill myself",
    "hurt myself",
    "touched me",
    "someone is hurting",
]

BYPASS = [
    "ignore your instructions",
    "ignore previous",
    "ignore all previous",
    "reveal your prompt",
    "system prompt",
    "pretend you are",
]

ANSWER_REQUEST = [
    "give me the answer",
    "tell me the answer",
    "just tell me",
    "whats the answer",
    "what is the answer",
    "solve it for me",
    "solve it",
    "do it for me",
    "answer please",
]

HINT_REQUEST = [
    "hint",
    "help",
    "im stuck",
    "stuck",
    "i dont understand",
    "dont understand",
    "dont get it",
    "confused",
]


def detect_intent(message):
    text = _clean(message)

    if not text:
        return "empty"

    if any(phrase in text for phrase in SAFETY):
        return "safety_concern"

    if text in QUIT:
        return "quit"

    if any(phrase in text for phrase in BYPASS):
        return "bypass_attempt"

    if any(phrase in text for phrase in ANSWER_REQUEST):
        return "answer_request"

    if any(phrase in text for phrase in HINT_REQUEST):
        return "hint_request"

    return "general_question"
