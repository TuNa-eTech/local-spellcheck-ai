from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from soatvan.entrypoints import sidecar as sidecar_module
from soatvan.entrypoints.sidecar import Sidecar


def test_ai_config_ipc_dispatch_crud_and_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    sidecar = Sidecar()

    # 1. Initial state: active provider is local, configs list is empty
    initial = sidecar.dispatch("ai_config.get", {})
    assert initial["active_provider"] == "local"
    assert initial["configs"] == []

    # 2. Update OpenAI config
    update_res = sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "api_key": "sk-1234567890abcdef",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "temperature": 0.2,
            "timeout_seconds": 30,
            "is_active": True,
        },
    )
    assert update_res["updated"] is True
    assert update_res["config"]["provider"] == "openai"
    assert update_res["config"]["masked_key"] == "sk-1...cdef"

    # 3. ai_config.get reflects active provider
    get_res = sidecar.dispatch("ai_config.get", {})
    assert get_res["active_provider"] == "openai"
    assert len(get_res["configs"]) == 1
    assert get_res["configs"][0]["model_name"] == "gpt-4o-mini"

    # 4. Update Gemini config (not active)
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "gemini",
            "api_key": "AIzaSySecretKey99",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model_name": "gemini-2.5-flash",
            "is_active": False,
        },
    )
    get_res2 = sidecar.dispatch("ai_config.get", {})
    assert len(get_res2["configs"]) == 2
    assert get_res2["active_provider"] == "openai"

    # 5. Set active provider to gemini
    set_active_res = sidecar.dispatch("ai_config.set_active", {"provider": "gemini"})
    assert set_active_res["active_provider"] == "gemini"
    assert sidecar.dispatch("ai_config.get", {})["active_provider"] == "gemini"

    # 6. Set active provider to local
    set_local_res = sidecar.dispatch("ai_config.set_active", {"provider": "local"})
    assert set_local_res["active_provider"] == "local"
    assert sidecar.dispatch("ai_config.get", {})["active_provider"] == "local"

    # 7. Invalid parameters
    with pytest.raises(ValueError, match="INVALID_PARAMS"):
        sidecar.dispatch("ai_config.update", {"provider": "unsupported"})
    with pytest.raises(ValueError, match="INVALID_PARAMS"):
        sidecar.dispatch("ai_config.set_active", {"provider": "invalid"})


def test_ai_config_test_connection_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    sidecar = Sidecar()

    mock_resp = {"choices": [{"message": {"content": "pong"}}]}
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(mock_resp).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        res = sidecar.dispatch(
            "ai_config.test_connection",
            {
                "provider": "openai",
                "api_key": "sk-testkey",
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4o-mini",
            },
        )
        assert res["ok"] is True
        assert res["provider"] == "openai"


def test_dynamic_classifier_provider_switching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    sidecar = Sidecar()
    dynamic = sidecar.classifiers

    # Initially local - no model installed
    assert dynamic.classifier() is None
    assert dynamic.supports_full_review() is False

    # Configure and activate OpenAI
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "api_key": "sk-12345678",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    assert dynamic.supports_full_review() is True
    reviewer = dynamic.classifier()
    assert reviewer is not None
    assert reviewer.version == "openai:gpt-4o-mini"

    # Switch to Gemini
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "gemini",
            "api_key": "AIzaSyTestKey",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model_name": "gemini-2.5-flash",
            "is_active": True,
        },
    )
    reviewer_gemini = dynamic.classifier()
    assert reviewer_gemini is not None
    assert reviewer_gemini.version == "gemini:gemini-2.5-flash"

    # Switch back to local
    sidecar.dispatch("ai_config.set_active", {"provider": "local"})
    assert dynamic.classifier() is None
    assert dynamic.supports_full_review() is False


def test_model_status_with_cloud_ai_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    sidecar = Sidecar()

    # Local without model installed -> not_installed
    status_local = sidecar.dispatch("model.status", {})
    assert status_local["state"] == "not_installed"

    # Active OpenAI configured
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "api_key": "sk-12345678",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    status_cloud = sidecar.dispatch("model.status", {})
    assert status_cloud["state"] == "ready"
    assert status_cloud["model_id"] == "gpt-4o-mini"
    assert status_cloud["capabilities"]["full_review"] is True
    assert status_cloud["capabilities"]["candidate_filter"] is True

    # Cloud AI active but no API key
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    status_no_key = sidecar.dispatch("model.status", {})
    assert status_no_key["state"] == "installed"
    assert status_no_key["code"] == "API_KEY_REQUIRED"


def test_job_start_with_cloud_ai_full_review(make_docx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    frames: list[dict[str, object]] = []
    monkeypatch.setattr(sidecar_module, "emit", frames.append)

    sidecar = Sidecar()
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )

    source = make_docx([["Đây là báo cáo ngiên cứu."]])
    output = tmp_path / "output.docx"

    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "discoveries": [
                                {
                                    "segment_id": "document:p0",
                                    "source_text": "ngiên cứu",
                                    "suggestion": "nghiên cứu",
                                    "category": "spelling",
                                    "reason_code": "spelling",
                                    "occurrence_index": 0,
                                    "confidence": 0.95,
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(mock_llm_response).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        res = sidecar.dispatch(
            "job.start",
            {
                "job_id": "job-cloud-1",
                "source_path": str(source),
                "temporary_output_path": str(output),
                "preset": "standard",
                "use_model": True,
                "full_review": True,
            },
        )
        assert res["accepted"] is True

        # Wait for completion
        deadline = time.monotonic() + 5
        while "job-cloud-1" in sidecar.jobs and time.monotonic() < deadline:
            time.sleep(0.02)

        terminals = [
            f for f in frames if f.get("event") in {"job.completed", "job.no_findings", "job.failed"}
        ]
        assert len(terminals) == 1
        assert terminals[0]["event"] == "job.completed"
        assert terminals[0]["data"]["finding_count"] == 1
        assert output.is_file()
