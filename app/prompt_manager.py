"""
Prompt Manager para TREFA — CRUD de master prompts y persistencia de conversaciones.
Sigue el mismo patrón que dataset_manager.py: _get_pg(), CREATE TABLE IF NOT EXISTS, _row_to_dict().
"""

import json
import time
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger()

_pg_pool = None

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS master_prompts (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    prompt_text TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_conversations (
    id SERIAL PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    master_prompt_id INTEGER REFERENCES master_prompts(id) ON DELETE SET NULL,
    messages JSONB DEFAULT '[]',
    tool_calls JSONB DEFAULT '[]',
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    rating INTEGER,
    rating_comment TEXT,
    has_hallucination BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER
);
"""


def _get_pg(database_url: str):
    """Retorna conexion PostgreSQL. Crea tablas si no existen."""
    global _pg_pool
    if _pg_pool is None:
        import psycopg2
        _pg_pool = psycopg2.connect(database_url)
        _pg_pool.autocommit = True
        with _pg_pool.cursor() as cur:
            cur.execute(CREATE_TABLES_SQL)
    return _pg_pool


def _row_to_dict(cur, row) -> dict:
    """Convierte una fila de cursor a dict."""
    cols = [desc[0] for desc in cur.description]
    d = {}
    for col, val in zip(cols, row):
        if col in ("created_at", "updated_at", "started_at", "ended_at") and val is not None:
            d[col] = val.isoformat()
        else:
            d[col] = val
    return d


# ---------------------------------------------------------------------------
# Cache in-memory del prompt activo (~60s TTL)
# ---------------------------------------------------------------------------
_active_prompt_cache: Optional[dict] = None
_active_prompt_ts: float = 0.0
_CACHE_TTL = 60.0


def _invalidate_cache():
    global _active_prompt_cache, _active_prompt_ts
    _active_prompt_cache = None
    _active_prompt_ts = 0.0


# ---------------------------------------------------------------------------
# CRUD — master_prompts
# ---------------------------------------------------------------------------

def create_prompt(database_url: str, name: str, prompt_text: str, description: str = "") -> dict:
    """Crea un nuevo master prompt."""
    conn = _get_pg(database_url)
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO master_prompts (name, description, prompt_text, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
        """, (name, description, prompt_text, now, now))
        row = cur.fetchone()
        return _row_to_dict(cur, row)


def update_prompt(database_url: str, prompt_id: int, data: dict) -> Optional[dict]:
    """Actualiza campos de un prompt. Auto-incrementa version."""
    conn = _get_pg(database_url)
    sets = ["updated_at = NOW()", "version = version + 1"]
    params = []

    for field in ("name", "description", "prompt_text"):
        if field in data:
            sets.append(f"{field} = %s")
            params.append(data[field])

    params.append(prompt_id)
    query = f"UPDATE master_prompts SET {', '.join(sets)} WHERE id = %s RETURNING *"

    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        if row:
            result = _row_to_dict(cur, row)
            _invalidate_cache()
            return result
    return None


def delete_prompt(database_url: str, prompt_id: int) -> bool:
    """Elimina un prompt (no se puede eliminar el activo)."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        # No permitir eliminar el prompt activo
        cur.execute("SELECT is_active FROM master_prompts WHERE id = %s", (prompt_id,))
        row = cur.fetchone()
        if not row:
            return False
        if row[0]:
            raise ValueError("No se puede eliminar el prompt activo")
        cur.execute("DELETE FROM master_prompts WHERE id = %s", (prompt_id,))
        return cur.rowcount > 0


def activate_prompt(database_url: str, prompt_id: int) -> Optional[dict]:
    """Activa un prompt y desactiva todos los demás."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute("UPDATE master_prompts SET is_active = FALSE WHERE is_active = TRUE")
        cur.execute("""
            UPDATE master_prompts SET is_active = TRUE, updated_at = NOW()
            WHERE id = %s RETURNING *
        """, (prompt_id,))
        row = cur.fetchone()
        if row:
            result = _row_to_dict(cur, row)
            _invalidate_cache()
            return result
    return None


def list_prompts(database_url: str) -> list[dict]:
    """Lista todos los prompts ordenados por fecha de creación."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM master_prompts ORDER BY created_at DESC")
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


def get_prompt(database_url: str, prompt_id: int) -> Optional[dict]:
    """Obtiene un prompt por ID."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM master_prompts WHERE id = %s", (prompt_id,))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None


def get_active_prompt(database_url: str) -> Optional[dict]:
    """Retorna el prompt activo, con cache de 60s."""
    global _active_prompt_cache, _active_prompt_ts
    now = time.time()
    if _active_prompt_cache and (now - _active_prompt_ts) < _CACHE_TTL:
        return _active_prompt_cache

    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM master_prompts WHERE is_active = TRUE LIMIT 1")
        row = cur.fetchone()
        if row:
            _active_prompt_cache = _row_to_dict(cur, row)
            _active_prompt_ts = now
            return _active_prompt_cache
    return None


