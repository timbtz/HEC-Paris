"""Pure helpers for the Cerebras (OpenAI-compat) runner.

Split out of `pydantic_ai_runner.py` so the schema-translation and
response-parsing logic stays unit-testable without a network round-trip.

Source: CEREBRAS_STACK_REFERENCE.md §5 (strict-mode requirements:
`additionalProperties:false` recursive injection), §7 (submit-tool
pattern), §10 (decision-trace fields incl. `reasoning_tokens`,
`response_id`, `finish_reason`).

Three helpers, no I/O, no async:
- ``translate_tool_schema`` — Anthropic shape -> OpenAI shape, recursive
  ``additionalProperties:false`` injection.
- ``translate_tool_choice`` — name -> ``{"type":"function","function":{"name":...}}``.
- ``parse_response`` — Cerebras chat-completion -> AgentResult-shape dict.
"""
from __future__ import annotations

import json
from typing import Any


def _inject_additional_properties_false(schema: Any) -> Any:
    """Walk a JSONSchema fragment and force ``additionalProperties:false``
    on every ``type:"object"`` node.

    Recursion descends into:
      - ``properties`` (every value, regardless of nominal type)
      - ``items`` (arrays may hold nested objects)
      - tuple-typed ``items`` lists (some schemas use positional tuples)

    Does NOT mutate ``enum`` lists, scalars, or ``null`` schemas.
    Returns a shallow-copied tree so callers can keep their originals.
    """
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: _inject_additional_properties_false(pv) for pk, pv in v.items()}
            elif k == "items":
                if isinstance(v, list):
                    out[k] = [_inject_additional_properties_false(it) for it in v]
                else:
                    out[k] = _inject_additional_properties_false(v)
            elif k == "enum":
                out[k] = list(v) if isinstance(v, list) else v
            else:
                out[k] = _inject_additional_properties_false(v)
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_inject_additional_properties_false(it) for it in schema]
    return schema


def translate_tool_schema(anthropic_tool: dict) -> dict:
    """Convert {name, description, input_schema} -> OpenAI {type, function}.

    - Wraps in ``{"type": "function", "function": {...}}``.
    - Renames ``input_schema`` -> ``parameters``.
    - Sets ``strict: True`` on the function (Cerebras constrained decoding).
    - Recursively injects ``additionalProperties:false`` into every
      ``type:object`` schema node.
    """
    name = anthropic_tool["name"]
    description = anthropic_tool.get("description", "")
    parameters = _inject_additional_properties_false(
        anthropic_tool.get("input_schema") or {"type": "object", "properties": {}}
    )
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
            "strict": True,
        },
    }


def translate_tool_choice(submit_tool_name: str) -> dict:
    """OpenAI/Cerebras ``tool_choice`` for a forced submit tool."""
    return {"type": "function", "function": {"name": submit_tool_name}}


def _confidence_from_output(parsed: Any) -> float | None:
    if isinstance(parsed, dict):
        c = parsed.get("confidence")
        if isinstance(c, (int, float)):
            return float(c)
    return None


def _alternatives_from_output(parsed: Any) -> list[dict] | None:
    if isinstance(parsed, dict):
        alt = parsed.get("alternatives")
        if isinstance(alt, list):
            return alt
    return None


def _usage_dict(usage_obj: Any) -> dict[str, int]:
    """Map an OpenAI/Cerebras ``usage`` payload to our dict shape.

    Cerebras follows the OpenAI shape:
      - ``prompt_tokens`` / ``completion_tokens`` at the top.
      - ``prompt_tokens_details.cached_tokens`` for cache reads.
      - ``completion_tokens_details.reasoning_tokens`` on reasoning models.

    All sub-objects may be absent on non-reasoning models.
    """
    if usage_obj is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }

    prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
    completion_details = getattr(usage_obj, "completion_tokens_details", None)
    cache_read = getattr(prompt_details, "cached_tokens", 0) if prompt_details is not None else 0
    reasoning = (
        getattr(completion_details, "reasoning_tokens", 0)
        if completion_details is not None
        else 0
    )

    return {
        "input_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
        "cache_read_tokens": cache_read or 0,
        "cache_write_tokens": 0,  # Cerebras has no manual cache write.
        "reasoning_tokens": reasoning or 0,
    }


def parse_response(resp: Any) -> dict:
    """Extract our standard dict shape from a Cerebras chat-completion response.

    Returns the same dict ``PydanticAiRunner.run()`` consumes
    (keys: output, model, response_id, alternatives, confidence, usage,
    finish_reason).

    Failure modes:
    - ``tool_calls[0].function.name`` does not start with ``submit`` ->
      ``output=None, finish_reason="tool_name_mismatch"``.
    - ``tool_calls[0].function.arguments`` fails ``json.loads`` ->
      ``output=None, finish_reason="tool_call_parse_error"``.
    """
    model = getattr(resp, "model", "") or ""
    response_id = getattr(resp, "id", None)
    usage = _usage_dict(getattr(resp, "usage", None))

    choices = getattr(resp, "choices", None) or []
    if not choices:
        return {
            "output": None,
            "model": model,
            "response_id": response_id,
            "alternatives": None,
            "confidence": None,
            "usage": usage,
            "finish_reason": "no_choices",
        }

    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    message = getattr(choice, "message", None)

    tool_calls = getattr(message, "tool_calls", None) if message is not None else None
    if tool_calls:
        first = tool_calls[0]
        fn = getattr(first, "function", None)
        fn_name = getattr(fn, "name", "") or ""
        fn_args = getattr(fn, "arguments", "") or ""

        if not fn_name.startswith("submit"):
            return {
                "output": None,
                "model": model,
                "response_id": response_id,
                "alternatives": None,
                "confidence": None,
                "usage": usage,
                "finish_reason": "tool_name_mismatch",
            }
        try:
            parsed = json.loads(fn_args) if fn_args else {}
        except (ValueError, TypeError):
            return {
                "output": None,
                "model": model,
                "response_id": response_id,
                "alternatives": None,
                "confidence": None,
                "usage": usage,
                "finish_reason": "tool_call_parse_error",
            }

        return {
            "output": parsed,
            "model": model,
            "response_id": response_id,
            "alternatives": _alternatives_from_output(parsed),
            "confidence": _confidence_from_output(parsed),
            "usage": usage,
            "finish_reason": finish_reason or "tool_calls",
        }

    text = getattr(message, "content", None) if message is not None else None
    return {
        "output": text,
        "model": model,
        "response_id": response_id,
        "alternatives": None,
        "confidence": None,
        "usage": usage,
        "finish_reason": finish_reason or "stop",
    }
