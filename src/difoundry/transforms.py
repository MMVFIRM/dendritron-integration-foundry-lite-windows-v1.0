from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

Transform = Callable[[Any, dict[str, Any], dict[str, Any]], Any]


class TransformRegistry:
    def __init__(self) -> None:
        self._transforms: dict[str, Transform] = {}
        self._register_builtins()

    def register(self, name: str, transform: Transform) -> None:
        if not name:
            raise ValueError("Transform name cannot be empty")
        self._transforms[name] = transform

    def apply(self, value: Any, specification: str | dict[str, Any], context: dict[str, Any]) -> Any:
        if isinstance(specification, str):
            name, config = specification, {}
        else:
            name = str(specification.get("name", ""))
            config = dict(specification.get("config", {}))
        try:
            transform = self._transforms[name]
        except KeyError as exc:
            raise KeyError(f"Unknown transform: {name!r}") from exc
        return transform(value, config, context)

    def _register_builtins(self) -> None:
        self.register("strip", lambda value, _c, _x: value.strip() if isinstance(value, str) else value)
        self.register("upper", lambda value, _c, _x: value.upper() if isinstance(value, str) else value)
        self.register("lower", lambda value, _c, _x: value.lower() if isinstance(value, str) else value)
        self.register("to_string", lambda value, _c, _x: "" if value is None else str(value))
        self.register("to_int", lambda value, _c, _x: int(value) if value is not None else None)
        self.register("to_float", lambda value, _c, _x: float(value) if value is not None else None)
        self.register("default", lambda value, config, _x: config.get("value") if value in (None, "") else value)
        self.register("prefix", lambda value, config, _x: f"{config.get('value', '')}{value}")
        self.register("suffix", lambda value, config, _x: f"{value}{config.get('value', '')}")
        self.register("replace", lambda value, config, _x: str(value).replace(str(config.get("old", "")), str(config.get("new", ""))))
        self.register("join", self._join)
        self.register("template", self._template)
        self.register("iso_datetime", self._iso_datetime)

    @staticmethod
    def _join(value: Any, config: dict[str, Any], _context: dict[str, Any]) -> str:
        separator = str(config.get("separator", ","))
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            return separator.join(str(item) for item in value)
        return str(value)

    @staticmethod
    def _template(value: Any, config: dict[str, Any], context: dict[str, Any]) -> str:
        template = str(config.get("value", "{value}"))
        safe_context = {"value": value, **context}
        return template.format_map(_SafeDict(safe_context))

    @staticmethod
    def _iso_datetime(value: Any, _config: dict[str, Any], _context: dict[str, Any]) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
