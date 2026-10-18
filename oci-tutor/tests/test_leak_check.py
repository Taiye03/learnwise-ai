import pytest

from content_provider import PROBLEMS
from leak_check import leaks_answer, safe_hint

FORBIDDEN = PROBLEMS["frac-add-1"]["forbidden_answers"]


@pytest.mark.parametrize("text", [
    "The answer is 5/6.",
    "It's 5 / 6",
    "five sixths!",
    "Five-sixths",
    "That equals 10/12",
    "about 0.83333",
])
def test_leaks_are_caught(text):
    assert leaks_answer(text, FORBIDDEN)


@pytest.mark.parametrize("text", [
    "Find a common denominator first.",
    "For example, 1/4 + 1/2 = 3/4.",
    "Try rewriting both fractions in sixths.",
    "What is 15/60?",
])
def test_safe_text_passes(text):
    assert not leaks_answer(text, FORBIDDEN)


def test_safe_hint_falls_back():
    assert safe_hint("It's 5/6", "fallback", FORBIDDEN) == "fallback"
    assert safe_hint("", "fallback", FORBIDDEN) == "fallback"
    assert safe_hint("Think about sixths", "fallback", FORBIDDEN) == "Think about sixths"
