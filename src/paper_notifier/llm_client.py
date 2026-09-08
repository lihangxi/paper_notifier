from __future__ import annotations

import time
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REASONING_EFFORT,
    DEEPSEEK_THINKING_ENABLED,
    LLM_PROVIDER,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_RETRY_INTERVAL_SECONDS,
    OPENROUTER_RETRY_LIMIT,
    OPENROUTER_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class LlmSettings:
    provider: str
    display_name: str
    api_key: str
    api_key_env: str
    base_url: str
    model: str


def _resolve_settings() -> LlmSettings:
    provider = LLM_PROVIDER.strip().lower()
    if provider in ("", "openrouter"):
        return LlmSettings(
            provider="openrouter",
            display_name="OpenRouter",
            api_key=OPENROUTER_API_KEY,
            api_key_env="OPENROUTER_API_KEY",
            base_url=OPENROUTER_BASE_URL,
            model=OPENROUTER_MODEL,
        )
    if provider == "deepseek":
        return LlmSettings(
            provider="deepseek",
            display_name="DeepSeek",
            api_key=DEEPSEEK_API_KEY,
            api_key_env="DEEPSEEK_API_KEY",
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL,
        )
    raise RuntimeError(
        "LLM_PROVIDER must be 'openrouter' or 'deepseek' (or unset to default to OpenRouter)"
    )


def get_active_provider_name() -> str:
    return _resolve_settings().display_name


def get_active_model() -> str:
    return _resolve_settings().model


def has_active_api_key() -> bool:
    return bool(_resolve_settings().api_key)


def _is_deepseek_model(model_name: object) -> bool:
    if not isinstance(model_name, str):
        return False
    return "deepseek" in model_name.lower()


def _normalize_effort(value: object) -> str:
    if not isinstance(value, str):
        return ""
    lowered = value.strip().lower()
    if lowered in {"high", "max"}:
        return lowered
    return ""


def _status_error_text(exc: APIStatusError) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    text = getattr(response, "text", None)
    if text:
        return str(text)
    return str(response)


def is_exhaustion_message(message: str) -> bool:
    """True when an API error text signals a non-retryable quota/permission limit."""
    lowered = (message or "").lower()
    return any(
        marker in lowered
        for marker in (
            "free-models-per-day",
            "add 10 credits",
            "credits to unlock",
            "insufficient_quota",
            "quota",
            "daily reset",
            "access restricted",
            "status=401",
            "status=403",
        )
    )


def post_chat_completions(payload: dict[str, object], request_name: str) -> dict[str, object]:
    settings = _resolve_settings()
    if not settings.api_key:
        raise RuntimeError(f"{settings.api_key_env} is empty (provider={settings.provider})")

    max_attempts = max(1, OPENROUTER_RETRY_LIMIT)
    retry_interval_seconds = max(1, OPENROUTER_RETRY_INTERVAL_SECONDS)
    timeout_seconds = max(5, OPENROUTER_TIMEOUT_SECONDS)

    request_payload = dict(payload)
    request_payload["model"] = request_payload.get("model") or settings.model

    # Map legacy reasoning payload to DeepSeek thinking-mode request fields.
    reasoning_payload = request_payload.pop("reasoning", None)
    if _is_deepseek_model(request_payload.get("model")):
        thinking_enabled = DEEPSEEK_THINKING_ENABLED
        reasoning_effort = _normalize_effort(DEEPSEEK_REASONING_EFFORT)

        # The official DeepSeek API must not receive per-request reasoning flags
        # (deepseek-chat rejects thinking/effort params); only env settings apply.
        # OpenRouter (and similar gateways) honor the per-request reasoning flags.
        if isinstance(reasoning_payload, dict) and settings.provider != "deepseek":
            if "enabled" in reasoning_payload:
                thinking_enabled = bool(reasoning_payload.get("enabled"))
            effort_override = _normalize_effort(reasoning_payload.get("effort"))
            if effort_override:
                reasoning_effort = effort_override

        # Only send thinking when explicitly enabled. reasoning_effort is sent
        # when configured so gateways/official endpoints that support it (e.g.
        # a reasoning model) honor the requested effort.
        if thinking_enabled:
            extra_body = request_payload.get("extra_body")
            if not isinstance(extra_body, dict):
                extra_body = {}
            extra_body["thinking"] = {"type": "enabled"}
            request_payload["extra_body"] = extra_body
        if reasoning_effort:
            request_payload["reasoning_effort"] = reasoning_effort

    client = OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=timeout_seconds,
        max_retries=0,
    )

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(**request_payload)
            return response.model_dump()
        except APIStatusError as exc:
            if exc.status_code == 429:
                if is_exhaustion_message(_status_error_text(exc)):
                    raise RuntimeError(
                        f"{settings.display_name} request rejected (quota/limit exhausted): "
                        f"{_status_error_text(exc)[:300]}"
                    ) from exc
                if attempt < max_attempts:
                    print(
                        f"[paper-notifier] {settings.display_name} {request_name} hit 429; "
                        f"retrying in {retry_interval_seconds}s "
                        f"({attempt}/{max_attempts})"
                    )
                    time.sleep(retry_interval_seconds)
                    continue
            raise RuntimeError(
                f"{settings.display_name} request failed: status={exc.status_code}, body={exc.response}"
            ) from exc
        except (APIConnectionError, APITimeoutError) as exc:
            if attempt < max_attempts:
                print(
                    f"[paper-notifier] {settings.display_name} {request_name} connection error; "
                    f"retrying in {retry_interval_seconds}s "
                    f"({attempt}/{max_attempts})"
                )
                time.sleep(retry_interval_seconds)
                continue
            raise RuntimeError(f"{settings.display_name} request error: {exc}") from exc

    raise RuntimeError(f"{settings.display_name} request failed after retries")
