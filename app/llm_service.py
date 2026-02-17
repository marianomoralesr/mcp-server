"""
Wrapper sobre LiteLLM para llamadas LLM con tracing automático.
Reemplaza las llamadas httpx directas con litellm SDK.
"""

import os
from typing import Any, AsyncGenerator, Dict, List, Optional

import litellm
import structlog

from app.config import Settings

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Callbacks de tracing (structlog)
# ---------------------------------------------------------------------------

def _log_success(kwargs, response_obj, start_time, end_time):
    """Callback ejecutado después de cada llamada LLM exitosa."""
    latency_ms = round((end_time - start_time).total_seconds() * 1000)
    usage = getattr(response_obj, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

    try:
        cost = litellm.completion_cost(completion_response=response_obj)
    except Exception:
        cost = None

    logger.info(
        "llm_call_success",
        model=kwargs.get("model"),
        latency_ms=latency_ms,
        tokens_prompt=prompt_tokens,
        tokens_completion=completion_tokens,
        cost=cost,
    )


def _log_failure(kwargs, exception, start_time, end_time):
    """Callback ejecutado cuando una llamada LLM falla."""
    latency_ms = round((end_time - start_time).total_seconds() * 1000)
    logger.error(
        "llm_call_failure",
        model=kwargs.get("model"),
        latency_ms=latency_ms,
        error=str(exception),
    )


# Registrar callbacks globales
litellm.success_callback = [_log_success]
litellm.failure_callback = [_log_failure]

# Langfuse opcional: si las env vars están definidas, litellm lo detecta
if os.environ.get("LANGFUSE_PUBLIC_KEY"):
    litellm.success_callback.append("langfuse")
    litellm.failure_callback.append("langfuse")
    logger.info("langfuse_callback_enabled")


# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------

class LLMService:
    """Servicio centralizado para llamadas LLM via LiteLLM."""

    # URL del tunnel Cloudflare fijo (fallback cuando no hay vLLM local)
    TUNNEL_URL = "https://api.trefa.mx"

    def __init__(self, settings: Settings):
        self.settings = settings

        # Determinar api_base: explícita > local > tunnel
        self.api_base = settings.vllm_base_url or f"http://{settings.vllm_host}:{settings.vllm_port}"
        self.is_tunnel = self.TUNNEL_URL in self.api_base

        # Detectar provider
        provider = settings.litellm_provider
        if not provider:
            if "together" in self.api_base.lower():
                provider = "together_ai"
            else:
                provider = "openai"

        self.provider = provider
        self.api_key = settings.vllm_api_key

        # LiteLLM: desactivar logs internos excesivos
        litellm.set_verbose = False

        logger.info(
            "llm_service_initialized",
            provider=self.provider,
            api_base=self.api_base,
            is_tunnel=self.is_tunnel,
            has_api_key=bool(self.api_key),
        )

    def _model_name(self, model: str) -> str:
        """Construye el model string para litellm: 'provider/model'."""
        return f"{self.provider}/{model}"

    def _common_kwargs(self, model: str, **extra) -> dict:
        """Kwargs comunes para todas las llamadas litellm."""
        kwargs = {
            "model": self._model_name(model),
            "api_key": self.api_key,
            "api_base": self.api_base,
            "timeout": 300.0,
        }
        kwargs.update(extra)
        return kwargs

    # ------------------------------------------------------------------
    # Chat completions
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> dict:
        """Llamada chat completion (no-streaming). Retorna dict OpenAI-format."""
        params = self._common_kwargs(
            model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )
        response = await litellm.acompletion(**params)
        return response.model_dump()

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Llamada chat completion streaming. Genera líneas SSE."""
        params = self._common_kwargs(
            model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        response = await litellm.acompletion(**params)
        async for chunk in response:
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
        max_tokens: int = 1024,
        **kwargs,
    ) -> dict:
        """Llamada text completion (no-streaming). Retorna dict OpenAI-format."""
        params = self._common_kwargs(
            model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )
        response = await litellm.atext_completion(**params)
        return response.model_dump()

    async def text_completion_stream(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Llamada text completion streaming. Genera líneas SSE."""
        params = self._common_kwargs(
            model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        response = await litellm.atext_completion(**params)
        async for chunk in response:
            yield f"data: {chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
