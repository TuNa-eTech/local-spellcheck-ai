"""Unit tests for direct seq2seq spell-correction adapter."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from soatvan.checking.domain import Block
from soatvan.models.classifier import ModelLoadFailed, ModelRuntimeUnavailable
from soatvan.models.seq2seq_speller import (
    DEFAULT_SPELL_MODEL,
    Seq2SeqSpeller,
    is_transformers_available,
)


def test_seq2seq_speller_initialization_defaults() -> None:
    speller = Seq2SeqSpeller()
    assert speller.model_id == DEFAULT_SPELL_MODEL
    assert speller.device == "auto"
    assert speller.heading_retry is True
    assert speller.is_loaded is False


def test_seq2seq_speller_raises_when_transformers_missing() -> None:
    speller = Seq2SeqSpeller()
    with patch("soatvan.models.seq2seq_speller.is_transformers_available", return_value=False):
        with pytest.raises(ModelRuntimeUnavailable, match="transformers and torch are required"):
            speller.load()


def test_seq2seq_speller_predict_and_check_block_mocked() -> None:
    speller = Seq2SeqSpeller()

    # Mock tokenizer and model
    mock_tokenizer = MagicMock()
    mock_model = MagicMock()

    mock_tokenizer.return_value.to.return_value = {"input_ids": [1, 2, 3]}
    mock_model.generate.return_value = [[10, 20, 30]]
    # Corrects typo: "thựchiện kế hoặch" -> "thực hiện kế hoạch"
    mock_tokenizer.decode.return_value = "thực hiện kế hoạch"

    speller._tokenizer = mock_tokenizer
    speller._model = mock_model

    # 1. Test predict
    corrected = speller.predict("thựchiện kế hoặch")
    assert corrected == "thực hiện kế hoạch"

    # 2. Test predict_batch
    batch_out = speller.predict_batch(["thựchiện kế hoặch", ""])
    assert batch_out == ["thực hiện kế hoạch", ""]

    # 3. Test check_block
    block = Block("doc:p0", "Cần thựchiện kế hoạch này.")
    mock_tokenizer.decode.return_value = "Cần thực hiện kế hoạch này."

    findings = speller.check_block(block)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == "spelling"
    assert f.detector_id == "model.seq2seq.v1"
    assert f.source_text == "thựchiện"
    assert f.suggestion == "thực hiện"


def test_seq2seq_speller_heading_retry_preserves_uppercase() -> None:
    speller = Seq2SeqSpeller(heading_retry=True)

    mock_tokenizer = MagicMock()
    mock_model = MagicMock()

    mock_tokenizer.return_value.to.return_value = {"input_ids": [1]}
    mock_model.generate.return_value = [[1]]
    # Model generates lowercase: "đơn xin nghỉ việc"
    mock_tokenizer.decode.return_value = "đơn xin nghỉ việc"

    speller._tokenizer = mock_tokenizer
    speller._model = mock_model

    # Source is uppercase heading: "ĐƠN XIN NGHĨ VIỆC"
    result = speller.predict("ĐƠN XIN NGHĨ VIỆC")
    assert result == "ĐƠN XIN NGHỈ VIỆC"


def test_seq2seq_speller_handles_empty_input() -> None:
    speller = Seq2SeqSpeller()
    assert speller.predict("") == ""
    assert speller.predict("   ") == "   "
    assert speller.check_block(Block("doc:p0", "")) == []
