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

from app.mcp_client import MCPClient
from app.llm_service import LLMService

logger = structlog.get_logger()

# Regex para detectar tool calls en formato Qwen
TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)

THINK_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

# Regex para limpiar tool_call tags huérfanos de la respuesta final
_TOOL_CALL_CLEANUP = re.compile(r"</?tool_call>", re.DOTALL)
_TOOL_CALL_BLOCK_CLEANUP = re.compile(
    r"<tool_call>\s*\{.*?\}\s*</tool_call>", re.DOTALL
)

# Patrones de saludo / mensajes que NO requieren herramientas
_GREETING_PATTERNS = re.compile(
    r"^\s*(hola|hey|buenos?\s*d[ií]as?|buenas?\s*tardes?|buenas?\s*noches?|"
    r"qu[eé]\s*tal|qué\s*onda|hi|hello|saludos|buen\s*d[ií]a)\s*[.!,?😊🙋‍♂️🙋‍♀️👋]*\s*$",
    re.IGNORECASE,
)

# Palabras clave que indican intención de búsqueda de vehículo
_VEHICLE_INTENT_KEYWORDS = re.compile(
    r"(busco|quiero|necesito|interesa|auto|carro|coche|camioneta|suv|sedan|sedán|"
    r"pick\s*up|pickup|truck|hatchback|van|minivan|"
    r"presupuesto|precio|cuánto|cuanto|financ|crédito|credito|enganche|mensualidad|"
    r"marca|modelo|año|kilometr|"
    r"toyota|honda|nissan|mazda|kia|hyundai|chevrolet|ford|volkswagen|vw|bmw|"
    r"mercedes|audi|seat|renault|peugeot|suzuki|mitsubishi|subaru|jeep|dodge|"
    r"ram|buick|gmc|cadillac|lincoln|acura|infiniti|lexus|"
    r"corolla|civic|sentra|versa|march|cx-?[3579]|rav4|tucson|sportage|"
    r"rio|accent|aveo|spark|onix|jetta|golf|tiguan|polo)",
    re.IGNORECASE,
)


def strip_thinking(text: str) -> str:
    """Elimina bloques <think>...</think> de la respuesta."""
    return THINK_PATTERN.sub("", text).strip()


def clean_response(text: str) -> str:
    """Limpia la respuesta final: elimina thinking y tool_call tags huérfanos."""
    text = strip_thinking(text)
    # Eliminar tags <think> o </think> sueltos
    text = re.sub(r"</?think>", "", text)
    # Eliminar bloques <tool_call>...</tool_call> completos que no se ejecutaron
    text = _TOOL_CALL_BLOCK_CLEANUP.sub("", text)
    # Eliminar tags sueltos de tool_call
    text = _TOOL_CALL_CLEANUP.sub("", text)
    text = text.strip()
    # Detectar respuesta duplicada (modelo repite después de </think>)
    if len(text) > 40:
        half = len(text) // 2
        first_half = text[:half].strip()
        second_half = text[half:].strip()
        # Si la segunda mitad empieza igual que la primera (al menos 30 chars)
        prefix = first_half[:min(30, len(first_half))]
        if prefix and second_half.startswith(prefix):
            text = second_half
    return text.strip()


def is_greeting(message: str) -> bool:
    """Detecta si el mensaje del usuario es un saludo simple."""
    return bool(_GREETING_PATTERNS.match(message.strip()))


def has_vehicle_intent(message: str) -> bool:
    """Detecta si el mensaje contiene intención de búsqueda de vehículo."""
    return bool(_VEHICLE_INTENT_KEYWORDS.search(message))


