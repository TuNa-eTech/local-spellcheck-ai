"""HTTP transport layer for LLM API calls.

Handles URL construction, authentication, payload formatting, and raw HTTP
communication for OpenAI-compatible and Gemini providers.  Returns parsed
JSON dicts — no domain knowledge about discoveries or verdicts lives here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from soatvan.custom_rules.ai_config_repository import AiConfigEntry

_USER_AGENT = "SoatVan/0.1.5 (Desktop; vi-VN)"


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def _normalize_base_url(provider: str, base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if provider == "gemini":
        if url == "https://generativelanguage.googleapis.com":
            url = "https://generativelanguage.googleapis.com/v1beta"
    elif provider == "openai":
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")].rstrip("/")
        elif url in {"https://api.openai.com", "https://api.deepseek.com"}:
            url = f"{url}/v1"
    return url


# ---------------------------------------------------------------------------
# SSE / streaming helpers
# ---------------------------------------------------------------------------

def _parse_openai_text_response(raw_body: str) -> str:
    raw_body = raw_body.strip()
    if not raw_body:
        return ""
    if raw_body.startswith("data:"):
        chunks: list[str] = []
        for line in raw_body.splitlines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data_part = line[len("data:") :].strip()
            if data_part == "[DONE]":
                continue
            try:
                chunk_obj = json.loads(data_part)
                choices = chunk_obj.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content") or choices[0].get(
                        "message", {}
                    ).get("content", "")
                    if content:
                        chunks.append(str(content))
            except Exception:
                continue
        return "".join(chunks)

    obj = json.loads(raw_body)
    if isinstance(obj, dict):
        choices = obj.get("choices", [])
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message", {})
            return str(msg.get("content", ""))
    return ""


def _extract_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


# ---------------------------------------------------------------------------
# Public transport API
# ---------------------------------------------------------------------------

def call_llm(
    config: AiConfigEntry,
    system_prompt: str,
    user_content: str,
) -> dict[str, Any]:
    """Send *system_prompt* + *user_content* to the configured LLM provider
    and return the parsed JSON dict from the model's response.

    Raises on HTTP errors or unparseable responses.
    """
    base_url = _normalize_base_url(config.provider, config.base_url)
    default_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": _USER_AGENT,
    }

    if config.provider == "gemini":
        url = (
            f"{base_url}/models/{config.model_name}:generateContent"
            f"?key={config.api_key}"
        )
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_prompt}\n\n{user_content}"}],
                }
            ],
            "generationConfig": {
                "temperature": config.temperature,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=default_headers,
            method="POST",
        )
        headers = default_headers
    else:
        url = f"{base_url}/chat/completions"
        payload = {
            "model": config.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": config.temperature,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        headers = dict(default_headers)
        headers["Authorization"] = f"Bearer {config.api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    try:
        with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        if (
            err.code == 400
            and config.provider != "gemini"
            and "response_format" in payload
        ):
            payload_no_rf = dict(payload)
            del payload_no_rf["response_format"]
            req_retry = urllib.request.Request(
                url,
                data=json.dumps(payload_no_rf).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(
                req_retry, timeout=config.timeout_seconds
            ) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
        else:
            raise

    if config.provider == "gemini":
        data = json.loads(raw_body)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    else:
        text = _parse_openai_text_response(raw_body)
    parsed = json.loads(_extract_json_text(text))
    if isinstance(parsed, dict):
        return parsed
    return {}


def test_connection(config: AiConfigEntry) -> dict[str, Any]:
    """Lightweight ping to verify API key / URL / model availability."""
    if not config.api_key:
        return {
            "ok": False,
            "error": "API_KEY_REQUIRED",
            "message": "API key không được để trống",
        }

    base_url = _normalize_base_url(config.provider, config.base_url)
    default_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": _USER_AGENT,
    }

    try:
        if config.provider == "gemini":
            url = (
                f"{base_url}/models/{config.model_name}:generateContent"
                f"?key={config.api_key}"
            )
            payload: dict[str, Any] = {
                "contents": [{"role": "user", "parts": [{"text": "Ping"}]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=default_headers,
                method="POST",
            )
        else:
            url = f"{base_url}/chat/completions"
            payload = {
                "model": config.model_name,
                "messages": [{"role": "user", "content": "Ping"}],
                "temperature": 0.0,
                "max_tokens": 10,
                "stream": False,
            }
            headers = dict(default_headers)
            headers["Authorization"] = f"Bearer {config.api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )

        with urllib.request.urlopen(req, timeout=15) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            if not raw_body.strip():
                return {
                    "ok": False,
                    "error": "EMPTY_RESPONSE",
                    "message": "Máy chủ phản hồi rỗng (vui lòng kiểm tra lại Base URL).",
                }
            if config.provider == "gemini":
                try:
                    _ = json.loads(raw_body)
                except json.JSONDecodeError:
                    snippet = raw_body[:120].replace("\n", " ")
                    return {
                        "ok": False,
                        "error": "INVALID_JSON",
                        "message": f"Máy chủ không trả về JSON hợp lệ: {snippet}",
                    }
            else:
                text_content = _parse_openai_text_response(raw_body)
                if not text_content and not raw_body.strip().startswith("{"):
                    snippet = raw_body[:120].replace("\n", " ")
                    return {
                        "ok": False,
                        "error": "INVALID_JSON",
                        "message": f"Máy chủ không trả về JSON hợp lệ: {snippet}",
                    }
            return {
                "ok": True,
                "provider": config.provider,
                "model": config.model_name,
            }
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", errors="ignore")
        if err.code in (401, 403):
            return {
                "ok": False,
                "error": "API_KEY_INVALID",
                "message": "API key không hợp lệ hoặc không có quyền truy cập",
            }
        if err.code == 404:
            return {
                "ok": False,
                "error": "MODEL_NOT_FOUND",
                "message": f"Không tìm thấy model '{config.model_name}' hoặc sai URL ({err.url})",
            }
        return {
            "ok": False,
            "error": f"HTTP_{err.code}",
            "message": f"Lỗi HTTP {err.code}: {err_msg[:200]}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "CONNECTION_FAILED",
            "message": f"Không thể kết nối đến máy chủ AI: {exc}",
        }


test_connection.__test__ = False  # type: ignore[attr-defined]
