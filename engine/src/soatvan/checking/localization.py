from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

WORD_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_DELETION_REASON_CODES = frozenset({"punctuation", "repetition", "spacing", "technical"})
_WORD_LEVEL_REASON_CODES = frozenset(
    {"spelling", "capitalization", "word_choice"}
)
_ORTHOGRAPHIC_REASON_CODES = frozenset(
    {"spelling", "compound_word", "capitalization"}
)
_MAX_LOCALIZED_SOURCE_LENGTH = 32
_MAX_LOCALIZED_SOURCE_WORDS = 4


def localize_llm_edit(
    source_text: str, suggestion: str, reason_code: str
) -> tuple[int, str, str] | None:
    """Validate an LLM replacement and return its safest source-relative edit.

    Small, already-targeted replacements are preserved so comments remain easy
    to understand. Broad replacements are accepted only when the model supplied
    a corrected version with enough unchanged context at the boundaries. In
    that case the unchanged context is removed and only the compact changed
    region is returned.
    """
    if not source_text or _normalized(source_text) == _normalized(suggestion):
        return None

    broad_replacement = _is_broad_replacement(source_text, suggestion)
    prefix_length = _common_prefix_length(source_text, suggestion)
    suffix_length = _common_suffix_length(
        source_text[prefix_length:], suggestion[prefix_length:]
    )
    shared_context = (
        source_text[:prefix_length]
        + (source_text[len(source_text) - suffix_length :] if suffix_length else "")
    )
    # A punctuation mark or a space shared by chance is not sufficient proof
    # that the suggestion is a corrected copy of this long source span.
    has_shared_context = sum(character.isalnum() for character in shared_context) >= 2
    word_level_edit = reason_code in _WORD_LEVEL_REASON_CODES or (
        reason_code == "compound_word"
        and _whitespace_signature(source_text) == _whitespace_signature(suggestion)
    )
    should_localize = broad_replacement or (word_level_edit and has_shared_context)
    if not should_localize:
        return _validate_localized_edit(0, source_text, suggestion, reason_code)
    if not has_shared_context:
        return None

    source_start = prefix_length
    suggestion_start = prefix_length
    source_end = len(source_text) - suffix_length if suffix_length else len(source_text)
    suggestion_end = len(suggestion) - suffix_length if suffix_length else len(suggestion)
    minimal_source = source_text[source_start:source_end]
    minimal_suggestion = suggestion[suggestion_start:suggestion_end]
    if _contains_separated_edits(minimal_source, minimal_suggestion):
        if broad_replacement:
            return None
        return _validate_localized_edit(0, source_text, suggestion, reason_code)
    if word_level_edit:
        source_start, source_end = _word_boundaries(source_text, source_start, source_end)
        suggestion_start, suggestion_end = _word_boundaries(
            suggestion, suggestion_start, suggestion_end
        )
    localized_source = source_text[source_start:source_end]
    localized_suggestion = suggestion[suggestion_start:suggestion_end]
    if not localized_source:
        # DOCX comments need a non-empty source anchor. If word-boundary
        # expansion could not provide one, guessing a neighbor is misleading.
        return None
    return _validate_localized_edit(
        source_start, localized_source, localized_suggestion, reason_code
    )


def canonicalize_llm_edit(
    source_text: str, suggestion: str, category: str, reason_code: str
) -> tuple[str, str]:
    """Derive high-confidence labels from the actual replacement shape."""
    normalized_source = _normalized(source_text)
    normalized_suggestion = _normalized(suggestion)
    if normalized_source.casefold() == normalized_suggestion.casefold():
        return "capitalization", "capitalization"
    if _collapse_whitespace(normalized_source).casefold() == _collapse_whitespace(
        normalized_suggestion
    ).casefold():
        return "technical", "spacing"
    if _remove_whitespace(normalized_source).casefold() == _remove_whitespace(
        normalized_suggestion
    ).casefold():
        return "compound_word", "compound_word"
    if _without_diacritics(normalized_source).casefold() == _without_diacritics(
        normalized_suggestion
    ).casefold():
        return "spelling", "diacritic"
    return category, reason_code