def needs_discovery(messages: List[Dict[str, str]]) -> bool:
    """Determina si la conversación aún necesita fase de descubrimiento.

    Retorna True si no se ha mencionado ningún criterio de búsqueda
    en los mensajes del usuario (excluyendo system y tool_response).
    """
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            # Ignorar tool_responses inyectados
            if content.strip().startswith("<tool_response>"):
                continue
            if has_vehicle_intent(content):
                return False
    return True


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
        llm_service: LLMService,
        known_tools: List[str],
        max_iterations: int = 5,
    ):
        self.mcp = mcp_client
        self.llm = llm_service
        self.known_tools = set(known_tools)
        self.max_iterations = max_iterations

    @staticmethod
    def _fix_vehicle_id(
        tool_args: Dict[str, Any],
        previous_calls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Corrige el ID de obtener_vehiculo si no coincide con IDs vistos.

        El modelo a veces confunde el kilometraje u otro número con el ID.
        Buscamos en los resultados previos de buscar_vehiculos los IDs reales
        y si el ID pedido no está, usamos el primer (o único) vehículo.
        """
        requested_id = tool_args.get("id")
        if requested_id is None:
            return tool_args

        # Recolectar IDs reales de búsquedas anteriores
        known_ids = []
        for prev in previous_calls:
            if prev["name"] == "buscar_vehiculos":
                result = prev.get("result", {})
                vehiculos = result.get("vehiculos", [])
                for v in vehiculos:
                    vid = v.get("id")
                    if vid is not None:
                        known_ids.append(vid)

        if not known_ids:
            return tool_args

        if requested_id in known_ids:
            return tool_args

        # ID incorrecto — usar el primero disponible (normalmente solo hay 1-3)
        corrected_id = known_ids[-1]  # último resultado, más probable que sea el elegido
        logger.warning(
            "vehicle_id_corrected",
            requested=requested_id,
            corrected=corrected_id,
            known_ids=known_ids,
        )
        return {**tool_args, "id": corrected_id}

    async def _call_llm(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> Tuple[str, Dict[str, Any]]:
        """Llama al LLM via OpenAI SDK y retorna (texto_respuesta, usage)."""
        data = await self.llm.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
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
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        working_messages = list(messages)

        # --- Guard: fase de descubrimiento ---
        # Si el usuario NO ha expresado ningún criterio de búsqueda
        # (no menciona marca, modelo, presupuesto, tipo de auto, etc.),
        # forzar respuesta conversacional SIN herramientas.
        # Esto evita que el modelo salte a buscar autos cuando el usuario
        # solo saluda, da su nombre, o platica de otra cosa.
        if needs_discovery(working_messages):
            last_user_msg = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_user_msg = msg.get("content", "")
                    break

            logger.info("discovery_mode", message=last_user_msg[:50])
            # Llamar SIN tools declaration para forzar respuesta natural
            discovery_messages = []
            for msg in working_messages:
                if msg.get("role") == "system" and "# Tools" in msg.get("content", ""):
                    system_text = msg["content"].split("\n\n# Tools")[0]
                    user_msg_count = sum(1 for m in messages if m.get("role") == "user")
                    if user_msg_count <= 1:
                        system_text += "\n\nIMPORTANTE: Este es el primer mensaje del cliente. DEBES saludar como Mariana, preséntarte y preguntar el nombre del cliente. NO uses herramientas en este turno."
                    else:
                        system_text += "\n\nIMPORTANTE: El cliente aún NO ha dicho qué vehículo busca. NO uses herramientas. Agradece su nombre si lo dio, y pregunta qué tipo de auto le interesa y su presupuesto. Sé conversacional."
                    discovery_messages.append({"role": "system", "content": system_text})
                else:
                    discovery_messages.append(msg)

            content, usage = await self._call_llm(
                discovery_messages, model, temperature, max_tokens
            )
            return {
                "response": clean_response(content),
                "tool_calls_executed": [],
                "iterations": 1,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }

        for iteration in range(1, self.max_iterations + 1):
            content, usage = await self._call_llm(
                working_messages, model, temperature, max_tokens
            )
            total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            total_usage["total_tokens"] += usage.get("total_tokens", 0)

            tool_calls = extract_tool_calls(content)

            if not tool_calls:
                return {
                    "response": clean_response(content),
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

                # Guard: corregir ID incorrecto en obtener_vehiculo
                if tool_name == "obtener_vehiculo" and "id" in tool_args:
                    tool_args = self._fix_vehicle_id(tool_args, tool_calls_executed)

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
        content, usage = await self._call_llm(
            working_messages, model, temperature, max_tokens
        )
        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        total_usage["total_tokens"] += usage.get("total_tokens", 0)

        return {
            "response": clean_response(content),
            "tool_calls_executed": tool_calls_executed,
            "iterations": self.max_iterations,
            "usage": total_usage,
        }
