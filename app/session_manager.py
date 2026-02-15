"""
Gestión de sesiones multi-turno en memoria con LRU eviction.
Mantiene historial de conversación por session_id.
"""

import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

DEFAULT_TTL = 86400  # 24 horas
MAX_SESSIONS = 1000


class ChatSession:
    def __init__(self, session_id: Optional[str] = None, metadata: Optional[Dict] = None):
        self.id = session_id or str(uuid.uuid4())
        self.messages: List[Dict[str, str]] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self.metadata: Dict[str, Any] = metadata or {}
        self.tool_calls_log: List[Dict[str, Any]] = []

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        self.updated_at = time.time()

    def add_tool_call(self, tool_info: Dict[str, Any]):
        self.tool_calls_log.append(tool_info)

    def get_messages(self, system_prompt: str, max_messages: int = 20) -> List[Dict[str, str]]:
        """Retorna system prompt + últimos N mensajes (ventana deslizante)."""
        result = [{"role": "system", "content": system_prompt}]
        if len(self.messages) > max_messages:
            result.extend(self.messages[-max_messages:])
        else:
            result.extend(self.messages)
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "tool_calls_count": len(self.tool_calls_log),
        }


class InMemorySessionManager:
    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl: float = DEFAULT_TTL):
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()
        self._max_sessions = max_sessions
        self._ttl = ttl

    def _evict_expired(self):
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s.updated_at > self._ttl
        ]
        for sid in expired:
            del self._sessions[sid]

    def _evict_lru(self):
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

    def create_session(self, session_id: Optional[str] = None, metadata: Optional[Dict] = None) -> ChatSession:
        self._evict_expired()
        self._evict_lru()
        session = ChatSession(session_id=session_id, metadata=metadata)
        self._sessions[session.id] = session
        self._sessions.move_to_end(session.id)
        logger.info("session_created", session_id=session.id)
        return session

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        self._evict_expired()
        session = self._sessions.get(session_id)
        if session:
            self._sessions.move_to_end(session_id)
        return session

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info("session_deleted", session_id=session_id)
            return True
        return False

    @property
    def active_count(self) -> int:
        self._evict_expired()
        return len(self._sessions)

    def get_all_sessions_summary(self) -> List[Dict[str, Any]]:
        self._evict_expired()
        return [
            {
                "id": s.id,
                "messages_count": len(s.messages),
                "tool_calls_count": len(s.tool_calls_log),
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in self._sessions.values()
        ]
