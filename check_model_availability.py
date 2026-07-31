from __future__ import annotations

import json
import argparse
import os
import socket
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def load_project_env() -> dict[str, str]:
    values = dict(os.environ)
    env_file = ROOT / ".env"
    if not env_file.exists():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def emit(payload: dict[str, object], exit_code: int) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether one configured model can answer a minimal request.")
    parser.add_argument("--model", default="", help="Override LLM_WORK_MODEL without editing .env.")
    args = parser.parse_args()
    env = load_project_env()
    provider = env.get("LLM_PROVIDER", "dashscope").strip()
    model = (args.model or env.get("LLM_WORK_MODEL", "deepseek-v3")).strip()
    api_key = env.get("DASHSCOPE_API_KEY", "").strip()
    base_url = env.get(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).rstrip("/")
    timeout = float(env.get("LLM_TIMEOUT_SECONDS", "30"))
    enable_thinking = env.get("LLM_ENABLE_THINKING", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }

    common = {"provider": provider, "model": model, "base_url": base_url}
    if provider != "dashscope":
        emit({**common, "status": "configuration_error", "error": "unsupported_provider"}, 2)
    if not api_key:
        emit({**common, "status": "configuration_error", "error": "missing_api_key"}, 2)
    if not model:
        emit({**common, "status": "configuration_error", "error": "missing_model"}, 2)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply exactly: OK"}],
        "max_tokens": 8,
    }
    if model.lower().startswith("qwen3"):
        payload["enable_thinking"] = enable_thinking
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        content = str(response_data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        usage = response_data.get("usage", {}) if isinstance(response_data.get("usage"), dict) else {}
        emit(
            {
                **common,
                "status": "available" if content else "invalid_response",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "response_preview": content[:80],
            },
            0 if content else 1,
        )
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        error_type = "authorization_or_model_access" if exc.code in {401, 403, 404} else "rate_limit" if exc.code == 429 else "http_error"
        emit(
            {
                **common,
                "status": "unavailable",
                "error_type": error_type,
                "http_status": exc.code,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": detail,
            },
            1,
        )
    except (TimeoutError, socket.timeout):
        emit({**common, "status": "unavailable", "error_type": "timeout"}, 1)
    except URLError as exc:
        emit({**common, "status": "unavailable", "error_type": "network_error", "error": str(exc.reason)}, 1)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        emit({**common, "status": "unavailable", "error_type": "invalid_response", "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