def _validate_localized_edit(
    offset: int, source_text: str, suggestion: str, reason_code: str
) -> tuple[int, str, str] | None:
    if (
        not source_text
        or len(source_text) > _MAX_LOCALIZED_SOURCE_LENGTH
        or len(WORD_PATTERN.findall(source_text)) > _MAX_LOCALIZED_SOURCE_WORDS
        or _normalized(source_text) == _normalized(suggestion)
    ):
        return None
    if not suggestion and (
        reason_code not in _DELETION_REASON_CODES or len(source_text) > 16
    ):
        return None
    if reason_code in _ORTHOGRAPHIC_REASON_CODES:
        distance_source = _normalized(source_text)
        distance_suggestion = _normalized(suggestion)
        if (
            _without_diacritics(distance_source).casefold()
            != _without_diacritics(distance_suggestion).casefold()
            and _edit_distance(distance_source, distance_suggestion) > 1
        ):
            # A spelling-like proposal such as "trể" -> "tệ" changes both
            # letters and diacritics. Without lexical evidence this is a
            # guessed rewrite, whether it arrived directly or was localized
            # from a broader sentence, so fail closed.
            return None
        # A spelling-like proposal must not silently change surrounding
        # whitespace or punctuation at the same time. Those mixed rewrites are
        # ambiguous (and can place a full stop in the middle of a paragraph),
        # so fail closed unless only the separators themselves changed.
        if (
            _separator_signature(distance_source)
            != _separator_signature(distance_suggestion)
            and _alphanumeric_text(distance_source).casefold()
            != _alphanumeric_text(distance_suggestion).casefold()
        ):
            return None
        if reason_code == "capitalization":
            distance_source = distance_source.casefold()
            distance_suggestion = distance_suggestion.casefold()
        maximum_distance = max(2, min(3, (max(len(source_text), len(suggestion)) + 6) // 7))
        if _edit_distance(distance_source, distance_suggestion) > maximum_distance:
            return None
    return offset, source_text, suggestion


def _is_broad_replacement(source_text: str, suggestion: str) -> bool:
    source_words = len(WORD_PATTERN.findall(source_text))
    suggestion_words = len(WORD_PATTERN.findall(suggestion))
    return (
        len(source_text) > _MAX_LOCALIZED_SOURCE_LENGTH
        or source_words > _MAX_LOCALIZED_SOURCE_WORDS
        or (
            len(source_text) > 24
            and len(source_text) > (2 * len(suggestion))
        )
        or (
            source_words >= 4
            and source_words >= suggestion_words + 2
            and len(source_text) >= len(suggestion) + 12
        )
    )


def _common_prefix_length(left: str, right: str) -> int:
    length = 0
    for left_character, right_character in zip(left, right, strict=False):
        if left_character != right_character:
            break
        length += 1
    return length


def _common_suffix_length(left: str, right: str) -> int:
    length = 0
    for left_character, right_character in zip(reversed(left), reversed(right), strict=False):
        if left_character != right_character:
            break
        length += 1
    return length


def _contains_separated_edits(source_text: str, suggestion: str) -> bool:
    """Detect meaningful unchanged context trapped between separate edits."""
    if not source_text or not suggestion:
        return False
    for match in SequenceMatcher(
        None, source_text, suggestion, autojunk=False
    ).get_matching_blocks():
        if not match.size:
            continue
        shared = source_text[match.a : match.a + match.size]
        if match.size >= 3 or any(character.isspace() for character in shared):
            return True
    return False


def _word_boundaries(value: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and _is_word_character(value[start - 1]):
        start -= 1
    while end < len(value) and _is_word_character(value[end]):
        end += 1
    return start, end


def _is_word_character(value: str) -> bool:
    return bool(value) and (value.isalnum() or unicodedata.category(value).startswith("M"))


def _whitespace_signature(value: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in re.finditer(r"\s+", value))


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _remove_whitespace(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _separator_signature(value: str) -> str:
    return "".join(character for character in value if not character.isalnum())


def _alphanumeric_text(value: str) -> str:
    return "".join(character for character in value if character.isalnum())


def _without_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return without_marks.replace("đ", "d").replace("Đ", "D")


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value)
