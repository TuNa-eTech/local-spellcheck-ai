"""Vietnamese text noise generator for spellcheck test benchmarks and quality evaluation.

Adapted from nom-vn text noise taxonomy:
Provides deterministic, reproducible typo mutations modeling real Vietnamese errors
(Telex slips, tone confusion clusters, adjacent QWERTY typos, missing spaces, OCR slips).
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass

__all__ = [
    "NoiseConfig",
    "NoiseGenerator",
    "heavy_noise",
    "light_noise",
]


@dataclass(frozen=True, slots=True)
class NoiseConfig:
    """Per-noise-type probabilities."""

    p_diacritic_strip: float = 0.0
    p_confusion: float = 0.0
    p_char_swap: float = 0.0
    p_char_delete: float = 0.0
    p_char_insert: float = 0.0
    p_telex_slip: float = 0.0
    p_segment: float = 0.0  # missing space / glued word
    p_keyboard: float = 0.0  # QWERTY-adjacent typo
    max_edit_ratio: float = 0.25


def light_noise() -> NoiseConfig:
    """Light realistic typos (1-2 typos per paragraph)."""
    return NoiseConfig(
        p_diacritic_strip=0.05,
        p_confusion=0.04,
        p_char_swap=0.01,
        p_char_delete=0.01,
        p_telex_slip=0.03,
        p_segment=0.02,
    )


def heavy_noise() -> NoiseConfig:
    """Heavy typos (stress testing)."""
    return NoiseConfig(
        p_diacritic_strip=0.12,
        p_confusion=0.08,
        p_char_swap=0.03,
        p_char_delete=0.03,
        p_char_insert=0.02,
        p_telex_slip=0.06,
        p_segment=0.05,
        p_keyboard=0.03,
    )


_CONFUSION_GROUPS: tuple[tuple[str, ...], ...] = (
    ("thuê", "thuế", "thuệ"),
    ("chữ", "chứ", "chử"),
    ("nhỉ", "nhì", "nhi"),
    ("giả", "giã", "giá", "gia"),
    ("Hùng", "Hưng", "Hứng"),
    ("Thanh", "Thánh", "Thành"),
    ("Lê", "Lễ", "Le"),
    ("nội", "nỗi", "nồi"),
    ("là", "lá", "lả", "lạ"),
    ("của", "cùa", "cũa"),
    ("không", "khong", "khống"),
    ("được", "đuoc", "duộc"),
    ("đã", "đa", "đả"),
    ("năm", "nắm", "nâm"),
    ("ngày", "ngay", "ngáy"),
    ("kỷ", "kỉ", "kí"),
    ("xử", "sử"),
    ("quy", "qui"),
    ("trình", "chình"),
    ("soát", "xoát"),
)

# QWERTY key adjacency for typing fat-finger mutations
_QWERTY_ADJACENT: dict[str, str] = {
    "a": "qwsz",
    "b": "vghn",
    "c": "xdfv",
    "d": "ersfxc",
    "e": "wsdr",
    "g": "tyfhvb",
    "h": "yugjbn",
    "i": "ujko",
    "k": "ijmlou",
    "l": "okp",
    "m": "njk",
    "n": "bhjm",
    "o": "iklp",
    "p": "ol",
    "q": "wa",
    "r": "edft",
    "s": "weadzx",
    "t": "rfgy",
    "u": "yhji",
    "v": "cfgb",
    "w": "qase",
    "x": "zsdc",
    "y": "tghu",
    "z": "asx",
}


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D")


class NoiseGenerator:
    """Generates deterministic synthetic errors for Vietnamese spellcheck benchmarks."""

    def __init__(self, config: NoiseConfig | None = None, seed: int = 42) -> None:
        self.cfg = config or light_noise()
        self.seed = seed
        self._rng = random.Random(seed)

    def noisify(self, text: str) -> str:
        """Apply noise mutations to text according to config probabilities."""
        if not text:
            return text

        tokens = text.split(" ")
        mutated_tokens: list[str] = []

        for token in tokens:
            if not token or len(token) <= 1:
                mutated_tokens.append(token)
                continue

            mutated = token

            # 1. Confusion cluster swap
            if self.cfg.p_confusion > 0 and self._rng.random() < self.cfg.p_confusion:
                for group in _CONFUSION_GROUPS:
                    lowered_group = [g.casefold() for g in group]
                    if mutated.casefold() in lowered_group:
                        choices = [g for g in group if g.casefold() != mutated.casefold()]
                        if choices:
                            replacement = self._rng.choice(choices)
                            if mutated[0].isupper():
                                replacement = replacement[0].upper() + replacement[1:]
                            mutated = replacement
                            break

            # 2. Diacritic stripping
            if self.cfg.p_diacritic_strip > 0 and self._rng.random() < self.cfg.p_diacritic_strip:
                mutated = _strip_diacritics(mutated)

            # 3. Char swap (adjacent letters)
            if self.cfg.p_char_swap > 0 and len(mutated) >= 3 and self._rng.random() < self.cfg.p_char_swap:
                idx = self._rng.randint(1, len(mutated) - 2)
                mutated = mutated[:idx] + mutated[idx + 1] + mutated[idx] + mutated[idx + 2 :]

            # 4. Char delete
            if self.cfg.p_char_delete > 0 and len(mutated) >= 3 and self._rng.random() < self.cfg.p_char_delete:
                idx = self._rng.randint(1, len(mutated) - 1)
                mutated = mutated[:idx] + mutated[idx + 1 :]

            # 5. Keyboard adjacent typo
            if self.cfg.p_keyboard > 0 and len(mutated) >= 2 and self._rng.random() < self.cfg.p_keyboard:
                idx = self._rng.randint(0, len(mutated) - 1)
                ch = mutated[idx].lower()
                adj = _QWERTY_ADJACENT.get(ch)
                if adj:
                    new_ch = self._rng.choice(adj)
                    if mutated[idx].isupper():
                        new_ch = new_ch.upper()
                    mutated = mutated[:idx] + new_ch + mutated[idx + 1 :]

            mutated_tokens.append(mutated)

        # 6. Segment noise (drop spaces -> glued words)
        if self.cfg.p_segment > 0 and len(mutated_tokens) >= 2:
            joined: list[str] = [mutated_tokens[0]]
            for t in mutated_tokens[1:]:
                if self._rng.random() < self.cfg.p_segment:
                    joined[-1] = joined[-1] + t
                else:
                    joined.append(t)
            return " ".join(joined)

        return " ".join(mutated_tokens)