def seed_default_prompt(database_url: str, default_prompt_text: str):
    """Si master_prompts está vacío, inserta el prompt hardcodeado como 'Mariana v1' activo."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM master_prompts")
        count = cur.fetchone()[0]
        if count == 0:
            now = datetime.now(timezone.utc)
            cur.execute("""
                INSERT INTO master_prompts (name, description, prompt_text, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, TRUE, %s, %s)
            """, ("Mariana v1", "Prompt original de Mariana — hardcodeado", default_prompt_text, now, now))
            logger.info("seed_default_prompt", name="Mariana v1")


# ---------------------------------------------------------------------------
# Conversaciones — chat_conversations
# ---------------------------------------------------------------------------

def save_or_update_conversation(
    database_url: str,
    session_id: str,
    master_prompt_id: Optional[int],
    messages: list,
    tool_calls: list,
    has_hallucination: bool = False,
) -> dict:
    """Upsert conversación por session_id."""
    conn = _get_pg(database_url)
    now = datetime.now(timezone.utc)
    messages_json = json.dumps(messages, ensure_ascii=False)
    tool_calls_json = json.dumps(tool_calls, ensure_ascii=False)

    with conn.cursor() as cur:
        # Intentar obtener started_at existente
        cur.execute("SELECT started_at FROM chat_conversations WHERE session_id = %s", (session_id,))
        existing = cur.fetchone()
        started_at = existing[0] if existing else now

        duration = None
        if existing:
            diff = now - started_at
            duration = int(diff.total_seconds())

        cur.execute("""
            INSERT INTO chat_conversations
                (session_id, master_prompt_id, messages, tool_calls,
                 message_count, tool_call_count, has_hallucination,
                 started_at, ended_at, duration_seconds)
            VALUES (%s, %s, %s::jsonb, %s::jsonb,
                    %s, %s, %s,
                    %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                messages = EXCLUDED.messages,
                tool_calls = EXCLUDED.tool_calls,
                message_count = EXCLUDED.message_count,
                tool_call_count = EXCLUDED.tool_call_count,
                has_hallucination = EXCLUDED.has_hallucination,
                ended_at = EXCLUDED.ended_at,
                duration_seconds = EXCLUDED.duration_seconds
            RETURNING *
        """, (
            session_id, master_prompt_id,
            messages_json, tool_calls_json,
            len(messages), len(tool_calls),
            has_hallucination,
            started_at, now, duration,
        ))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else {}


def update_conversation_rating(
    database_url: str, session_id: str, rating: int, comment: Optional[str] = None
) -> Optional[dict]:
    """Actualiza rating de una conversación."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE chat_conversations SET rating = %s, rating_comment = %s
            WHERE session_id = %s RETURNING *
        """, (rating, comment, session_id))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None


def get_conversations_by_prompt(
    database_url: str, prompt_id: int, offset: int = 0, limit: int = 20
) -> dict:
    """Obtiene conversaciones paginadas de un prompt."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM chat_conversations WHERE master_prompt_id = %s",
            (prompt_id,)
        )
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT * FROM chat_conversations
            WHERE master_prompt_id = %s
            ORDER BY started_at DESC
            LIMIT %s OFFSET %s
        """, (prompt_id, limit, offset))
        rows = cur.fetchall()
        convs = [_row_to_dict(cur, r) for r in rows]

    return {"conversations": convs, "total": total, "offset": offset, "limit": limit}


# ---------------------------------------------------------------------------
# Estadísticas
# ---------------------------------------------------------------------------

def get_prompt_stats(database_url: str, prompt_id: int) -> dict:
    """Estadísticas agregadas de un prompt."""
    conn = _get_pg(database_url)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total_conversations,
                COALESCE(AVG(rating), 0) AS avg_rating,
                COUNT(rating) AS rated_count,
                COALESCE(AVG(message_count), 0) AS avg_messages,
                COALESCE(AVG(tool_call_count), 0) AS avg_tool_calls,
                COUNT(CASE WHEN tool_call_count > 0 THEN 1 END) AS conversations_with_tools,
                COALESCE(AVG(duration_seconds), 0) AS avg_duration,
                COUNT(CASE WHEN has_hallucination THEN 1 END) AS hallucination_count
            FROM chat_conversations
            WHERE master_prompt_id = %s
        """, (prompt_id,))
        row = cur.fetchone()
        cols = [desc[0] for desc in cur.description]
        stats = dict(zip(cols, row))

        # Convertir Decimal a float
        for k, v in stats.items():
            if hasattr(v, '__float__'):
                stats[k] = round(float(v), 2)

        total = stats["total_conversations"]
        stats["tool_calling_rate"] = (
            round(stats["conversations_with_tools"] / total * 100, 1) if total > 0 else 0
        )
        stats["hallucination_rate"] = (
            round(stats["hallucination_count"] / total * 100, 1) if total > 0 else 0
        )

    return stats


def get_all_prompt_stats(database_url: str) -> list[dict]:
    """Estadísticas de todos los prompts."""
    prompts = list_prompts(database_url)
    results = []
    for p in prompts:
        stats = get_prompt_stats(database_url, p["id"])
        stats["prompt_id"] = p["id"]
        stats["prompt_name"] = p["name"]
        stats["is_active"] = p["is_active"]
        results.append(stats)
    return results
