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
    _loaded: bool = False
    _syllables: frozenset[str]
    _compounds: frozenset[str]
    _rep_rules: list[tuple[str, str]]
    _tone_map: dict[str, str]
    _confusions: dict[str, tuple[str, str]]

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
        self._confusions = _load_confusions()
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

    @property
    def confusions(self) -> dict[str, tuple[str, str]]:
        self._ensure_loaded()
        return self._confusions

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

    def suggest_split(self, word: str) -> str | None:
        """Split a glued word such as "bổsung" or glued+misspelled "hướngdẩn" back into "bổ sung" / "hướng dẫn".

        Every split point is tried:
        1. Exact split: head and tail are valid syllables and "head tail" is in compounds.
        2. Glued + typo: tone swap or phonological corrections on head/tail form a valid compound.
        """
        self._ensure_loaded()
        normalized = _normalize(word)
        if len(normalized) < 2 or " " in normalized:
            return None

        # 1. Exact split
        for index in range(1, len(normalized)):
            head, tail = normalized[:index], normalized[index:]
            if head in self._syllables and tail in self._syllables and f"{head} {tail}" in self._compounds:
                if len(word) == len(normalized):
                    return f"{word[:index]} {word[index:]}"
                return f"{head} {tail}"

        # 2. Glued word with typos
        for index in range(1, len(normalized)):
            head, tail = normalized[:index], normalized[index:]

            # 2a. Tone swap on tail (e.g. "hướngdẩn" -> "hướng dẫn")
            ts_tail = _swap_tones(tail, self._tone_map)
            if (
                ts_tail != tail
                and head in self._syllables
                and ts_tail in self._syllables
                and f"{head} {ts_tail}" in self._compounds
            ):
                return f"{head} {ts_tail}"

            # 2b. Tone swap on head (e.g. "dểhiểu" -> "dễ hiểu")
            ts_head = _swap_tones(head, self._tone_map)
            if (
                ts_head != head
                and ts_head in self._syllables
                and tail in self._syllables
                and f"{ts_head} {tail}" in self._compounds
            ):
                return f"{ts_head} {tail}"

            # 2c. Tone swap on both (e.g. "sữachửa" -> "sửa chữa")
            if (
                ts_head != head
                and ts_tail != tail
                and ts_head in self._syllables
                and ts_tail in self._syllables
                and f"{ts_head} {ts_tail}" in self._compounds
            ):
                return f"{ts_head} {ts_tail}"

            # 2d. REP rules on tail (e.g. "bốchí" -> "bố trí", "đềsuất" -> "đề xuất", "bỏxót" -> "bỏ sót")
            for old, new in self._rep_rules:
                if old in tail:
                    cand_tail = tail.replace(old, new, 1)
                    if (
                        head in self._syllables
                        and cand_tail in self._syllables
                        and f"{head} {cand_tail}" in self._compounds
                    ):
                        return f"{head} {cand_tail}"

            # 2e. REP rules on head (e.g. "chedấu" -> "che giấu", "thịchấn" -> "thị trấn")
            for old, new in self._rep_rules:
                if old in head:
                    cand_head = head.replace(old, new, 1)
                    if (
                        cand_head in self._syllables
                        and tail in self._syllables
                        and f"{cand_head} {tail}" in self._compounds
                    ):
                        return f"{cand_head} {tail}"

        return None

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

    def find_compound_confusion(
        self, w1: str, w2: str
    ) -> tuple[str, str] | None:
        """Check whether two consecutive valid syllables form an invalid compound with a valid tone swap correction.

        Returns (suggested_compound, reason) or None.
        """
        self._ensure_loaded()
        n1 = _normalize(w1)
        n2 = _normalize(w2)
        if not (self.is_valid_syllable(n1) and self.is_valid_syllable(n2)):
            return None
        bigram = f"{n1} {n2}"
        if bigram in self.compounds or bigram in self.confusions:
            return None

        # 1. Tone swap on w2
        ts2 = _swap_tones(n2, self._tone_map)
        if ts2 != n2 and ts2 in self._syllables and f"{n1} {ts2}" in self._compounds:
            return f"{n1} {ts2}", f"Từ đúng chính tả là “{n1} {ts2}”."

        # 2. Tone swap on w1
        ts1 = _swap_tones(n1, self._tone_map)
        if ts1 != n1 and ts1 in self._syllables and f"{ts1} {n2}" in self._compounds:
            return f"{ts1} {n2}", f"Từ đúng chính tả là “{ts1} {n2}”."

        # 3. Tone swap on both (e.g. "sữa chửa" -> "sửa chữa")
        if (
            ts1 != n1
            and ts2 != n2
            and ts1 in self._syllables
            and ts2 in self._syllables
            and f"{ts1} {ts2}" in self._compounds
        ):
            return f"{ts1} {ts2}", f"Từ đúng chính tả là “{ts1} {ts2}”."

        return None


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


def _load_confusions() -> dict[str, tuple[str, str]]:
    path = _DATA_DIR / "confusions.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: (v[0], v[1]) for k, v in data.items() if isinstance(v, list) and len(v) >= 2}


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
    tone_data = data.get("tone_map", {})
    return {str(k): str(v) for k, v in tone_data.items()}


def _swap_tones(word: str, tone_map: dict[str, str]) -> str:
    """Swap hỏi↔ngã tones in a syllable."""
    return "".join(tone_map.get(c, c) for c in word)

