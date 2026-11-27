"""Tests for the Ollama, OpenAI-compatible, and Gemini providers.

Most tests mock `requests.post` so the suite runs in CI without a real
Ollama/OpenAI/Gemini endpoint reachable. A small number of tests are gated
on Ollama actually being reachable (`pytest.mark.skipif`) - these exercise
the real HTTP path end to end rather than a mocked one, for anyone (like
during this feature's own development) who has `ollama serve` running
locally; they are skipped, not failed, everywhere else including CI. There
is no live-gated test for Gemini - unlike Ollama, there's no free local
endpoint to gate on, and no funded Google AI key was used to build this
project (same honest caveat as AnthropicProvider).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from helix.providers.gemini_provider import GeminiProvider
from helix.providers.ollama_provider import OllamaProvider
from helix.providers.openai_compatible_provider import OpenAICompatibleProvider


def _ollama_reachable() -> bool:
    try:
        requests.get("http://localhost:11434/api/tags", timeout=1.0)
        return True
    except requests.exceptions.RequestException:
        return False


# --- OllamaProvider: mocked ------------------------------------------------


def test_ollama_parses_native_response_shape():
    provider = OllamaProvider(model="qwen3:4b")
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "message": {"role": "assistant", "content": '{"ok": true}'},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 42,
        "eval_count": 7,
    }
    fake_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_response) as mock_post:
        result = provider.complete("system prompt", "user prompt", max_tokens=100)

    assert result.text == '{"ok": true}'
    assert result.input_tokens == 42
    assert result.output_tokens == 7
    assert result.estimated_cost_usd == 0.0
    # The reasoning-budget floor is applied even when max_tokens is smaller.
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["options"]["num_predict"] >= provider.min_predict_tokens


def test_ollama_raises_clear_error_when_unreachable():
    provider = OllamaProvider(base_url="http://localhost:19999")  # nothing listens here
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(RuntimeError, match="Could not reach Ollama"):
            provider.complete("sys", "prompt")


def test_ollama_raises_clear_error_on_timeout():
    provider = OllamaProvider(timeout_seconds=5.0)
    with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        with pytest.raises(RuntimeError, match="did not respond within"):
            provider.complete("sys", "prompt")


def test_ollama_respects_custom_min_predict_tokens():
    provider = OllamaProvider(min_predict_tokens=16)
    fake_response = MagicMock()
    fake_response.json.return_value = {"message": {"content": "x"}, "done": True}
    fake_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=fake_response) as mock_post:
        provider.complete("sys", "prompt", max_tokens=4)
    assert mock_post.call_args.kwargs["json"]["options"]["num_predict"] == 16


# --- OllamaProvider: live, skipped unless reachable -----------------------


@pytest.mark.skipif(not _ollama_reachable(), reason="Ollama is not running locally")
def test_ollama_live_completion():
    provider = OllamaProvider(model="qwen3:4b")
    response = provider.complete(
        "You are a helpful assistant. Respond with exactly the requested text and nothing else.",
        "Respond with exactly: hello world",
    )
    assert "hello world" in response.text.lower()
    assert response.input_tokens > 0
    assert response.output_tokens > 0


# --- OpenAICompatibleProvider: mocked --------------------------------------


def test_openai_compatible_requires_key_for_remote_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No API key found"):
        OpenAICompatibleProvider(base_url="https://api.openai.com/v1")


def test_openai_compatible_does_not_require_key_for_localhost():
    # Should not raise, even with no key anywhere.
    provider = OpenAICompatibleProvider(base_url="http://localhost:1234/v1", api_key=None)
    assert provider.api_key is None


def test_openai_compatible_reads_key_from_custom_env_var(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key-123")
    provider = OpenAICompatibleProvider(
        base_url="https://api.groq.com/openai/v1", api_key_env="GROQ_API_KEY"
    )
    assert provider.api_key == "test-key-123"


def test_openai_compatible_parses_chat_completion_response():
    provider = OpenAICompatibleProvider(base_url="http://localhost:1234/v1")
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "model": "local-model",
        "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_response):
        result = provider.complete("system", "prompt")

    assert result.text == '{"ok": true}'
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.estimated_cost_usd == 0.0  # no pricing supplied


def test_openai_compatible_estimates_cost_when_pricing_supplied():
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
        pricing_per_million_tokens={"input": 1.0, "output": 2.0},
    )
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "model": "local-model",
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
    }
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_response):
        result = provider.complete("system", "prompt")

    assert result.estimated_cost_usd == pytest.approx(2.0)  # 1.0 + 0.5*2.0


def test_openai_compatible_retries_without_json_mode_on_400():
    provider = OpenAICompatibleProvider(base_url="http://localhost:1234/v1", json_mode=True)
    rejection = MagicMock(status_code=400)
    success = MagicMock(status_code=200)
    success.json.return_value = {
        "model": "local-model",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {},
    }
    success.raise_for_status = MagicMock()

    with patch("requests.post", side_effect=[rejection, success]) as mock_post:
        result = provider.complete("system", "prompt")

    assert result.text == "ok"
    assert mock_post.call_count == 2
    assert "response_format" not in mock_post.call_args_list[1].kwargs["json"]


def test_openai_compatible_raises_clear_error_when_unreachable():
    provider = OpenAICompatibleProvider(base_url="http://localhost:19999/v1")
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(RuntimeError, match="Could not reach"):
            provider.complete("sys", "prompt")


def test_openai_compatible_raises_clear_error_on_timeout():
    provider = OpenAICompatibleProvider(base_url="http://localhost:1234/v1", timeout_seconds=5.0)
    with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        with pytest.raises(RuntimeError, match="did not respond within"):
            provider.complete("sys", "prompt")


# --- GeminiProvider: mocked -------------------------------------------------


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No API key found"):
        GeminiProvider()


def test_gemini_reads_key_from_custom_env_var(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_AI_KEY", "test-key-123")
    provider = GeminiProvider(api_key_env="GOOGLE_AI_KEY")
    assert provider.api_key == "test-key-123"


def test_gemini_parses_generate_content_response():
    provider = GeminiProvider(api_key="test-key")
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": '{"ok": true}'}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4},
        "modelVersion": "gemini-2.0-flash",
    }
    fake_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_response) as mock_post:
        result = provider.complete("system prompt", "user prompt", max_tokens=100)

    assert result.text == '{"ok": true}'
    assert result.input_tokens == 12
    assert result.output_tokens == 4
    assert result.stop_reason == "STOP"
    # The API key travels as a query param, never in the request body.
    assert mock_post.call_args.kwargs["params"]["key"] == "test-key"
    assert "key" not in mock_post.call_args.kwargs["json"]


def test_gemini_estimates_cost_for_known_model():
    provider = GeminiProvider(api_key="test-key", model="gemini-2.0-flash")
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1_000_000, "candidatesTokenCount": 500_000},
    }
    fake_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_response):
        result = provider.complete("system", "prompt")

    assert result.estimated_cost_usd == pytest.approx(0.10 + 0.5 * 0.40)


def test_gemini_raises_clear_error_when_no_candidates():
    """Blocked-by-safety-filter responses have an empty candidates list -
    this must fail with a clear message, not an IndexError."""
    provider = GeminiProvider(api_key="test-key")
    fake_response = MagicMock()
    fake_response.json.return_value = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
    fake_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_response):
        with pytest.raises(RuntimeError, match="no candidates"):
            provider.complete("sys", "prompt")


def test_gemini_raises_clear_error_when_unreachable():
    provider = GeminiProvider(api_key="test-key")
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(RuntimeError, match="Could not reach"):
            provider.complete("sys", "prompt")


def test_gemini_raises_clear_error_on_timeout():
    provider = GeminiProvider(api_key="test-key", timeout_seconds=5.0)
    with patch("requests.post", side_effect=requests.exceptions.Timeout("timed out")):
        with pytest.raises(RuntimeError, match="did not respond within"):
            provider.complete("sys", "prompt")
