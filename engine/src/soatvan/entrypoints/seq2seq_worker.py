"""Subprocess worker for seq2seq spell-checking.

Runs seq2seq inference in an isolated process so that torch memory
allocations cannot cause the main sidecar process to be OOM-killed
(critical on macOS with unified memory after unloading a GGUF model).

Protocol (JSON over stdin/stdout):
  stdin  → {"model_dir": "...", "blocks": [{"id":"…","text":"…","kind":"…"}], "ignored_words": [...]}
  stdout ← {"findings": [{"id":"…","category":"…", …}]}
  stderr ← diagnostic log lines (forwarded to parent stderr)
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    data = json.loads(sys.stdin.buffer.read())
    model_dir = data["model_dir"]
    ignored_words = frozenset(data.get("ignored_words", []))

    from soatvan.checking.domain import Block
    from soatvan.workflow.seq2seq_provider import LocalSeq2SeqProvider

    blocks = [Block(id=b["id"], text=b["text"], kind=b.get("kind", "paragraph")) for b in data["blocks"]]

    class _NoCancellation:
        def raise_if_cancelled(self) -> None:
            pass

    provider = LocalSeq2SeqProvider(model_dir=model_dir)
    findings = provider.check_blocks(blocks, ignored_words, _NoCancellation())

    result = {
        "findings": [
            {
                "id": f.id,
                "category": f.category,
                "origin": f.origin,
                "detector_id": f.detector_id,
                "block_id": f.block_id,
                "start": f.start,
                "end": f.end,
                "source_text": f.source_text,
                "suggestion": f.suggestion,
                "reason": f.reason,
                "rule_version": f.rule_version,
                "confidence": f.confidence,
            }
            for f in findings
        ]
    }
    sys.stdout.buffer.write(json.dumps(result).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
