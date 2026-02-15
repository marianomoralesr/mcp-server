"""
Feedback y detección de alucinaciones.
Registra conversaciones, ratings y detecta respuestas sospechosas.
"""

import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

# Tools que consultan inventario/precios
INVENTORY_TOOLS = {
    "buscar_vehiculos",
    "obtener_vehiculo",
    "buscar_alternativas",
    "comparar_vehiculos",
    "estadisticas_inventario",
    "calcular_financiamiento",
}

# Regex para detectar precios en MXN
PRICE_PATTERN = re.compile(r"\$[\d,]{4,}")


class FeedbackManager:
    def __init__(self):
        self._ratings: List[Dict[str, Any]] = []
        self._conversation_logs: List[Dict[str, Any]] = []
        self._tool_usage: Dict[str, int] = defaultdict(int)
        self._hallucination_flags: List[Dict[str, Any]] = []
        self._total_chats: int = 0
        self._chats_with_tools: int = 0
        self._total_latency_ms: float = 0
        self._latency_count: int = 0

    def log_conversation(self, session, tool_calls: List[Dict[str, Any]]):
        """Registra conversación para revisión."""
        self._total_chats += 1
        if tool_calls:
            self._chats_with_tools += 1
        for tc in tool_calls:
            self._tool_usage[tc["name"]] += 1
            if tc.get("duration_ms"):
                self._total_latency_ms += tc["duration_ms"]
                self._latency_count += 1

        self._conversation_logs.append({
            "session_id": session.id,
            "messages_count": len(session.messages),
            "tool_calls_count": len(tool_calls),
            "timestamp": time.time(),
        })
        # Keep last 1000 logs
        if len(self._conversation_logs) > 1000:
            self._conversation_logs = self._conversation_logs[-1000:]

    def detect_potential_hallucination(
        self, response: str, tool_calls: List[Dict[str, Any]]
    ) -> bool:
        """Heurística: si la respuesta menciona precios sin haber ejecutado tools de inventario."""
        has_prices = bool(PRICE_PATTERN.search(response))
        used_inventory_tool = any(
            tc["name"] in INVENTORY_TOOLS for tc in tool_calls
        )

        if has_prices and not used_inventory_tool:
            self._hallucination_flags.append({
                "response_snippet": response[:200],
                "tool_calls": [tc["name"] for tc in tool_calls],
                "timestamp": time.time(),
            })
            logger.warning(
                "potential_hallucination",
                has_prices=True,
                used_inventory_tool=False,
            )
            return True
        return False

    def submit_rating(self, session_id: str, rating: int, comment: Optional[str] = None):
        """Registra calificación de un revisor."""
        entry = {
            "session_id": session_id,
            "rating": rating,
            "comment": comment,
            "timestamp": time.time(),
        }
        self._ratings.append(entry)
        logger.info("feedback_received", session_id=session_id, rating=rating)

    def get_analytics(self) -> Dict[str, Any]:
        """Retorna analíticas agregadas."""
        avg_rating = 0.0
        if self._ratings:
            avg_rating = sum(r["rating"] for r in self._ratings) / len(self._ratings)

        avg_latency_ms = 0.0
        if self._latency_count > 0:
            avg_latency_ms = self._total_latency_ms / self._latency_count

        tool_calling_rate = 0.0
        if self._total_chats > 0:
            tool_calling_rate = self._chats_with_tools / self._total_chats

        # Top tools sorted by usage
        top_tools = sorted(
            self._tool_usage.items(), key=lambda x: x[1], reverse=True
        )

        return {
            "total_chats": self._total_chats,
            "chats_with_tools": self._chats_with_tools,
            "tool_calling_rate": round(tool_calling_rate, 3),
            "top_tools": [{"name": n, "calls": c} for n, c in top_tools],
            "total_ratings": len(self._ratings),
            "average_rating": round(avg_rating, 2),
            "hallucination_flags": len(self._hallucination_flags),
            "avg_tool_latency_ms": round(avg_latency_ms, 1),
        }
