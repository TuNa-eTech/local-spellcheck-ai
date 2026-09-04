"""Direct integration adapter for nrl-ai seq2seq spell-correction and diacritic models.

Supports HuggingFace seq2seq checkpoints:
- nrl-ai/vn-spell-correction-small (BARTpho-syllable 115M, low latency ~50ms/sent)
- nrl-ai/vn-spell-correction-base (ViT5-base 220M, typo/OCR/Telex correction)
- nrl-ai/vn-diacritic-vit5-base (ViT5-base 220M, diacritic restoration)
- nrl-ai/vn-diacritic-small (BARTpho-syllable 115M)
"""

from __future__ import annotations

import contextlib
import importlib.util
import re
import unicodedata
from collections.abc import Iterator
from typing import Any, cast

from soatvan.checking.domain import Block, Finding
from soatvan.checking.heading import is_heading, merge_tone_only
from soatvan.checking.localization import localize_llm_edits
from soatvan.models.classifier import ModelLoadFailed, ModelRuntimeUnavailable

DEFAULT_SPELL_MODEL = "nrl-ai/vn-spell-correction-small"


@contextlib.contextmanager
def _no_grad_ctx() -> Iterator[None]:
    try:
        import torch

        with torch.no_grad():
            yield
    except ImportError:
        yield


def is_transformers_available() -> bool:
    """Check if torch, transformers, and sentencepiece are importable."""
    return all(
        importlib.util.find_spec(pkg) is not None
        for pkg in ("sentencepiece", "torch", "transformers")
    )


class Seq2SeqSpeller:
    """Adapter for nrl-ai seq2seq Vietnamese spelling correction models."""

    def __init__(
        self,
        model_id: str = DEFAULT_SPELL_MODEL,
        device: str = "auto",
        max_input_tokens: int = 512,
        num_beams: int = 1,
        heading_retry: bool = True,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.max_input_tokens = max_input_tokens
        self.num_beams = num_beams
        self.heading_retry = heading_retry
        self._tokenizer: Any = None
        self._model: Any = None
        self._resolved_device: str = "cpu"

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    def load(self) -> None:
        """Load tokenizer and model weights into memory."""
        if self.is_loaded:
            return

        if not is_transformers_available():
            raise ModelRuntimeUnavailable(
                "transformers, torch, and sentencepiece are required for Seq2SeqSpeller. "
                "Install with: uv sync --project engine --extra seq2seq"
            )

        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        if self.device == "auto":
            if torch.cuda.is_available():
                self._resolved_device = "cuda"
            else:
                self._resolved_device = "cpu"
        else:
            self._resolved_device = self.device

        try:
            self._tokenizer = cast(Any, AutoTokenizer).from_pretrained(self.model_id)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id)
            self._model.to(self._resolved_device)
            self._model.eval()
        except Exception as exc:
            raise ModelLoadFailed(f"Failed to load model {self.model_id!r}: {exc}") from exc

    def predict(self, text: str) -> str:
        """Correct typos and restore diacritics in a single Vietnamese sentence."""
        if not text or not text.strip():
            return text

        self.load()

        norm_text = unicodedata.normalize("NFC", text.strip())
        is_title = self.heading_retry and is_heading(norm_text)

        # For heading inputs, lowercase temporarily to keep in-distribution
        feed_text = norm_text.casefold() if is_title else norm_text

        inputs = self._tokenizer(
            feed_text,
            return_tensors="pt",
            max_length=self.max_input_tokens,
            truncation=True,
        ).to(self._resolved_device)

        with _no_grad_ctx():
            outputs = self._model.generate(
                **inputs,
                max_length=self.max_input_tokens,
                num_beams=self.num_beams,
            )

        decoded = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
        corrected = unicodedata.normalize("NFC", decoded.strip())

        if is_title:
            return merge_tone_only(norm_text, corrected)

        return corrected

    def predict_batch(self, texts: list[str]) -> list[str]:
        """Batch inference across multiple sentences."""
        if not texts:
            return []

        return [self.predict(t) for t in texts]

    def check_block(self, block: Block) -> list[Finding]:
        """Run seq2seq spelling correction on a block and return localized findings."""
        if not block.text.strip():
            return []

        chunks = _split_into_sentences(block.text)
        if not chunks:
            return []

        findings: list[Finding] = []
        for start_offset, _end_offset, chunk_text in chunks:
            if not chunk_text.strip():
                continue
            predicted = self.predict(chunk_text)
            if predicted == chunk_text:
                continue

            localized_edits = localize_llm_edits(chunk_text, predicted, "spelling")
            for offset, source_text, suggestion in localized_edits:
                global_offset = start_offset + offset
                finding = Finding(
                    id=f"{block.id}:m:{global_offset}",
                    category="spelling",
                    origin="model",
                    detector_id="model.seq2seq.v1",
                    block_id=block.id,
                    start=global_offset,
                    end=global_offset + len(source_text),
                    source_text=source_text,
                    suggestion=suggestion,
                    reason=f"Đề xuất chỉnh sửa chính tả theo ngữ cảnh: “{suggestion}”.",
                    rule_version="1.0",
                    confidence=0.92,
                )
                findings.append(finding)
        return findings


def _split_into_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split block text into sentences while preserving exact character offsets."""
    if not text.strip():
        return []
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in re.finditer(r"(?:(?<=[.!?])\s+|\n+)", text):
        end = match.start()
        chunk = text[start:end]
        if chunk.strip():
            spans.append((start, end, chunk))
        start = match.end()
    if start < len(text):
        chunk = text[start:]
        if chunk.strip():
            spans.append((start, len(text), chunk))
    return spans
