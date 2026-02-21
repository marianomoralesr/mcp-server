"""
Wrapper sobre OpenAI SDK para llamadas LLM con tracing automático.
Usa el SDK openai directamente contra vLLM (OpenAI-compatible).
"""

import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import structlog
from openai import AsyncOpenAI

from app.config import Settings

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------

class LLMService:
    """Servicio centralizado para llamadas LLM via OpenAI SDK."""

    # URL del tunnel Cloudflare fijo (fallback cuando no hay vLLM local)
    TUNNEL_URL = "https://api.trefa.mx"

    def __init__(self, settings: Settings):
        self.settings = settings

        # Determinar api_base: explícita > local > tunnel
        raw_base = settings.vllm_base_url or f"http://{settings.vllm_host}:{settings.vllm_port}"
        self.health_url = raw_base.rstrip("/")
        self.api_base = raw_base.rstrip("/")
        if not self.api_base.endswith("/v1"):
            self.api_base += "/v1"
        self.is_tunnel = self.TUNNEL_URL in self.api_base

        self.api_key = settings.vllm_api_key or "EMPTY"

        # Cliente OpenAI apuntando a vLLM
        self.client = AsyncOpenAI(
            base_url=self.api_base,
            api_key=self.api_key,
            timeout=300.0,
        )

        logger.info(
            "llm_service_initialized",
            api_base=self.api_base,
            is_tunnel=self.is_tunnel,
            has_api_key=bool(self.api_key and self.api_key != "EMPTY"),
        )

    # ------------------------------------------------------------------
    # Chat completions
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> dict:
        """Llamada chat completion (no-streaming). Retorna dict OpenAI-format."""
        start = time.monotonic()
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                **kwargs,
            )
            latency_ms = round((time.monotonic() - start) * 1000)
            usage = response.usage
            logger.info(
                "llm_call_success",
                model=model,
                latency_ms=latency_ms,
                tokens_prompt=usage.prompt_tokens if usage else 0,
                tokens_completion=usage.completion_tokens if usage else 0,
            )
            return response.model_dump()
        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000)
            logger.error("llm_call_failure", model=model, latency_ms=latency_ms, error=str(e))
            raise

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Llamada chat completion streaming. Genera líneas SSE."""
        stream = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        async for chunk in stream:
            yield f"data: {chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    # ------------------------------------------------------------------
    # Text completions (legacy)
    # ------------------------------------------------------------------

    async def text_completion(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> dict:
        """Llamada text completion (no-streaming). Retorna dict OpenAI-format."""
        start = time.monotonic()
        try:
            response = await self.client.completions.create(
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                **kwargs,
            )
            latency_ms = round((time.monotonic() - start) * 1000)
            logger.info("llm_call_success", model=model, latency_ms=latency_ms)
            return response.model_dump()
        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000)
            logger.error("llm_call_failure", model=model, latency_ms=latency_ms, error=str(e))
            raise

    async def text_completion_stream(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Llamada text completion streaming. Genera líneas SSE."""
        stream = await self.client.completions.create(
            model=model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        async for chunk in stream:
            yield f"data: {chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
