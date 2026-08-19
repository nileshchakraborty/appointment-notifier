import io
import json
from unittest.mock import patch

import pytest

from appointment_notifier.llm import (
    FallbackLlmClient,
    LlmProviderError,
    NvidiaNimProvider,
)


class StubProvider:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_fallback_uses_next_provider_after_failure():
    first = StubProvider("nvidia", error=LlmProviderError("rate limited"))
    second = StubProvider("ollama_cloud", result="fallback answer")
    third = StubProvider("ollama", result="should not be called")
    client = FallbackLlmClient([first, second, third])

    response = client.complete(
        [{"role": "user", "content": "hello"}],
        max_tokens=100,
        temperature=0.2,
    )

    assert response.content == "fallback answer"
    assert response.provider == "ollama_cloud"
    assert (first.calls, second.calls, third.calls) == (1, 1, 0)


def test_fallback_reports_when_every_provider_fails():
    client = FallbackLlmClient(
        [
            StubProvider("nvidia", error=LlmProviderError("unauthorized")),
            StubProvider("ollama", error=LlmProviderError("offline")),
        ]
    )

    with pytest.raises(LlmProviderError, match="all configured providers failed"):
        client.complete([], max_tokens=10, temperature=0)


def test_nvidia_provider_uses_bearer_secret_and_openai_payload(tmp_path):
    secret = tmp_path / "nvidia_api_key"
    secret.write_text("nvapi-test-secret", encoding="utf-8")
    provider = NvidiaNimProvider(
        url="https://integrate.api.nvidia.com",
        model="openai/gpt-oss-20b",
        api_key_file=str(secret),
        timeout_seconds=27,
    )
    response = io.BytesIO(
        json.dumps({"choices": [{"message": {"content": "NIM answer"}}]}).encode()
    )

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        answer = provider.complete(
            [{"role": "user", "content": "hello"}],
            max_tokens=80,
            temperature=0.1,
        )

    request = urlopen.call_args.args[0]
    payload = json.loads(request.data)
    assert answer == "NIM answer"
    assert request.full_url == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer nvapi-test-secret"
    assert payload["model"] == "openai/gpt-oss-20b"
    assert payload["max_tokens"] == 80
    assert urlopen.call_args.kwargs["timeout"] == 27
