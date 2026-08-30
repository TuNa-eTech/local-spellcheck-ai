"""Heading-shaped input detection and tone-only edit protection.

Inspired by nom-vn text heading guards:
Protects document furniture (letterheads, all-caps titles, form labels, codes)
from destructive model rewrites while permitting tone-level spelling corrections.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["is_heading", "merge_tone_only"]

# Combining tone marks in NFD form: huyền (\u0300), sắc (\u0301), ngã (\u0303), hỏi (\u0309), nặng (\u0323)
_TONE_MARKS = frozenset("\u0300\u0301\u0303\u0309\u0323")

# Token is purely alphabetic (no digits, hyphens, slashes)
_ALPHA_TOKEN = re.compile(r"^[^\W\d_]+$", re.UNICODE)

# Non-whitespace token pattern
_TOKEN_SPAN = re.compile(r"\S+")

_MAX_HEADING_TOKENS = 12
_MIN_CAPITALIZED_RATIO = 0.5


def _detone(word: str) -> str:
    """Strip tone marks only, preserving vowel base modifiers (â, ê, ô, ơ, ư, ă) and đ."""
    decomposed = unicodedata.normalize("NFD", word)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if c not in _TONE_MARKS))


def is_heading(text: str) -> bool:
    """Return True when text looks like a document heading, title, or letterhead.

    Heading-shaped means:
    1. Short (<= 12 tokens)
    2. Does not end with sentence-final punctuation (. ! ?)
    3. Either fully UPPERCASE or >= 50% TitleCased across its alphabetic tokens.
    """
    stripped = text.strip()
    if not stripped:
        return False
    tokens = stripped.split()
    if len(tokens) > _MAX_HEADING_TOKENS:
        return False
    if stripped.endswith((".", "!", "?")):
        return False
    alpha = [t for t in tokens if _ALPHA_TOKEN.match(t)]
    if not alpha:
        return False
    if all(t.isupper() for t in alpha):
        return True
    capitalized = sum(1 for t in alpha if t[:1].isupper())
    return (capitalized / len(alpha)) >= _MIN_CAPITALIZED_RATIO


def merge_tone_only(source: str, candidate: str) -> str:
    """Safely merge candidate corrections into a heading source text.

    Only accepts tone-level edits on purely alphabetic tokens, preserving
    all original uppercase styling, punctuation, and codes (e.g. 15/QĐ-UBND).
    """
    source_tokens = list(_TOKEN_SPAN.finditer(source))
    cand_tokens = list(_TOKEN_SPAN.finditer(candidate))
    if len(source_tokens) != len(cand_tokens):
        return source

    out_parts: list[str] = []
    last_end = 0

    for s_m, c_m in zip(source_tokens, cand_tokens, strict=False):
        out_parts.append(source[last_end : s_m.start()])
        s_tok = s_m.group(0)
        c_tok = c_m.group(0)

        if not _ALPHA_TOKEN.match(s_tok) or not _ALPHA_TOKEN.match(c_tok):
            out_parts.append(s_tok)
        elif _detone(s_tok).casefold() != _detone(c_tok).casefold():
            out_parts.append(s_tok)
        else:
            # Preserve original casing
            if s_tok.isupper():
                out_parts.append(c_tok.upper())
            elif s_tok[:1].isupper():
                out_parts.append(c_tok[:1].upper() + c_tok[1:])
            else:
                out_parts.append(c_tok.lower())

        last_end = s_m.end()

    out_parts.append(source[last_end:])
    return "".join(out_parts)
