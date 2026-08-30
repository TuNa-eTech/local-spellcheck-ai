from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .heading import is_heading, merge_tone_only

WORD_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_TOKEN_SPLIT = re.compile(r"(\s+)")
_DELETION_REASON_CODES = frozenset({"punctuation", "repetition", "spacing", "technical"})
_WORD_LEVEL_REASON_CODES = frozenset(
    {"spelling", "capitalization", "word_choice"}
)
_ORTHOGRAPHIC_REASON_CODES = frozenset(
    {"spelling", "compound_word", "capitalization"}
)
_MAX_LOCALIZED_SOURCE_LENGTH = 32
_MAX_LOCALIZED_SOURCE_WORDS = 4
_VIETNAMESE_VOWELS = frozenset("aăâeêioôơuưy")


def _syllable_onset_length(value: str) -> int:
    base = [_without_diacritics(character).casefold() for character in value]
    cut = len(base)
    for index, character in enumerate(base):
        if character in _VIETNAMESE_VOWELS:
            cut = index
            break
    # "gi" and "qu" are digraph onsets, so their vowel letter belongs to the
    # onset rather than the rime as long as a rime is left behind: giấu, quyết.
    if cut == 1 and len(base) > 2:
        digraph = (base[0], base[1])
        if digraph in {("g", "i"), ("q", "u")} and any(
            character in _VIETNAMESE_VOWELS for character in base[2:]
        ):
            cut = 2
    return cut


def _onset_only_substitution(source_text: str, suggestion: str) -> bool:
    """True when one syllable keeps its rime and only the leading consonant moves.

    The common Vietnamese spelling confusions are onset pairs — ch/tr, s/x,
    d/gi/r, n/l — and several of them cost two character edits, so a plain
    edit-distance limit of one rejects them. Requiring an identical rime keeps
    the check just as strict about everything else: a proposal that also alters
    the vowel or the tone is still treated as a guessed rewrite.
    """
    if any(character.isspace() for character in source_text + suggestion):
        return False
    source_cut = _syllable_onset_length(source_text)
    suggestion_cut = _syllable_onset_length(suggestion)
    if not source_cut or not suggestion_cut:
        return False
    rime = source_text[source_cut:]
    return (
        bool(rime)
        and rime == suggestion[suggestion_cut:]
        and source_text[:source_cut].casefold() != suggestion[:suggestion_cut].casefold()
    )


def _rime_only_substitution(source_text: str, suggestion: str) -> bool:
    """True when one syllable keeps its onset and only the rime/dialect vowel moves."""
    if any(character.isspace() for character in source_text + suggestion):
        return False
    source_cut = _syllable_onset_length(source_text)
    suggestion_cut = _syllable_onset_length(suggestion)
    if not source_cut and not suggestion_cut:
        return _edit_distance(source_text, suggestion) <= 2
    if not source_cut or not suggestion_cut:
        return False
    onset_source = source_text[:source_cut].casefold()
    onset_sug = suggestion[:suggestion_cut].casefold()
    if onset_source != onset_sug:
        return False
    rime_source = source_text[source_cut:]
    rime_sug = suggestion[suggestion_cut:]
    return bool(rime_source) and bool(rime_sug) and _edit_distance(rime_source, rime_sug) <= 2


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


def localize_llm_edits(
    source_text: str, suggestion: str, reason_code: str
) -> tuple[tuple[int, str, str], ...]:
    """Return every safe local edit contained in one model replacement.

    Small local models routinely ignore the "one discovery per error" rule and
    answer with a rewritten clause that folds several corrections together.
    ``localize_llm_edit`` refuses such a replacement wholesale, which throws
    away corrections that are individually anchorable. This splits the rewrite
    into its separate changed regions and validates each region on its own with
    exactly the same rules, so nothing weaker than a single-edit proposal is
    ever accepted.
    """
    single = localize_llm_edit(source_text, suggestion, reason_code)
    if single is not None:
        return (single,)
    if not source_text or _normalized(source_text) == _normalized(suggestion):
        return ()
    return _decompose_edits(source_text, suggestion, reason_code)


