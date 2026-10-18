import re


def _normalize(text):
    text = text.lower().replace("-", " ")
    text = re.sub(r"\s*/\s*", "/", text)      # "5 / 6" -> "5/6"
    return re.sub(r"\s+", " ", text).strip()


def leaks_answer(text, forbidden_answers):
    """True if the text contains any forbidden answer form.

    Demo guardrail only: it can miss paraphrases or worked steps.
    """
    normalized = _normalize(text)
    for answer in forbidden_answers:
        form = _normalize(answer)
        # Decimals are prefixes: "0.83" should also catch "0.83333".
        end = "" if "." in form else r"(?![\w/])"
        pattern = r"(?<![\w.])" + re.escape(form) + end
        if re.search(pattern, normalized):
            return True
    return False


def safe_hint(candidate, fallback, forbidden_answers):
    if not candidate or leaks_answer(candidate, forbidden_answers):
        return fallback
    return candidate
