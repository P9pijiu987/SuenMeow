from __future__ import annotations

import json
from dataclasses import dataclass
import logging

import httpx

from bot.settings import ModelRoute, ProviderConfig


logger = logging.getLogger(__name__)


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

    async def chat(self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> LlmResponse | None:
        route = self.models.get(route_name)
        if route is None:
            return None
        provider = self.providers.get(route.provider)
        if provider is None or not self.is_route_available(route_name):
            return None

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
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Provider failures should degrade to the local fallback path instead of crashing the worker.
            logger.warning("LLM request failed for route %s: %s", route_name, exc)
            return None

        choices = body.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            # Some OpenAI-compatible providers return content parts instead of a flat string.
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            return None
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
