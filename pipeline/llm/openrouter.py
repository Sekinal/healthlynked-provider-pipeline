"""Thin OpenRouter client (OpenAI-compatible) with structured JSON output.

Centralizes the call so reconciliation + bakeoff share retry, JSON parsing, and
token/cost accounting. Prices are per-1M tokens (in, out) for the cost ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from openai import OpenAI

from pipeline.config import settings

# Per-1M-token (input, output) USD. Used for cost accounting + bakeoff.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "deepseek/deepseek-v4-flash": (0.09, 0.18),
    "stepfun/step-3.7-flash": (0.20, 1.15),
    "google/gemini-3.1-flash-lite": (0.25, 1.50),
    "minimax/minimax-m3": (0.30, 1.20),
    "z-ai/glm-5.2": (0.98, 3.08),
}


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    prompt_tokens: int
    completion_tokens: int
    usd: float
    raw: str


def _client() -> OpenAI:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    return OpenAI(base_url=settings.openrouter_base_url,
                  api_key=settings.openrouter_api_key)


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = MODEL_PRICES.get(model, (0.0, 0.0))
    return prompt_tokens / 1e6 * pin + completion_tokens / 1e6 * pout


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def complete_json(system: str, user: str, model: Optional[str] = None,
                  temperature: float = 0.0) -> LLMResult:
    model = model or settings.llm_tier1_model
    client = _client()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    usage = resp.usage
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    try:
        data = _extract_json(raw)
    except Exception:
        data = {}
    return LLMResult(data=data, model=model, prompt_tokens=pt,
                     completion_tokens=ct, usd=cost_for(model, pt, ct), raw=raw)
