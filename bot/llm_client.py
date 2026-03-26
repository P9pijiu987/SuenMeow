from __future__ import annotations

import json
from dataclasses import dataclass
import logging

import httpx

from bot.settings import ModelRoute, ProviderConfig


logger = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """Base exception for unrecoverable LLM route failures."""


class LlmRouteUnavailableError(LlmError):
    """Raised when requested route/provider configuration is unavailable."""


class LlmQuotaExhaustedError(LlmError):
    """Raised when provider quota/billing is exhausted."""


class LlmProviderHttpError(LlmError):
    """Raised when provider responds with a non-success status."""


class LlmResponseFormatError(LlmError):
    """Raised when provider response payload is invalid for the expected schema."""


@dataclass(slots=True)
class LlmResponse:
    content: str
    model: str
    provider: str


class LlmClient:
    def __init__(self, providers: dict[str, ProviderConfig], models: dict[str, ModelRoute]) -> None:
        self.providers = providers
        self.models = models

    def describe_route(self, route_name: str) -> dict[str, str] | None:
        route = self.models.get(route_name)
        if route is None:
            return None
        return {
            "route": route_name,
            "provider": route.provider,
            "model": route.model,
        }

    def is_route_available(self, route_name: str) -> bool:
        route = self.models.get(route_name)
        if route is None:
            return False
        provider = self.providers.get(route.provider)
        if provider is None:
            return False
        return bool(provider.api_key and provider.api_key != "replace_me")

    @staticmethod
    def _extract_error_details(payload: object) -> tuple[str, str, str]:
        if not isinstance(payload, dict):
            return "", "", ""
        raw_error = payload.get("error")
        if not isinstance(raw_error, dict):
            return "", "", ""
        code = str(raw_error.get("code") or "").strip().lower()
        err_type = str(raw_error.get("type") or "").strip().lower()
        message = str(raw_error.get("message") or "").strip()
        return code, err_type, message

    async def chat(self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> LlmResponse:
        route = self.models.get(route_name)
        if route is None:
            raise LlmRouteUnavailableError(f"LLM route is not configured: {route_name}")
        provider = self.providers.get(route.provider)
        if provider is None or not self.is_route_available(route_name):
            raise LlmRouteUnavailableError(
                f"LLM provider is unavailable for route '{route_name}' (provider='{route.provider}')"
            )

        payload = {
            "model": route.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                response = await client.post(
                    provider.base_url,
                    headers={
                        "authorization": f"Bearer {provider.api_key}",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise LlmProviderHttpError(f"LLM network request failed for route '{route_name}': {exc}") from exc

        try:
            body: object = response.json()
        except ValueError as exc:
            raise LlmResponseFormatError(
                f"LLM provider returned non-JSON response for route '{route_name}'; status={response.status_code}"
            ) from exc

        if response.status_code >= 400:
            error_code, error_type, error_message = self._extract_error_details(body)
            diagnostic = (
                f"route={route_name} provider={route.provider} model={route.model} status={response.status_code} "
                f"code={error_code or '(none)'} type={error_type or '(none)'} "
                f"message={error_message or '(empty)'}"
            )
            if response.status_code == 429 and (error_code == "insufficient_quota" or error_type == "insufficient_quota"):
                raise LlmQuotaExhaustedError(f"LLM quota exhausted: {diagnostic}")
            raise LlmProviderHttpError(f"LLM provider request failed: {diagnostic}")

        if not isinstance(body, dict):
            raise LlmResponseFormatError(
                f"LLM provider response is not a JSON object for route '{route_name}'; status={response.status_code}"
            )
        choices = body.get("choices") or []
        if not choices:
            raise LlmResponseFormatError(f"LLM provider response has no choices for route '{route_name}'")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            # Some OpenAI-compatible providers return content parts instead of a flat string.
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise LlmResponseFormatError(f"LLM provider response has empty content for route '{route_name}'")
        return LlmResponse(content=content.strip(), model=route.model, provider=route.provider)

    @staticmethod
    def parse_json_object(content: str) -> dict[str, object] | None:
        try:
            parsed: object = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        normalized: dict[str, object] = {}
        for key, value in parsed.items():
            if not isinstance(key, str):
                return None
            normalized[key] = value
        return normalized
