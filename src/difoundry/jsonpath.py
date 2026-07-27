from __future__ import annotations

from typing import Any

_MISSING = object()


def get_path(data: Any, path: str, default: Any = _MISSING) -> Any:
    if path in {"", "$", "."}:
        return data
    normalized = path.removeprefix("$.").removeprefix(".")
    current = data
    for token in normalized.split("."):
        if token == "":
            continue
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        elif default is not _MISSING:
            return default
        else:
            raise KeyError(path)
    return current


def set_path(data: dict[str, Any], path: str, value: Any) -> None:
    normalized = path.removeprefix("$.").removeprefix(".")
    tokens = [token for token in normalized.split(".") if token]
    if not tokens:
        if not isinstance(value, dict):
            raise ValueError("Root assignment requires a dictionary")
        data.clear()
        data.update(value)
        return
    current = data
    for token in tokens[:-1]:
        next_value = current.get(token)
        if not isinstance(next_value, dict):
            next_value = {}
            current[token] = next_value
        current = next_value
    current[tokens[-1]] = value
