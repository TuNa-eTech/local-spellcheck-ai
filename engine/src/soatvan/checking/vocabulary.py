"""Vietnamese vocabulary lookup using bundled dictionary data files."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_WORD_PATTERN = re.compile(r"[A-Za-zÀ-ỹĐđ]+")
_VIETNAMESE_MARKERS = frozenset(
    "đăằắẳẵặâầấẩẫậêềếểễệôồốổỗộơờớởỡợưừứửữự"
    "àáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ"
)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip().casefold()


def _has_vietnamese_chars(word: str) -> bool:
    return any(c in _VIETNAMESE_MARKERS for c in word.casefold())


class VietnameseVocabulary:
    """Singleton-style Vietnamese vocabulary with syllable + compound lookup.

    Data is loaded lazily from bundled text files on first access.
    Lookups are O(1) using frozenset membership tests.
    """

    _instance: VietnameseVocabulary | None = None

    def __new__(cls) -> VietnameseVocabulary:
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._loaded = False
            cls._instance = inst
        return cls._instance

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._syllables = _load_syllables()
        self._compounds = _load_compounds()
        self._rep_rules = _load_rep_rules()
        self._tone_map = _load_tone_map()
        self._loaded = True

    @property
    def syllables(self) -> frozenset[str]:
        self._ensure_loaded()
        return self._syllables

    @property
    def compounds(self) -> frozenset[str]:
        self._ensure_loaded()
        return self._compounds

    @property
    def rep_rules(self) -> list[tuple[str, str]]:
        self._ensure_loaded()
        return self._rep_rules

    @property
    def tone_map(self) -> dict[str, str]:
        self._ensure_loaded()
        return self._tone_map

    def is_valid_syllable(self, syllable: str) -> bool:
        """Check if a syllable exists in the Vietnamese vocabulary."""
        return _normalize(syllable) in self.syllables

    def is_valid_compound(self, compound: str) -> bool:
        """Check if a compound word/phrase exists in the Vietnamese vocabulary."""
        return _normalize(compound) in self.compounds

    def suggest_corrections(self, syllable: str) -> list[str]:
        """Suggest corrections for an invalid syllable using REP rules + tone MAP.

        Returns a list of valid syllables that could replace the input,
        ordered by likelihood (tone swap first, then consonant substitutions).
        """
        self._ensure_loaded()
        normalized = _normalize(syllable)
        suggestions: list[str] = []
        seen: set[str] = set()

        # 1. Try tone swap (hỏi↔ngã) — most common Vietnamese typo
        tone_swapped = _swap_tones(normalized, self._tone_map)
        if tone_swapped != normalized and tone_swapped in self._syllables:
            suggestions.append(tone_swapped)
            seen.add(tone_swapped)

        # 2. Try REP rules (ch↔tr, s↔x, d↔gi, ng↔ngh, etc.)
        for old, new in self._rep_rules:
            if old in normalized:
                candidate = normalized.replace(old, new, 1)
                if candidate not in seen and candidate in self._syllables:
                    suggestions.append(candidate)
                    seen.add(candidate)

        return suggestions

    def suggest_for_compound(
        self, prev_word: str, wrong_word: str
    ) -> str | None:
        """Suggest the best correction considering bigram context.

        If a suggestion forms a valid compound with the preceding word,
        prefer that suggestion over others.
        """
        suggestions = self.suggest_corrections(wrong_word)
        if not suggestions:
            return None

        prev_norm = _normalize(prev_word)
        for s in suggestions:
            bigram = f"{prev_norm} {s}"
            if bigram in self.compounds:
                return s

        return suggestions[0]


def _load_syllables() -> frozenset[str]:
    path = _DATA_DIR / "syllables.txt"
    if not path.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _load_compounds() -> frozenset[str]:
    path = _DATA_DIR / "compounds.txt"
    if not path.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _load_rep_rules() -> list[tuple[str, str]]:
    path = _DATA_DIR / "rep_rules.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(r["from"], r["to"]) for r in data.get("replacements", [])]


def _load_tone_map() -> dict[str, str]:
    path = _DATA_DIR / "rep_rules.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("tone_map", {})


def _swap_tones(word: str, tone_map: dict[str, str]) -> str:
    """Swap hỏi↔ngã tones in a syllable."""
    return "".join(tone_map.get(c, c) for c in word)
