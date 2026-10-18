import re
from fractions import Fraction


def is_correct(message, accepted_answers):
    text = message.lower().replace("-", " ")
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s+", " ", text).strip()
    for answer in accepted_answers:
        if re.search(r"(?<![\w/])" + re.escape(answer) + r"(?![\w/])", text):
            return True
    return False


def looks_like_answer(message):
    """True if the message is mostly a number or fraction, e.g. '2', '2/5', 'is it 3/5?'."""
    text = re.sub(r"\s*/\s*", "/", message.lower())
    return bool(re.search(r"\d+(/\d+)?|\d*\.\d+", text)) and len(text.split()) <= 4


def find_mistake(message, common_mistakes):
    text = re.sub(r"\s*/\s*", "/", message.lower())
    for wrong, feedback in common_mistakes.items():
        if re.search(r"(?<![\w/])" + re.escape(wrong) + r"(?![\w/])", text):
            return feedback
    return None


def _parse_number(message):
    text = re.sub(r"\s*/\s*", "/", message.lower())
    match = re.search(r"\d*\.\d+|\d+(?:/\d+)?", text)
    if not match:
        return None, None
    raw = match.group(0)
    try:
        return raw, Fraction(raw)
    except (ValueError, ZeroDivisionError):
        return raw, None


def wrong_answer_feedback(message, addends, correct_value):
    """Explain why an answer is wrong without revealing the right one."""
    raw, value = _parse_number(message)
    if value is None:
        return "Hmm, I couldn't read that answer. Try writing it like 1/4."
    biggest = max(Fraction(a) for a in addends)
    if "/" not in raw and "." not in raw:
        return ("Good try! But we are adding two pieces that are each smaller than one whole, "
                "so the answer is a fraction too. Try writing it like _/_.")
    if value >= 1:
        return ("That's one whole or more! Picture a pizza: half of it plus a third of it "
                "doesn't fill the whole pizza. Try a smaller fraction.")
    if value <= biggest:
        return ("Hmm, " + str(biggest) + " is already bigger than or equal to your answer. "
                "If we add more to it, the answer should get bigger. Try again!")
    return ("You're in the right range! Check that both fractions have the same bottom "
            "number before you add the top numbers.")
