from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from soatvan.checking.domain import Block
from soatvan.custom_rules.ai_config_repository import AiConfigEntry
from soatvan.models.cloud_api import CloudAiReviewer, test_ai_connection
from soatvan.workflow.ports import (
    CancellationToken,
    ClassificationCandidate,
    ReviewCandidate,
)


class DummyCancelToken(CancellationToken):
    def __init__(self, should_cancel: bool = False) -> None:
        self._should_cancel = should_cancel

    def raise_if_cancelled(self) -> None:
        if self._should_cancel:
            raise RuntimeError("CANCELLED")


def test_openai_compatible_review_success() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        temperature=0.2,
        timeout_seconds=30,
    )
    reviewer = CloudAiReviewer(config)
    assert reviewer.version == "openai:gpt-4o-mini"
    assert reviewer.minimum_confidence == 0.55

    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "candidate_id": "c1",
                                    "verdict": "keep",
                                    "confidence": 0.95,
                                }
                            ],
                            "discoveries": [
                                {
                                    "segment_id": "b1",
                                    "source_text": "nghiên cưu",
                                    "suggestion": "nghiên cứu",
                                    "category": "spelling",
                                    "reason_code": "spelling",
                                    "occurrence_index": 0,
                                    "confidence": 0.9,
                                },
                                {
                                    "segment_id": "b1",
                                    "source_text": "chính ta",
                                    "suggestion": "chính tả",
                                    "category": "spelling",
                                    "reason_code": "spelling",
                                    "occurrence_index": 0,
                                    "confidence": 0.88,
                                },
                            ],
                        }
                    )
                }
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(mock_response).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        blocks = (Block(id="b1", text="Đây là nghiên cưu về chính ta."),)
        candidates = (
            ReviewCandidate(
                candidate_id="c1",
                block_id="b1",
                start=7,
                end=17,
                source_text="nghiên cưu",
                suggestion="nghiên cứu",
                reason_code="spelling",
            ),
        )

        progress_calls: list[tuple[int, int]] = []
        result = reviewer.review(
            blocks,
            candidates,
            custom_prompt="Kiểm tra kỹ thuật.",
            cancellation=DummyCancelToken(),
            progress=lambda done, total: progress_calls.append((done, total)),
        )

        assert result.status == "complete"
        assert result.reviewed_chunks == 1
        assert len(result.verdicts) == 1
        assert result.verdicts[0].candidate_id == "c1"
        assert result.verdicts[0].verdict == "keep"
        assert len(result.discoveries) == 2
        assert result.discoveries[0].block_id == "b1"
        assert result.discoveries[0].source_text == "nghiên cưu"
        assert result.discoveries[0].suggestion == "nghiên cứu"
        assert result.discoveries[0].start == 7
        assert result.discoveries[0].end == 17
        assert result.discoveries[1].block_id == "b1"
        assert result.discoveries[1].source_text == "chính ta"
        assert result.discoveries[1].suggestion == "chính tả"
        assert result.discoveries[1].start == 21
        assert result.discoveries[1].end == 29
        assert len(progress_calls) == 1
        assert progress_calls[0] == (1, 1)


def test_gemini_review_success() -> None:
    config = AiConfigEntry(
        provider="gemini",
        api_key="AIzaSyTest",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
    )
    reviewer = CloudAiReviewer(config)
    assert reviewer.version == "gemini:gemini-2.5-flash"

    mock_gemini_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "discoveries": [
                                        {
                                            "segment_id": "b1",
                                            "source_text": "sai sot",
                                            "suggestion": "sai sót",
                                            "category": "spelling",
                                            "reason_code": "spelling",
                                            "occurrence_index": 0,
                                            "confidence": 0.92,
                                        }
                                    ]
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(mock_gemini_resp).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        blocks = (Block(id="b1", text="Văn bản có sai sot này."),)
        result = reviewer.review(blocks, (), "", DummyCancelToken())
        assert result.reviewed_chunks == 1
        assert result.status == "complete"
        assert len(result.discoveries) == 1
        assert result.discoveries[0].source_text == "sai sot"
        assert result.discoveries[0].suggestion == "sai sót"
        assert result.discoveries[0].start == 11
        assert result.discoveries[0].end == 18



def test_classify_method() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    reviewer = CloudAiReviewer(config)
    candidates = (
        ClassificationCandidate(
            candidate_id="c1",
            paragraph_id="p1",
            source_text="test",
            suggestion="thử",
            reason_code="spelling",
            occurrence_index=0,
            context="context",
        ),
    )
    verdicts = reviewer.classify(candidates, "", DummyCancelToken())
    assert len(verdicts) == 1
    assert verdicts[0].candidate_id == "c1"
    assert verdicts[0].verdict == "keep"


