"""
runtime/llm.py — the ONLY module that talks to the Claude API.

Everything above this file sees a plain `LLM.complete(...)` call that returns
an `LLMResult`. That keeps three things swappable without touching the rest:
  * the SDK (or a Rust port of this file),
  * test fakes (tests/fakes.py implements the same method),
  * per-call accounting (tokens, cache hits) that the router and budget use.

Refs:
  Messages API      https://docs.claude.com/en/api/messages
  Tool use          https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview
  Prompt caching    https://docs.claude.com/en/docs/build-with-claude/prompt-caching
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# Cache reads are billed at a fraction of normal input. We weight them when
# counting a learner's daily budget. Verify the current ratio on the pricing page.
CACHE_READ_WEIGHT = 0.1


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResult:
    model: str
    text: str                          # concatenated text blocks
    tool_calls: list[ToolCall]         # tool_use blocks, if any
    stop_reason: str
    input_tokens: int = 0              # UNcached input tokens
    output_tokens: int = 0
    cache_read_tokens: int = 0         # served from the prompt cache (cheap)
    cache_write_tokens: int = 0        # written to the prompt cache (slightly dearer than input)
    raw_content: list[dict] = field(default_factory=list)  # assistant blocks, replayed in a tool round

    @property
    def budget_tokens(self) -> int:
        """Tokens counted against the learner's daily budget (cache reads discounted)."""
        return (self.input_tokens + self.cache_write_tokens + self.output_tokens
                + int(self.cache_read_tokens * CACHE_READ_WEIGHT))


class LLM(Protocol):
    """What the rest of the harness needs from a model provider. Nothing more."""
    def complete(self, *, model: str, system: list[dict], messages: list[dict], max_tokens: int,
                 tools: list[dict] | None = None, tool_choice: dict | None = None) -> LLMResult: ...


class ClaudeClient:
    """Thin wrapper over the official Anthropic Python SDK."""

    def __init__(self, api_key: str, timeout_s: float = 30.0, max_retries: int = 2):
        # Imported here, not at module top, so tests and the context layer never need the SDK.
        import anthropic
        # The SDK already retries 429/5xx/connection errors with exponential backoff;
        # we only bound how many times and how long, so a child never waits forever.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=max_retries)

    def complete(self, *, model, system, messages, max_tokens, tools=None, tool_choice=None) -> LLMResult:
        kwargs: dict[str, Any] = {"model": model, "system": system, "messages": messages, "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        resp = self._client.messages.create(**kwargs)

        texts, calls, raw = [], [], []
        for block in resp.content:
            if block.type == "text":
                texts.append(block.text)
                raw.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input)))
                raw.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})

        usage = resp.usage
        return LLMResult(
            model=resp.model,
            text="".join(texts).strip(),
            tool_calls=calls,
            stop_reason=resp.stop_reason,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            raw_content=raw,
        )


def structured_call(llm: LLM, *, model: str, system: list[dict], user_text: str,
                    tool_schema: dict, max_tokens: int = 400) -> tuple[dict, LLMResult]:
    """
    Get validated JSON out of Claude in ONE round trip by *forcing* a tool.

    `tool_choice={"type": "tool", "name": X}` makes the model answer by filling
    tool X's input schema. We never execute that "tool" — its input IS the
    answer. No parsing of free text, no second call to fix malformed JSON.
    """
    result = llm.complete(
        model=model, system=system, max_tokens=max_tokens,
        messages=[{"role": "user", "content": user_text}],
        tools=[tool_schema], tool_choice={"type": "tool", "name": tool_schema["name"]},
    )
    for call in result.tool_calls:
        if call.name == tool_schema["name"]:
            return call.input, result
    raise ValueError(f"model did not return forced tool {tool_schema['name']!r}")
