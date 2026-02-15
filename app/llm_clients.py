"""
llm_clients.py — Clientes async para Anthropic Claude y Google Gemini.
Import lazy: si las dependencias no están instaladas, el servidor inicia normalmente
y solo falla al intentar usar esos endpoints.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class LLMUsage:
    tokens_input: int = 0
    tokens_output: int = 0
    calls: int = 0
    errors: int = 0


def parsear_json_respuesta(texto: str) -> Optional[dict]:
    """Intenta extraer JSON de una respuesta LLM (puede venir con markdown fences)."""
    texto = texto.strip()
    # Buscar bloque ```json ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", texto, re.DOTALL)
    if m:
        texto = m.group(1).strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        # Intentar encontrar { ... } o [ ... ]
        for start, end in [("{", "}"), ("[", "]")]:
            i = texto.find(start)
            j = texto.rfind(end)
            if i != -1 and j > i:
                try:
                    return json.loads(texto[i : j + 1])
                except json.JSONDecodeError:
                    continue
        return None


class AnthropicClient:
    """Cliente async para Anthropic Claude con retry y rate-limit handling."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        self.api_key = api_key
        self.model = model
        self.usage = LLMUsage()
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise RuntimeError(
                    "El paquete 'anthropic' no está instalado. "
                    "Ejecuta: pip install anthropic>=0.40.0"
                )
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.9,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> Optional[str]:
        """Genera texto con Claude. Retorna None si falla tras reintentos."""
        client = self._get_client()
        messages = [{"role": "user", "content": prompt}]

        for attempt in range(max_retries):
            try:
                kwargs = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": messages,
                }
                if system:
                    kwargs["system"] = system

                response = await client.messages.create(**kwargs)

                self.usage.calls += 1
                self.usage.tokens_input += response.usage.input_tokens
                self.usage.tokens_output += response.usage.output_tokens

                text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text += block.text
                return text

            except Exception as e:
                err_str = str(e).lower()
                self.usage.errors += 1

                if "rate" in err_str or "429" in err_str:
                    wait = min(2 ** (attempt + 1), 30)
                    logger.warning(
                        "anthropic_rate_limit", attempt=attempt + 1, wait=wait
                    )
                    await asyncio.sleep(wait)
                elif "overloaded" in err_str or "529" in err_str:
                    wait = min(5 * (attempt + 1), 60)
                    logger.warning(
                        "anthropic_overloaded", attempt=attempt + 1, wait=wait
                    )
                    await asyncio.sleep(wait)
                elif attempt < max_retries - 1:
                    logger.warning(
                        "anthropic_error", attempt=attempt + 1, error=str(e)
                    )
                    await asyncio.sleep(1)
                else:
                    logger.error(
                        "anthropic_failed", attempts=max_retries, error=str(e)
                    )
                    return None
        return None

    def get_usage(self) -> dict:
        return {
            "calls": self.usage.calls,
            "tokens_input": self.usage.tokens_input,
            "tokens_output": self.usage.tokens_output,
            "errors": self.usage.errors,
            "model": self.model,
        }


class GeminiClient:
    """Cliente async para Google Gemini con retry y rate-limit handling."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self.usage = LLMUsage()
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise RuntimeError(
                    "El paquete 'google-genai' no está instalado. "
                    "Ejecuta: pip install google-genai>=1.0.0"
                )
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.8,
        response_mime: str = "application/json",
        max_retries: int = 3,
    ) -> Optional[str]:
        """Genera texto con Gemini. Retorna None si falla tras reintentos."""
        client = self._get_client()

        for attempt in range(max_retries):
            try:
                from google.genai import types

                config = types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type=response_mime,
                )

                # google-genai SDK is sync, run in thread
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=prompt,
                    config=config,
                )

                self.usage.calls += 1
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    um = response.usage_metadata
                    self.usage.tokens_input += getattr(um, "prompt_token_count", 0) or 0
                    self.usage.tokens_output += getattr(um, "candidates_token_count", 0) or 0

                return response.text

            except Exception as e:
                err_str = str(e).lower()
                self.usage.errors += 1

                if "429" in err_str or "resource_exhausted" in err_str:
                    wait = min(5 * (attempt + 1), 60)
                    logger.warning(
                        "gemini_rate_limit", attempt=attempt + 1, wait=wait
                    )
                    await asyncio.sleep(wait)
                elif attempt < max_retries - 1:
                    logger.warning(
                        "gemini_error", attempt=attempt + 1, error=str(e)
                    )
                    await asyncio.sleep(2)
                else:
                    logger.error(
                        "gemini_failed", attempts=max_retries, error=str(e)
                    )
                    return None
        return None

    def get_usage(self) -> dict:
        return {
            "calls": self.usage.calls,
            "tokens_input": self.usage.tokens_input,
            "tokens_output": self.usage.tokens_output,
            "errors": self.usage.errors,
            "model": self.model,
        }