def test_review_cancellation() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    reviewer = CloudAiReviewer(config)
    blocks = (Block(id="b1", text="Một đoạn văn bản."),)
    cancel_token = DummyCancelToken(should_cancel=True)

    with pytest.raises(RuntimeError, match="CANCELLED"):
        reviewer.review(blocks, (), "", cancel_token)

    with pytest.raises(RuntimeError, match="CANCELLED"):
        reviewer.classify((), "", cancel_token)


def test_empty_blocks_review() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    reviewer = CloudAiReviewer(config)
    result = reviewer.review((), (), "", DummyCancelToken())
    assert result.total_chunks == 0
    assert result.reviewed_chunks == 0
    assert len(result.discoveries) == 0


def test_ai_connection_success_openai() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        res = test_ai_connection(config)
        assert res["ok"] is True
        assert res["provider"] == "openai"
        assert res["model"] == "gpt-4o-mini"


def test_ai_connection_success_gemini() -> None:
    config = AiConfigEntry(
        provider="gemini",
        api_key="AIzaSyTest",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
    )
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        ).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        res = test_ai_connection(config)
        assert res["ok"] is True
        assert res["provider"] == "gemini"
        assert res["model"] == "gemini-2.5-flash"


def test_ai_connection_empty_api_key() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    res = test_ai_connection(config)
    assert res["ok"] is False
    assert res["error"] == "API_KEY_REQUIRED"


def test_ai_connection_http_401_invalid_key() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-badkey",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=401,
        msg="Unauthorized",
        hdrs=MagicMock(),
        fp=io.BytesIO(b'{"error": "Invalid API key"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        res = test_ai_connection(config)
        assert res["ok"] is False
        assert res["error"] == "API_KEY_INVALID"


def test_ai_connection_http_404_model_not_found() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="non-existent-model",
    )
    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=404,
        msg="Not Found",
        hdrs=MagicMock(),
        fp=io.BytesIO(b'{"error": "Model not found"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        res = test_ai_connection(config)
        assert res["ok"] is False
        assert res["error"] == "MODEL_NOT_FOUND"


def test_ai_connection_generic_http_error() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=500,
        msg="Internal Server Error",
        hdrs=MagicMock(),
        fp=io.BytesIO(b'{"error": "Server error"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        res = test_ai_connection(config)
        assert res["ok"] is False
        assert res["error"] == "HTTP_500"


def test_ai_connection_network_error() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://bad-domain-that-does-not-exist.local",
        model_name="gpt-4o-mini",
    )
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS lookup failed")):
        res = test_ai_connection(config)
        assert res["ok"] is False
        assert res["error"] == "CONNECTION_FAILED"


def test_review_chunk_failure_handling() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    reviewer = CloudAiReviewer(config)
    blocks = (Block(id="b1", text="Đoạn văn bị lỗi."),)

    # When ALL chunks fail with a non-retryable error, the reviewer raises
    # MODEL_FULL_REVIEW_FAILED rather than silently returning zero findings.
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")),
        pytest.raises(ValueError, match="MODEL_FULL_REVIEW_FAILED"),
    ):
        reviewer.review(blocks, (), "", DummyCancelToken())


def test_gemini_markdown_fences_parsing() -> None:
    config = AiConfigEntry(
        provider="gemini",
        api_key="AIzaSyTest",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
    )
    reviewer = CloudAiReviewer(config)
    mock_gemini_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "```json\n"
                            + json.dumps(
                                {
                                    "discoveries": [
                                        {
                                            "segment_id": "b1",
                                            "source_text": "nghiên cưu",
                                            "suggestion": "nghiên cứu",
                                            "category": "spelling",
                                            "reason_code": "spelling",
                                            "occurrence_index": 0,
                                            "confidence": 0.9,
                                        }
                                    ]
                                }
                            )
                            + "\n```"
                        }
                    ]
                }
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(mock_gemini_resp).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        blocks = (Block(id="b1", text="Đây là nghiên cưu."),)
        result = reviewer.review(blocks, (), "", DummyCancelToken())
        assert result.status == "complete"
        assert len(result.discoveries) == 1
        assert result.discoveries[0].source_text == "nghiên cưu"


def test_build_chunks_splitting() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    reviewer = CloudAiReviewer(config)
    long_text = "a" * 2500
    blocks = (
        Block(id="b1", text=long_text),
        Block(id="b2", text=long_text),
    )
    chunks = reviewer._build_chunks(blocks, ())
    assert len(chunks) == 2
    assert chunks[0][0] == "chunk_0"
    assert chunks[1][0] == "chunk_1"

