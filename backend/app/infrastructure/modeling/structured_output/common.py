from __future__ import annotations

import json
from typing import Any, Dict

class StructuredOutputParseError(ValueError):
    pass


def _loads(content: str) -> Dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise StructuredOutputParseError(f"Model response is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise StructuredOutputParseError("Model response JSON must be an object.")
    return value
