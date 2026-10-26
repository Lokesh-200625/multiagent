"""Single LLM client wrapper: key rotation, strict JSON schema, temperature 0."""
from __future__ import annotations

import json
import logging
from typing import Any

from groq import Groq

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised when every configured LLM key failed or none are configured."""


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Force Groq strict-mode requirements recursively:
    additionalProperties=false + required = all properties, at every level."""
    if not isinstance(schema, dict):
        return schema

    schema = dict(schema)

    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        schema["additionalProperties"] = False
        schema["required"] = list(properties.keys())
        schema["properties"] = {
            key: strict_schema(value)
            for key, value in properties.items()
        }

    if "items" in schema:
        schema["items"] = strict_schema(schema["items"])

    if "$defs" in schema and isinstance(schema["$defs"], dict):
        schema["$defs"] = {
            key: strict_schema(value)
            for key, value in schema["$defs"].items()
        }

    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema and isinstance(schema[key], list):
            schema[key] = [strict_schema(item) for item in schema[key]]

    return schema


def _clients() -> list[Groq]:
    keys = [settings.groq_api_key_1, settings.groq_api_key_2]
    return [Groq(api_key=key.strip()) for key in keys if key and key.strip()]


def complete_json(
    *,
    system: str,
    payload: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    """One strict-JSON completion. Rotates keys on failure.
    Raises LLMUnavailable when no key works."""
    clients = _clients()

    if not clients:
        raise LLMUnavailable("no Groq API keys configured")

    body = strict_schema(schema)
    last_error: Exception | None = None

    for index, client in enumerate(clients, start=1):
        try:
            response = client.chat.completions.create(
                model=model or settings.groq_model,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": body,
                    },
                },
            )

            content = response.choices[0].message.content

            if not content:
                raise RuntimeError("LLM returned an empty response")

            parsed = json.loads(content)

            if not isinstance(parsed, dict):
                raise RuntimeError("LLM returned a non-object JSON payload")

            return parsed

        except Exception as exc:  # noqa: BLE001 - rotate keys on any failure
            last_error = exc
            logger.warning(
                "LLM key %d failed: %s: %s",
                index,
                type(exc).__name__,
                exc,
            )
            if index < len(clients):
                continue

    raise LLMUnavailable(
        f"all configured Groq keys failed: {last_error}"
    ) from last_error
