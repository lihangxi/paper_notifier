from __future__ import annotations

import json
import time

import requests

from .config import (
    OPENROUTER_API_KEY,
    OPENROUTER_RETRY_INTERVAL_SECONDS,
    OPENROUTER_RETRY_LIMIT,
    OPENROUTER_TIMEOUT_SECONDS,
)


def post_chat_completions(payload: dict[str, object], request_name: str) -> dict[str, object]:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is empty")

    max_attempts = max(1, OPENROUTER_RETRY_LIMIT)
    retry_interval_seconds = max(1, OPENROUTER_RETRY_INTERVAL_SECONDS)

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=max(5, OPENROUTER_TIMEOUT_SECONDS),
            )

            if response.status_code == 429 and attempt < max_attempts:
                print(
                    f"[paper-notifier] OpenRouter {request_name} hit 429; "
                    f"retrying in {retry_interval_seconds}s "
                    f"({attempt}/{max_attempts})"
                )
                time.sleep(retry_interval_seconds)
                continue

            response.raise_for_status()

            try:
                body = response.json()
            except ValueError as exc:
                raise RuntimeError("OpenRouter response is not valid JSON") from exc
            return body
        except requests.RequestException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 429 and attempt < max_attempts:
                print(
                    f"[paper-notifier] OpenRouter {request_name} hit 429; "
                    f"retrying in {retry_interval_seconds}s "
                    f"({attempt}/{max_attempts})"
                )
                time.sleep(retry_interval_seconds)
                continue
            raise RuntimeError(f"OpenRouter request error: {exc}") from exc

    raise RuntimeError("OpenRouter request failed after retries")
