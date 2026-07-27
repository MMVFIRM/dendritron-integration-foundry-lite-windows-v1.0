from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def load_data(path: str | Path) -> Any:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    suffix = file_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".sql", ".ddl", ".graphql", ".gql", ".txt"}:
        return text
    raise ValueError(f"Unsupported file format: {file_path.suffix}")


def load_model(path: str | Path, model_type: type[T]) -> T:
    return model_type.model_validate(load_data(path))


def dump_json(data: Any) -> str:
    if isinstance(data, BaseModel):
        return data.model_dump_json(indent=2)
    return json.dumps(data, indent=2, default=str, sort_keys=True)


def dump_yaml(data: Any) -> str:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
