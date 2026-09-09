# SPDX-License-Identifier: MIT

"""Any model that speaks the OpenAI chat API - which is nearly all of them.

**One protocol, no favourite provider.** Backends tied to a single vendor go
stale, and this file is deliberately not one: `/chat/completions` is the
closest thing this field has to a standard, so a single implementation reaches

- **Ollama and LM Studio**, locally, with no key and no account;
- **OpenRouter, Azure AI Foundry, Together, Groq, vLLM**, and anything else
  with a base URL and a key.

Two names are registered against it. `ollama` is the same code with a local
base URL and no key required, because "run Ollama" should not also mean "read
the documentation for a generic backend".

The model is always the caller's choice. There is no default model here worth
the name: `llama3.1` for Ollama because that is what Ollama calls its own
starting point, and nothing at all for the generic backend, which refuses
rather than guessing at someone's provider.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import WriterError, register


class OpenAICompatible:
    """The shared implementation. Registered twice, with different defaults."""

    name = "openai"
    default_base_url = ""
    default_model = ""
    needs_key = True
    key_env = "TICKET_AI_API_KEY"

    def __init__(
        self,
        *,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        temperature: float = 0.3,
        timeout: float = 180.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("TICKET_AI_BASE_URL") or self.default_base_url
        ).rstrip("/")
        if not self.base_url:
            raise WriterError(
                f"{self.name} needs a base url. Set TICKET_AI_BASE_URL - for example "
                "https://openrouter.ai/api/v1, or http://localhost:11434/v1 for a "
                "local Ollama."
            )

        self.model = model or os.environ.get("TICKET_AI_MODEL") or self.default_model
        if not self.model:
            raise WriterError(
                f"{self.name} needs a model. Set TICKET_AI_MODEL or pass --model. "
                "Run `ticket-ai models` to see what the endpoint offers."
            )

        key = api_key or os.environ.get(self.key_env, "")
        if self.needs_key and not key:
            raise WriterError(
                f"{self.name} needs an API key in {self.key_env}. For a keyless "
                "setup, run a model locally and use --writer ollama."
            )

        self.temperature = temperature
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._client = client or httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout
        )

    def write(self, system: str, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise WriterError(
                f"could not reach {self.base_url}: {exc}. "
                + (
                    "Is the local server running? `ollama serve`."
                    if "localhost" in self.base_url or "127.0.0.1" in self.base_url
                    else "Check the base url and the network."
                )
            ) from exc

        if response.status_code == 404:
            raise WriterError(
                f"{self.base_url} has no model {self.model!r}. "
                "Run `ticket-ai models` to see what it does have"
                + (", or `ollama pull <model>`." if "11434" in self.base_url else ".")
            )
        if response.status_code == 429:
            raise WriterError(f"{self.base_url} is rate limiting. Wait, or use a smaller model.")
        if response.status_code in (401, 403):
            raise WriterError(f"{self.base_url} rejected the key in {self.key_env}.")
        if response.status_code >= 400:
            raise WriterError(f"{response.status_code} from {self.base_url}: {response.text[:300]}")

        try:
            return (response.json()["choices"][0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise WriterError(f"unexpected answer from {self.base_url}: {exc}") from exc

    def models(self) -> list[str]:
        """Whatever `/models` reports, which most of these endpoints serve."""
        try:
            response = self._client.get("/models")
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        try:
            body = response.json()
        except ValueError:
            return []
        rows = body.get("data") if isinstance(body, dict) else body
        if not isinstance(rows, list):
            return []
        return sorted(
            {
                row.get("id") or row.get("name")
                for row in rows
                if isinstance(row, dict) and (row.get("id") or row.get("name"))
            }
        )


register(OpenAICompatible)


@register
class OllamaWriter(OpenAICompatible):
    """The same protocol, pointed at a model on this machine.

    Separate only so that running a local model needs no configuration beyond
    installing one. No key, no account, no request leaving the laptop - which
    also makes it the option for a board whose tickets should not be sent
    anywhere.
    """

    name = "ollama"
    default_base_url = "http://localhost:11434/v1"
    default_model = "llama3.1"
    needs_key = False
