"""LLM client: OpenAI-compatible HTTP implementation and a deterministic mock."""

from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from app.contracts import DetectedException, Fix, FixAction

from .settings import AISettings


class LLMError(Exception):
    """The LLM call failed (network, HTTP error, unusable response)."""


class LLMTimeout(LLMError):
    """The LLM call timed out."""


class LLM(Protocol):
    model_name: str

    def complete(self, system: str, user: str) -> str:
        """Return the raw model text (expected to be a JSON object)."""
        ...


class OpenAICompatibleLLM:
    """Works with any OpenAI-compatible endpoint (hosted open-weight model, Ollama /v1)."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 20.0):
        if not base_url or not model:
            raise ValueError("LLM_BASE_URL and LLM_MODEL must be set")
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException as e:
            raise LLMTimeout(str(e)) from e
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e


MOCK_MODES = ("ok", "invalid_json", "timeout", "bad_citation", "low_confidence")
_EXC_RE = re.compile(r"<exception>\s*(.*?)\s*</exception>", re.DOTALL)


class MockLLM:
    """Deterministic fake. Reads the exception from the prompt and answers from its
    baseline_fix and first policy ref, so tests need no network."""

    def __init__(self, mode: str = "ok", model_name: str = "mock-llm"):
        if mode not in MOCK_MODES:
            raise ValueError(f"unknown mock mode {mode!r}; expected one of {MOCK_MODES}")
        self.mode = mode
        self.model_name = model_name
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if self.mode == "timeout":
            raise LLMTimeout("mock timeout")
        if self.mode == "invalid_json":
            return "this is not json {"
        m = _EXC_RE.search(user)
        if not m:
            raise LLMError("mock could not find <exception> in prompt")
        exc = DetectedException.model_validate_json(m.group(1))
        fix = exc.baseline_fix or Fix(action=FixAction.FLAG_FOR_MANAGER, changes=[])
        clause = "POL-TA-99" if self.mode == "bad_citation" else exc.policy_refs[0]
        confidence = 0.2 if self.mode == "low_confidence" else 0.9
        return json.dumps(
            {
                "fix": fix.model_dump(mode="json"),
                "rationale": f"Applying {clause} to resolve the {exc.type.value} exception.",
                "cited_clause_ids": [clause],
                "confidence": confidence,
            }
        )


def build_llm(settings: AISettings | None = None) -> LLM:
    s = settings or AISettings.from_env()
    if s.llm_provider == "mock":
        return MockLLM(mode=s.llm_mock_mode, model_name=s.llm_model or "mock-llm")
    if s.llm_provider in ("openai_compatible", "ollama"):
        return OpenAICompatibleLLM(
            s.llm_base_url, s.llm_model, s.llm_api_key, s.llm_timeout_seconds
        )
    raise ValueError(f"unknown LLM_PROVIDER {s.llm_provider!r}")