def _aligned_token_edits(
    source_text: str, suggestion: str, reason_code: str
) -> tuple[tuple[int, str, str], ...]:
    """Pair the two strings token by token when the model kept the word count.

    Character-level diffing is unreliable for Vietnamese, where two syllables
    often share letters and a single onset change can be split across
    non-adjacent regions. When the rewrite preserves the token count, matching
    tokens positionally recovers one clean edit per changed word.
    """
    source_parts = _TOKEN_SPLIT.split(source_text)
    suggestion_parts = _TOKEN_SPLIT.split(suggestion)
    if len(source_parts) < 3 or len(source_parts) != len(suggestion_parts):
        return ()
    edits: list[tuple[int, str, str]] = []
    offset = 0
    for source_part, suggestion_part in zip(source_parts, suggestion_parts, strict=True):
        if source_part != suggestion_part:
            prefix = _common_prefix_length(source_part, suggestion_part)
            suffix = _common_suffix_length(source_part[prefix:], suggestion_part[prefix:])
            if reason_code in _WORD_LEVEL_REASON_CODES or reason_code == "compound_word":
                src_start, src_end = _word_boundaries(
                    source_part, prefix, len(source_part) - suffix if suffix else len(source_part)
                )
                sug_start, sug_end = _word_boundaries(
                    suggestion_part, prefix, len(suggestion_part) - suffix if suffix else len(suggestion_part)
                )
                src_core = source_part[src_start:src_end]
                sug_core = suggestion_part[sug_start:sug_end]
                part_offset = offset + src_start
            else:
                src_core = source_part[prefix:len(source_part) - suffix if suffix else len(source_part)]
                sug_core = suggestion_part[prefix:len(suggestion_part) - suffix if suffix else len(suggestion_part)]
                part_offset = offset + prefix
            validated = _validate_localized_edit(
                part_offset, src_core, sug_core, reason_code
            )
            if validated is not None:
                edits.append(validated)
        offset += len(source_part)
    return tuple(edits)



def _decompose_edits(
    source_text: str, suggestion: str, reason_code: str
) -> tuple[tuple[int, str, str], ...]:
    aligned = _aligned_token_edits(source_text, suggestion, reason_code)
    if aligned:
        return aligned
    regions: list[tuple[int, int, int, int]] = []
    for tag, start, end, other_start, other_end in SequenceMatcher(
        None, source_text, suggestion, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        # A changed region rarely lines up with word edges, so grow it until the
        # comment shows a whole word on both sides of the arrow.
        source_span = _word_boundaries(source_text, start, end)
        suggestion_span = _word_boundaries(suggestion, other_start, other_end)
        regions.append((*source_span, *suggestion_span))

    merged: list[tuple[int, int, int, int]] = []
    for region in regions:
        if merged and region[0] <= merged[-1][1]:
            previous = merged[-1]
            merged[-1] = (
                previous[0],
                max(previous[1], region[1]),
                previous[2],
                max(previous[3], region[3]),
            )
            continue
        merged.append(region)

    edits: list[tuple[int, str, str]] = []
    for source_start, source_end, suggestion_start, suggestion_end in merged:
        validated = _validate_localized_edit(
            source_start,
            source_text[source_start:source_end],
            suggestion[suggestion_start:suggestion_end],
            reason_code,
        )
        if validated is not None:
            edits.append(validated)
    return tuple(edits)


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
        or _normalized(source_text).strip() == _normalized(suggestion).strip()
    ):
        return None
    if not suggestion and (
        reason_code not in _DELETION_REASON_CODES or len(source_text) > 16
    ):
        return None
    if is_heading(source_text):
        guarded = merge_tone_only(source_text, suggestion)
        if _normalized(guarded).strip() == _normalized(source_text).strip():
            return None
        suggestion = guarded
    if reason_code == "capitalization" and source_text.isupper() and len(source_text) >= 2:
        # All-caps text in headings, titles, or acronyms is standard administrative format.
        # Converting all-caps words to lowercase is a false positive.
        return None
    if reason_code in _ORTHOGRAPHIC_REASON_CODES:
        distance_source = _normalized(source_text)
        distance_suggestion = _normalized(suggestion)
        if (
            _without_diacritics(distance_source).casefold()
            != _without_diacritics(distance_suggestion).casefold()
            and _edit_distance(distance_source, distance_suggestion) > 1
            and not _onset_only_substitution(distance_source, distance_suggestion)
            and not _rime_only_substitution(distance_source, distance_suggestion)
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
