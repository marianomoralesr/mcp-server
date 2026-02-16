"""
Motor de orquestación de tool calling.
Detecta <tool_call> en respuestas del LLM, ejecuta via MCP,
reinyecta <tool_response> y repite hasta respuesta final.
"""

import re
import json
import time
import structlog
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.mcp_client import MCPClient

logger = structlog.get_logger()

# Regex para detectar tool calls en formato Qwen
TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)

THINK_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Elimina bloques <think>...</think> de la respuesta."""
    return THINK_PATTERN.sub("", text).strip()


def extract_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extrae tool calls del texto de respuesta del LLM.

    Returns:
        Lista de dicts con keys 'name' y 'arguments'.
    """
    calls = []
    for match in TOOL_CALL_PATTERN.finditer(text):
        try:
            parsed = json.loads(match.group(1))
            if "name" in parsed:
                calls.append({
                    "name": parsed["name"],
                    "arguments": parsed.get("arguments", {}),
                })
        except json.JSONDecodeError:
            logger.warning("tool_call_parse_error", raw=match.group(1))
    return calls


class ToolOrchestrator:
    def __init__(
        self,
        mcp_client: MCPClient,
        vllm_client: httpx.AsyncClient,
        known_tools: List[str],
        max_iterations: int = 5,
    ):
        self.mcp = mcp_client
        self.vllm = vllm_client
        self.known_tools = set(known_tools)
        self.max_iterations = max_iterations

    async def _call_vllm(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> Tuple[str, Dict[str, Any]]:
        """Llama a vLLM y retorna (texto_respuesta, usage)."""
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        resp = await self.vllm.post("/v1/chat/completions", json=payload, timeout=300.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return content, usage

    async def execute_with_tools(
        self,
        messages: List[Dict[str, str]],
        model: str = "trefa-lora",
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """Ejecuta el ciclo completo de tool calling.

        Returns:
            {
                "response": str,           # Respuesta final del LLM
                "tool_calls_executed": [],  # Lista de {name, args, result, duration_ms}
                "iterations": int,
                "usage": {}
            }
        """
        tool_calls_executed = []
        total_usage = {}
        working_messages = list(messages)

        for iteration in range(1, self.max_iterations + 1):
            content, usage = await self._call_vllm(
                working_messages, model, temperature, max_tokens
            )
            total_usage = usage

            tool_calls = extract_tool_calls(content)

            if not tool_calls:
                return {
                    "response": strip_thinking(content),
                    "tool_calls_executed": tool_calls_executed,
                    "iterations": iteration,
                    "usage": total_usage,
                }

            # Agregar respuesta del asistente con tool_calls al historial
            working_messages.append({"role": "assistant", "content": content})

            # Ejecutar cada tool call
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]

                if tool_name not in self.known_tools:
                    logger.warning("unknown_tool_call", tool=tool_name)
                    result = {"error": f"Herramienta desconocida: {tool_name}"}
                    duration_ms = 0
                else:
                    start = time.monotonic()
                    try:
                        result = await self.mcp.call_tool(tool_name, tool_args)
                    except Exception as e:
                        logger.error("tool_call_error", tool=tool_name, error=str(e))
                        result = {"error": str(e)}
                    duration_ms = int((time.monotonic() - start) * 1000)

                tool_calls_executed.append({
                    "name": tool_name,
                    "arguments": tool_args,
                    "result": result,
                    "duration_ms": duration_ms,
                })

                # Reinyectar resultado como tool_response en formato Qwen
                tool_response_text = (
                    f"<tool_response>\n{json.dumps(result, ensure_ascii=False)}\n</tool_response>"
                )
                working_messages.append({"role": "user", "content": tool_response_text})

            logger.info(
                "tool_iteration_complete",
                iteration=iteration,
                tools_called=[tc["name"] for tc in tool_calls],
            )

        # Si se agotaron las iteraciones, una última llamada sin esperar tools
        content, usage = await self._call_vllm(
            working_messages, model, temperature, max_tokens
        )
        total_usage = usage

        return {
            "response": strip_thinking(content),
            "tool_calls_executed": tool_calls_executed,
            "iterations": self.max_iterations,
            "usage": total_usage,
        }
