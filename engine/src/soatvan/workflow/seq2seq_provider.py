"""Concrete Seq2SeqProvider that wraps Seq2SeqSpeller for use in the process pipeline.

Usage:
    from soatvan.workflow.seq2seq_provider import LocalSeq2SeqProvider

    provider = LocalSeq2SeqProvider(model_dir="./models/vn-spell-correction-small")
    processor = ProcessDocument(documents, dictionary, rules, seq2seq=provider)
"""

from __future__ import annotations

import os
from pathlib import Path

from soatvan.checking.domain import Block, Finding
from soatvan.models.seq2seq_speller import Seq2SeqSpeller, is_transformers_available
from soatvan.workflow.ports import CancellationToken

DEFAULT_MODEL_DIR = Path.home() / ".config" / "soatvan" / "models" / "vn-spell-correction-small"


def find_default_model_dir() -> Path:
    """Find the best default path for the vn-spell-correction-small model."""
    candidates = [
        # Relative to project repo root
        Path(__file__).resolve().parents[4] / "models" / "vn-spell-correction-small",
        Path.cwd() / "models" / "vn-spell-correction-small",
        (
            Path(os.environ["LOCALAPPDATA"]) / "SoatVan" / "models" / "vn-spell-correction-small"
            if "LOCALAPPDATA" in os.environ
            else None
        ),
        DEFAULT_MODEL_DIR,
    ]
    for c in candidates:
        if c is not None and (c / "config.json").exists():
            return c
    return DEFAULT_MODEL_DIR


class LocalSeq2SeqProvider:
    """Concrete Seq2SeqProvider backed by a local HuggingFace safetensors checkpoint.

    Drop-in for the Seq2SeqProvider protocol.  The model is lazy-loaded on the
    first call to check_blocks so startup is not delayed.
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        device: str = "auto",
        num_beams: int = 1,
    ) -> None:
        path = Path(model_dir) if model_dir else find_default_model_dir()
        if not (path / "config.json").exists() and (find_default_model_dir() / "config.json").exists():
            path = find_default_model_dir()
        self._speller = Seq2SeqSpeller(
            model_id=str(path),
            device=device,
            num_beams=num_beams,
            heading_retry=True,
        )
        self._model_dir = path

    def is_ready(self) -> bool:
        """Return True if the model directory exists and transformers is installed."""
        return is_transformers_available() and (self._model_dir / "config.json").exists()

    def check_blocks(
        self,
        blocks: list[Block],
        ignored_words: frozenset[str],
        cancellation: CancellationToken,
    ) -> list[Finding]:
        """Run seq2seq correction over every block; skip blocks whose text is ignored."""
        import sys

        total = len(blocks)
        findings: list[Finding] = []
        for i, block in enumerate(blocks):
            cancellation.raise_if_cancelled()
            if not block.text.strip():
                continue
            block_findings = self._speller.check_block(block)
            for f in block_findings:
                if _is_ignored(f.source_text, ignored_words):
                    continue
                findings.append(f)
            if (i + 1) % 10 == 0 or i + 1 == total:
                sys.stderr.write(
                    f"[SoatVan-Seq2Seq] Block {i + 1}/{total} processed, "
                    f"{len(findings)} finding(s) so far.\n"
                )
                sys.stderr.flush()
        return findings

    def unload(self) -> None:
        """Unload the underlying model from memory and free GPU/system RAM."""
        self._speller.unload()


def _is_ignored(text: str, ignored_words: frozenset[str]) -> bool:
    import unicodedata

    normalized = unicodedata.normalize("NFC", text).casefold()
    ignored = {unicodedata.normalize("NFC", w).casefold() for w in ignored_words}
    return normalized in ignored
