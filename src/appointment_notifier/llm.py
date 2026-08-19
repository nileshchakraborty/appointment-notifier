from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import TrendSettings

LOGGER = logging.getLogger(__name__)


class LlmProvider(Protocol):
    name: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> str: ...


class LlmProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmResponse:
    content: str
    provider: str


class FallbackLlmClient:
    def __init__(self, providers: list[LlmProvider]) -> None:
        self.providers = providers

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> LlmResponse:
        failures = []
        for provider in self.providers:
            try:
                content = provider.complete(
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return LlmResponse(content=content, provider=provider.name)
            except (LlmProviderError, OSError, ValueError, urllib.error.URLError) as exc:
                failures.append(f"{provider.name}: {exc}")
                LOGGER.warning("LLM provider %s failed: %s", provider.name, exc)
        raise LlmProviderError("all configured providers failed: " + "; ".join(failures))


class OllamaProvider:
    def __init__(
        self,
        *,
        name: str,
        url: str,
        model: str,
        api_key_file: str = "",
        context_tokens: int = 2048,
    ) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.model = model
        self.api_key_file = api_key_file
        self.context_tokens = context_tokens

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key_file:
            headers["Authorization"] = "Bearer " + _read_secret(self.api_key_file)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "5m",
            "options": {
                "temperature": temperature,
                "num_ctx": self.context_tokens,
                "num_predict": max_tokens,
            },
        }
        data = _request_json(
            self.url + "/api/chat",
            payload,
            headers,
            timeout=120,
        )
        content = str((data.get("message") or {}).get("content") or "").strip()
        if not content:
            raise LlmProviderError("empty response")
        return content


class NvidiaNimProvider:
    name = "nvidia"

    def __init__(
        self,
        *,
        url: str,
        model: str,
        api_key_file: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.api_key_file = api_key_file
        self.timeout_seconds = timeout_seconds

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> str:
        headers = {
            "Authorization": "Bearer " + _read_secret(self.api_key_file),
            "Content-Type": "application/json",
        }
        data = _request_json(
            self.url + "/v1/chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            headers,
            timeout=self.timeout_seconds,
        )
        choices = data.get("choices") or []
        content = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "").strip()
        if not content:
            raise LlmProviderError("empty response")
        return content


def build_llm_client(settings: TrendSettings) -> FallbackLlmClient:
    configured: dict[str, LlmProvider] = {}
    if settings.nvidia_enabled:
        configured["nvidia"] = NvidiaNimProvider(
            url=settings.nvidia_url,
            model=settings.nvidia_model,
            api_key_file=settings.nvidia_api_key_file,
            timeout_seconds=settings.nvidia_timeout_seconds,
        )
    if settings.ollama_cloud_enabled:
        configured["ollama_cloud"] = OllamaProvider(
            name="ollama_cloud",
            url=settings.ollama_cloud_url,
            model=settings.ollama_cloud_model,
            api_key_file=settings.ollama_cloud_api_key_file,
            context_tokens=settings.context_tokens,
        )
    if settings.ollama_enabled:
        configured["ollama"] = OllamaProvider(
            name="ollama",
            url=settings.ollama_url,
            model=settings.ollama_model,
            context_tokens=settings.context_tokens,
        )

    providers = []
    for raw_name in settings.provider_order:
        name = raw_name.strip().lower().replace("-", "_")
        provider = configured.pop(name, None)
        if provider is not None:
            providers.append(provider)
    providers.extend(configured.values())
    return FallbackLlmClient(providers)


def _read_secret(path: str) -> str:
    if not path:
        raise LlmProviderError("API key file is not configured")
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LlmProviderError(f"cannot read API key file {path}") from exc
    if not value:
        raise LlmProviderError(f"API key file {path} is empty")
    return value


def _request_json(
    url: str,
    payload: dict[str, object],
    headers: dict[str, str],
    *,
    timeout: int,
) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(2):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                retry_after = exc.headers.get("Retry-After", "1")
                try:
                    delay = min(max(float(retry_after), 0.0), 3.0)
                except ValueError:
                    delay = 1.0
                time.sleep(delay)
                continue
            raise LlmProviderError(f"HTTP {exc.code}") from exc
    raise LlmProviderError("request retry exhausted")
