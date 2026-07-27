from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class SparseFeatureEncoder:
    """Protocol-neutral event encoder for Dendritron routing tissue.

    The encoder intentionally produces inspectable sparse features rather than
    opaque embeddings. Exact values, existence, string tokens, collection size,
    and logarithmic numeric buckets are represented independently.
    """

    def __init__(self, max_depth: int = 8, max_collection_items: int = 16) -> None:
        self.max_depth = max_depth
        self.max_collection_items = max_collection_items

    def encode(self, context: Mapping[str, Any]) -> dict[str, float]:
        features: dict[str, float] = {}
        self._walk(context, "", 0, features)
        return features

    def _walk(self, value: Any, path: str, depth: int, features: dict[str, float]) -> None:
        if depth > self.max_depth:
            return
        if path:
            features[f"exists:{path}"] = 1.0
        if isinstance(value, Mapping):
            features[f"kind:{path or '$'}=object"] = 1.0
            for key in sorted(value, key=str):
                child = f"{path}.{key}" if path else str(key)
                self._walk(value[key], child, depth + 1, features)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            features[f"kind:{path}=array"] = 1.0
            features[f"length:{path}={min(len(value), self.max_collection_items)}"] = 1.0
            for item in list(value)[: self.max_collection_items]:
                self._add_scalar(item, f"{path}[]", features)
            return
        self._add_scalar(value, path, features)

    def _add_scalar(self, value: Any, path: str, features: dict[str, float]) -> None:
        if value is None:
            features[f"value:{path}=<null>"] = 1.0
            return
        if isinstance(value, bool):
            features[f"value:{path}={str(value).lower()}"] = 1.0
            return
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            features[f"kind:{path}=number"] = 1.0
            if math.isfinite(number):
                sign = "zero" if number == 0 else "positive" if number > 0 else "negative"
                features[f"sign:{path}={sign}"] = 1.0
                magnitude = 0 if number == 0 else int(math.floor(math.log10(abs(number))))
                features[f"magnitude:{path}=1e{magnitude}"] = 1.0
                if float(number).is_integer() and abs(number) <= 10000:
                    features[f"value:{path}={int(number)}"] = 1.0
            return
        text = str(value).strip().casefold()
        features[f"kind:{path}=string"] = 1.0
        features[f"value:{path}={text}"] = 1.0
        for token in _TOKEN_RE.findall(text)[:16]:
            if len(token) >= 2:
                features[f"token:{path}={token}"] = 1.0


def weighted_jaccard(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    numerator = sum(min(max(left.get(key, 0.0), 0.0), max(right.get(key, 0.0), 0.0)) for key in keys)
    denominator = sum(max(max(left.get(key, 0.0), 0.0), max(right.get(key, 0.0), 0.0)) for key in keys)
    return 0.0 if denominator == 0.0 else numerator / denominator
