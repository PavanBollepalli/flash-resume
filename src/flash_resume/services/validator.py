"""Validation and typographic length estimation services."""

from __future__ import annotations

from typing import Tuple


def estimate_typographic_width(text: str) -> float:
    """Estimate horizontal advance width of text for proportional fonts.

    Narrow letters (i, l, t, j, space, punctuation) count ~0.5 units.
    Wide letters (m, w, M, W, @, %, etc.) count ~1.3 units.
    Standard letters count ~1.0 unit.
    """
    narrow_chars = set("iltjI1 .,'-–—:;\"'()")
    wide_chars = set("mwMW@%#&QO")

    width = 0.0
    for ch in text:
        if ch in narrow_chars:
            width += 0.52
        elif ch in wide_chars:
            width += 1.35
        elif ch.isupper():
            width += 1.15
        else:
            width += 0.95
    return width


def validate_bullet_length(
    original_text: str,
    replacement_text: str,
    max_word_delta: int = 2,
    max_width_ratio: float = 1.12,
) -> Tuple[bool, int, float]:
    """Validate that replacement bullet doesn't cause line-wrap overflow.

    Returns:
        Tuple[bool, int, float]: (is_valid, word_delta, width_ratio)
    """
    orig_words = len(original_text.split())
    new_words = len(replacement_text.split())
    word_delta = new_words - orig_words

    orig_width = estimate_typographic_width(original_text)
    new_width = estimate_typographic_width(replacement_text)
    width_ratio = new_width / max(orig_width, 1.0)

    # Keep the replacement within the budget in either direction and prevent
    # large typographic width increases that can cause an extra line wrap.
    is_valid = (abs(word_delta) <= max_word_delta) and (width_ratio <= max_width_ratio)
    return is_valid, word_delta, width_ratio

