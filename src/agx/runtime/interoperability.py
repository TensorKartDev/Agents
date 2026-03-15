"""Cross-agent interoperability helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping, Optional

TOKEN_PATTERN = re.compile(r"\{\{\s*(inputs|results)\.([^.}]+)\.([^\s}]+)\s*\}\}")


def resolve_bindings(
    value: Any,
    *,
    input_store: Mapping[str, Mapping[str, Any]],
    result_store: Mapping[str, Mapping[str, Any]],
) -> Any:
    """Resolve interpolation tokens in nested values."""

    if value is None:
        return None
    if isinstance(value, dict):
        return {
            key: resolve_bindings(item, input_store=input_store, result_store=result_store)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            resolve_bindings(item, input_store=input_store, result_store=result_store)
            for item in value
        ]
    if not isinstance(value, str):
        return value

    match = TOKEN_PATTERN.fullmatch(value.strip())
    if match:
        resolved = _resolve_token(match, input_store=input_store, result_store=result_store)
        if resolved is not None:
            return resolved

    def _replace(token_match: re.Match[str]) -> str:
        resolved = _resolve_token(token_match, input_store=input_store, result_store=result_store)
        if resolved is None:
            return token_match.group(0)
        return str(resolved)

    return TOKEN_PATTERN.sub(_replace, value)


def build_handoff_payload(
    *,
    source_task: str,
    result_store: Mapping[str, Mapping[str, Any]],
    target_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a normalized payload for an agent-to-agent handoff."""

    source_result = dict(result_store.get(source_task, {}))
    output = source_result.get("output")
    payload: Dict[str, Any] = {
        "source_task": source_task,
        "raw_output": output,
        "parsed_output": parse_output_text(output),
    }
    if target_agent:
        payload["target_agent"] = target_agent
    return payload


def parse_output_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text.upper().startswith("FINAL:"):
        text = text[6:].strip()
    try:
        return json.loads(text)
    except Exception:
        return value


def _resolve_token(
    token_match: re.Match[str],
    *,
    input_store: Mapping[str, Mapping[str, Any]],
    result_store: Mapping[str, Mapping[str, Any]],
) -> Any:
    scope = token_match.group(1)
    task_id = token_match.group(2)
    key_path = token_match.group(3)
    root = input_store.get(task_id, {}) if scope == "inputs" else result_store.get(task_id, {})
    return _pluck(root, key_path)


def _pluck(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return None
    return current
