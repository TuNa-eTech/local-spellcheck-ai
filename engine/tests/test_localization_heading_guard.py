"""Tests for heading and uppercase protection during LLM edit localization."""
from __future__ import annotations

from soatvan.checking.localization import localize_llm_edit


def test_heading_uppercase_preserves_casing_on_tone_fix() -> None:
    # LLM tries to fix typo in uppercase title by lowercasing everything
    source = "ĐƠN XIN NGHĨ VIỆC"
    suggestion = "đơn xin nghỉ việc"
    edit = localize_llm_edit(source, suggestion, "spelling")

    assert edit is not None
    offset, loc_src, loc_sug = edit
    assert loc_src == "ĐƠN XIN NGHĨ VIỆC"
    assert loc_sug == "ĐƠN XIN NGHỈ VIỆC"


def test_heading_acronym_code_is_protected_from_mutation() -> None:
    # LLM hallucinates an altered acronym in a document code
    source = "Số: 15/QĐ-UBND"
    suggestion = "số: 15/qđ-bnd"
    edit = localize_llm_edit(source, suggestion, "spelling")

    # The edit should be rejected (None) because code modification is disallowed
    assert edit is None
