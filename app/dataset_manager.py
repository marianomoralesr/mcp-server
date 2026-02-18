"""
Dataset Manager para TREFA — escaneo, parsing, validación y persistencia
de conversaciones JSONL para revisión y calificación.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

# Herramientas conocidas de Mariana (TREFA)
TOOLS_CONOCIDAS = {
    "buscar_vehiculos", "obtener_vehiculo", "buscar_alternativas",
    "comparar_vehiculos", "estadisticas_inventario", "calcular_financiamiento",
    "buscar_informacion", "obtener_info_negocio", "obtener_faqs",
    "solicitar_datos_contacto", "enviar_cotizacion_email",
    "agendar_cita", "evaluar_intercambio",
}

TOOLS_PATTERN = "|".join(map(re.escape, TOOLS_CONOCIDAS))

TOOL_CALL_PATTERNS = [
    re.compile(r'<tool_call>\s*\{[^}]*"name"\s*:\s*"(\w+)"', re.DOTALL),
    re.compile(r'"function_call"\s*:\s*\{[^}]*"name"\s*:\s*"(\w+)"'),
    re.compile(r'Action:\s*(\w+)\s*\nAction Input:'),
    re.compile(r'"tool_calls"\s*:\s*\[\s*\{[^}]*"name"\s*:\s*"(\w+)"'),
    re.compile(rf'\b({TOOLS_PATTERN})\s*\(', re.IGNORECASE),
]

TOOL_CALL_BLOCK = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL)
TOOL_RESPONSE_BLOCK = re.compile(r'<tool_response>\s*(.*?)\s*</tool_response>', re.DOTALL)

# Cache en memoria
_file_cache: dict = {}


def _detect_tools(text: str) -> list[str]:
    """Detecta herramientas usadas en un mensaje de assistant."""
    tools = []
    for pattern in TOOL_CALL_PATTERNS:
        for m in pattern.findall(text):
            if m in TOOLS_CONOCIDAS and m not in tools:
                tools.append(m)
    return tools


def _has_tool_calling(messages: list[dict]) -> tuple[bool, list[str]]:
    """Retorna (tiene_tool_calling, lista_de_tools) para una conversación."""
    all_tools = []
    for msg in messages:
        if msg.get("role") == "assistant":
            found = _detect_tools(msg.get("content", ""))
            for t in found:
                if t not in all_tools:
                    all_tools.append(t)
    return len(all_tools) > 0, all_tools


def validate_qwen_format(messages: list[dict]) -> dict:
    """
    Valida formato Qwen ChatML.
    Retorna {valid: bool, errors: [str], warnings: [str]}
    """
    errors = []
    warnings = []
    valid_roles = {"system", "user", "assistant", "tool"}

    if not messages:
        return {"valid": False, "errors": ["Conversación vacía"], "warnings": []}

    prev_role = None
    tool_calls_pending = []

    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        # Cada mensaje debe tener role y content
        if not role:
            errors.append(f"Mensaje {i}: falta 'role'")
            continue
        if content is None:
            errors.append(f"Mensaje {i}: falta 'content'")
            continue
        if not isinstance(role, str):
            errors.append(f"Mensaje {i}: 'role' no es string")
        if not isinstance(content, str):
            errors.append(f"Mensaje {i}: 'content' no es string")
            continue

        # Role válido
        if role not in valid_roles:
            errors.append(f"Mensaje {i}: role '{role}' inválido (esperado: {valid_roles})")

        # Validar tool_call JSON
        if role == "assistant":
            tc_blocks = TOOL_CALL_BLOCK.findall(content)
            for tc_raw in tc_blocks:
                try:
                    tc_json = json.loads(tc_raw)
                    if "name" not in tc_json:
                        errors.append(f"Mensaje {i}: tool_call sin 'name'")
                    elif tc_json["name"] not in TOOLS_CONOCIDAS:
                        warnings.append(f"Mensaje {i}: tool '{tc_json['name']}' no está en whitelist TREFA")
                    else:
                        tool_calls_pending.append(tc_json["name"])
                    if "arguments" not in tc_json:
                        warnings.append(f"Mensaje {i}: tool_call sin 'arguments'")
                except json.JSONDecodeError:
                    errors.append(f"Mensaje {i}: tool_call con JSON inválido")

        # Validar tool_response JSON
        if role == "tool":
            tr_blocks = TOOL_RESPONSE_BLOCK.findall(content)
            if not tr_blocks and content.strip():
                # Contenido directo (sin tags), verificar que sea JSON
                try:
                    json.loads(content)
                except json.JSONDecodeError:
                    warnings.append(f"Mensaje {i}: tool response no es JSON válido")
            for tr_raw in tr_blocks:
                try:
                    json.loads(tr_raw)
                except json.JSONDecodeError:
                    errors.append(f"Mensaje {i}: tool_response con JSON inválido")
            # Marcar que se respondió un tool_call
            if tool_calls_pending:
                tool_calls_pending.pop(0)

        # Alternancia de roles
        if role == "tool" and prev_role not in ("assistant", None):
            warnings.append(f"Mensaje {i}: role 'tool' después de '{prev_role}' (esperado: assistant)")
        if role == "user" and prev_role == "user":
            warnings.append(f"Mensaje {i}: dos mensajes 'user' consecutivos")

        prev_role = role

    # Tool calls sin response
    if tool_calls_pending:
        warnings.append(f"tool_call sin tool_response correspondiente: {tool_calls_pending}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def merge_jsonl_files(file_paths: list[str], output_path: str) -> dict:
    """
    Combina múltiples archivos JSONL en un solo archivo.
    Retorna {total_lines, source_files, output_path}.
    """
    total_lines = 0
    source_files = []

    with open(output_path, "w", encoding="utf-8") as out:
        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                continue
            count = 0
            with open(p, "r", encoding="utf-8") as inp:
                for line in inp:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    out.write(stripped + "\n")
                    count += 1
                    total_lines += 1
            source_files.append({"path": str(p), "lines": count})

    return {
        "total_lines": total_lines,
        "source_files": source_files,
        "output_path": output_path,
    }


def auto_tag_files(base_dirs: list[str]) -> dict:
    """
    Escanea todos los JSONL y cuenta conversaciones con/sin TC por archivo.
    Reutiliza _has_tool_calling().
    """
    results = []
    for base_dir in base_dirs:
        base_path = Path(base_dir)
        if not base_path.exists():
            continue
        for jsonl_file in sorted(base_path.rglob("*.jsonl")):
            total = 0
            with_tc = 0
            without_tc = 0
            try:
                with open(jsonl_file, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        total += 1
                        try:
                            data = json.loads(stripped)
                            messages = data.get("messages", data.get("conversations", []))
                            has_tc, _ = _has_tool_calling(messages)
                            if has_tc:
                                with_tc += 1
                            else:
                                without_tc += 1
                        except json.JSONDecodeError:
                            without_tc += 1
            except OSError:
                continue
            results.append({
                "path": str(jsonl_file),
                "filename": jsonl_file.name,
                "total": total,
                "with_tc": with_tc,
                "without_tc": without_tc,
            })

    return {"files": results}


def _meta_path(filepath: str) -> Path:
    """Retorna la ruta del sidecar .meta.json para un archivo JSONL."""
    return Path(filepath + ".meta.json")


def get_file_metadata(filepath: str) -> dict:
    """Lee metadatos de un sidecar .meta.json. Retorna dict vacío si no existe."""
    mp = _meta_path(filepath)
    if mp.exists():
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_file_metadata(filepath: str, data: dict) -> dict:
    """Guarda metadatos en sidecar .meta.json. Hace merge con datos existentes."""
    existing = get_file_metadata(filepath)
    existing.update(data)
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    mp = _meta_path(filepath)
    mp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return existing


def scan_jsonl_files(base_dirs: list[str], force_rescan: bool = False) -> list[dict]:
    """
    Escanea directorios recursivamente buscando archivos .jsonl.
    Retorna lista de metadatos por archivo.
    """
    global _file_cache

    cache_key = ",".join(sorted(base_dirs))
    if not force_rescan and cache_key in _file_cache:
        return _file_cache[cache_key]

    files = []
    for base_dir in base_dirs:
        base_path = Path(base_dir)
        if not base_path.exists():
            continue
        for jsonl_file in sorted(base_path.rglob("*.jsonl")):
            # Saltar sidecars
            if jsonl_file.name.endswith(".meta.json"):
                continue
            try:
                stat = jsonl_file.stat()
                # Estimar líneas por tamaño (promedio ~2KB por conversación)
                estimated_lines = max(1, int(stat.st_size / 2048))
                meta = get_file_metadata(str(jsonl_file))
                files.append({
                    "path": str(jsonl_file),
                    "filename": jsonl_file.name,
                    "size_bytes": stat.st_size,
                    "line_count": estimated_lines,
                    "directory": str(jsonl_file.parent.relative_to(base_path.parent)),
                    "version": meta.get("version", ""),
                    "file_tags": meta.get("file_tags", []),
                    "modified_at": stat.st_mtime,
                })
            except OSError:
                continue

    _file_cache[cache_key] = files
    return files


def read_jsonl_page(filepath: str, offset: int = 0, limit: int = 50) -> dict:
    """
    Lee N conversaciones desde un archivo JSONL con paginación.
    Retorna {conversations: [...], total: int, offset: int, limit: int}
    """
    conversations = []
    total = 0
    errors = 0

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                total += 1

                if total <= offset or total > offset + limit:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    conversations.append({
                        "line_number": line_num,
                        "messages": [],
                        "parse_error": True,
                        "has_tool_calling": False,
                        "tools_used": [],
                        "message_count": 0,
                        "has_system_prompt": False,
                        "roles_present": [],
                        "first_user_message": "",
                    })
                    continue

                messages = data.get("messages", data.get("conversations", []))
                has_tc, tools_used = _has_tool_calling(messages)
                roles = list({m.get("role", "") for m in messages})
                first_user = ""
                for m in messages:
                    if m.get("role") == "user":
                        first_user = m.get("content", "")[:200]
                        break

                conversations.append({
                    "line_number": line_num,
                    "messages": messages,
                    "has_tool_calling": has_tc,
                    "tools_used": tools_used,
                    "message_count": len(messages),
                    "has_system_prompt": any(m.get("role") == "system" for m in messages),
                    "roles_present": roles,
                    "first_user_message": first_user,
                    "metadata": data.get("metadata", {}),
                })
    except FileNotFoundError:
        return {"conversations": [], "total": 0, "offset": offset, "limit": limit, "error": "Archivo no encontrado"}

    return {
        "conversations": conversations,
        "total": total,
        "offset": offset,
        "limit": limit,
        "errors": errors,
    }


# ── PostgreSQL client ──────────────────────────────────────────

_pg_pool = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS validated_conversations (
    id SERIAL PRIMARY KEY,
    source_file TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    messages JSONB NOT NULL DEFAULT '[]',
    metadata JSONB NOT NULL DEFAULT '{}',
    tags TEXT[] NOT NULL DEFAULT '{}',
    has_tool_calling BOOLEAN NOT NULL DEFAULT FALSE,
    tools_used TEXT[] NOT NULL DEFAULT '{}',
    format_valid BOOLEAN NOT NULL DEFAULT TRUE,
    format_errors TEXT[] NOT NULL DEFAULT '{}',
    target_format TEXT NOT NULL DEFAULT 'qwen',
    quality_rating INTEGER,
    review_notes TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_file, line_number)
);
"""


def _get_pg(database_url: str):
    """Retorna conexion PostgreSQL. Crea tabla si no existe."""
    global _pg_pool
    if _pg_pool is None:
        import psycopg2
        _pg_pool = psycopg2.connect(database_url)
        _pg_pool.autocommit = True
        with _pg_pool.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
    return _pg_pool


def _row_to_dict(cur, row) -> dict:
    """Convierte una fila de cursor a dict."""
    cols = [desc[0] for desc in cur.description]
    d = {}
    for col, val in zip(cols, row):
        if col in ("created_at", "updated_at", "reviewed_at") and val is not None:
            d[col] = val.isoformat()
        else:
            d[col] = val
    return d


def save_conversation(database_url: str, data: dict) -> dict:
    """Upsert conversacion en validated_conversations."""
    conn = _get_pg(database_url)

    messages = data.get("messages") or []
    has_tc, tools_used = _has_tool_calling(messages)
    validation = validate_qwen_format(messages)
    now = datetime.now(timezone.utc)

    clean_messages = json.dumps([
        {"role": m.get("role", ""), "content": m.get("content", "")}
        for m in messages
    ], ensure_ascii=False)
    meta_json = json.dumps(data.get("metadata") or {}, ensure_ascii=False)
    tags = data.get("tags") or []
    reviewed_at = now if data.get("quality_rating") is not None else None

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO validated_conversations
                (source_file, line_number, messages, metadata, tags,
                 has_tool_calling, tools_used, format_valid, format_errors,
                 target_format, quality_rating, review_notes, reviewed_at, updated_at)
            VALUES (%s, %s, %s::jsonb, %s::jsonb, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
            ON CONFLICT (source_file, line_number) DO UPDATE SET
                messages = EXCLUDED.messages,
                metadata = EXCLUDED.metadata,
                tags = EXCLUDED.tags,
                has_tool_calling = EXCLUDED.has_tool_calling,
                tools_used = EXCLUDED.tools_used,
                format_valid = EXCLUDED.format_valid,
                format_errors = EXCLUDED.format_errors,
                target_format = EXCLUDED.target_format,
                quality_rating = EXCLUDED.quality_rating,
                review_notes = EXCLUDED.review_notes,
                reviewed_at = EXCLUDED.reviewed_at,
                updated_at = EXCLUDED.updated_at
            RETURNING *
        """, (
            data["source_file"], data["line_number"],
            clean_messages, meta_json, tags,
            has_tc, tools_used, validation["valid"], validation["errors"],
            data.get("target_format", "qwen"),
            data.get("quality_rating"), data.get("review_notes"),
            reviewed_at, now,
        ))
        row = cur.fetchone()
        result = _row_to_dict(cur, row) if row else {}

    return {"status": "saved", "data": [result]}


def get_saved_conversations(
    database_url: str,
    source_file: Optional[str] = None,
    rating: Optional[int] = None,
    tag: Optional[str] = None,
) -> list[dict]:
    """Obtiene conversaciones guardadas con filtros opcionales."""
    conn = _get_pg(database_url)
    conditions = []
    params = []

    if source_file:
        conditions.append("source_file = %s")
        params.append(source_file)
    if rating is not None:
        conditions.append("quality_rating = %s")
        params.append(rating)
    if tag:
        conditions.append("%s = ANY(tags)")
        params.append(tag)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"SELECT * FROM validated_conversations{where} ORDER BY source_file, line_number"

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


def update_rating(database_url: str, conv_id: str, rating: int, notes: Optional[str] = None) -> dict:
    """Actualiza rating y notas de una conversacion."""
    conn = _get_pg(database_url)
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        if notes is not None:
            cur.execute("""
                UPDATE validated_conversations
                SET quality_rating = %s, review_notes = %s, reviewed_at = %s, updated_at = %s
                WHERE id = %s RETURNING *
            """, (rating, notes, now, now, conv_id))
        else:
            cur.execute("""
                UPDATE validated_conversations
                SET quality_rating = %s, reviewed_at = %s, updated_at = %s
                WHERE id = %s RETURNING *
            """, (rating, now, now, conv_id))
        row = cur.fetchone()
        result = _row_to_dict(cur, row) if row else {}

    return {"status": "updated", "data": [result]}


def update_tags(database_url: str, conv_id: str, tags: list[str]) -> dict:
    """Actualiza tags de una conversacion."""
    conn = _get_pg(database_url)
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE validated_conversations SET tags = %s, updated_at = %s
            WHERE id = %s RETURNING *
        """, (tags, now, conv_id))
        row = cur.fetchone()
        result = _row_to_dict(cur, row) if row else {}

    return {"status": "updated", "data": [result]}


def update_messages(database_url: str, conv_id: str, messages: list[dict]) -> dict:
    """Guarda mensajes editados."""
    conn = _get_pg(database_url)
    has_tc, tools_used = _has_tool_calling(messages)
    validation = validate_qwen_format(messages)
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE validated_conversations
            SET messages = %s::jsonb, has_tool_calling = %s, tools_used = %s,
                format_valid = %s, format_errors = %s, updated_at = %s
            WHERE id = %s RETURNING *
        """, (
            json.dumps(messages, ensure_ascii=False),
            has_tc, tools_used,
            validation["valid"], validation["errors"], now, conv_id,
        ))
        row = cur.fetchone()
        result = _row_to_dict(cur, row) if row else {}

    return {"status": "updated", "data": [result]}


def get_review_stats(database_url: str) -> dict:
    """Estadisticas de revision."""
    conn = _get_pg(database_url)

    with conn.cursor() as cur:
        cur.execute("SELECT quality_rating, tags, reviewed_at FROM validated_conversations")
        rows = cur.fetchall()

    total = len(rows)
    reviewed = sum(1 for r in rows if r[2] is not None)
    by_rating = {}
    tag_counts = {}

    for r in rows:
        rating = r[0]
        tags = r[1] or []
        if rating is not None:
            by_rating[rating] = by_rating.get(rating, 0) + 1
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "total": total,
        "reviewed": reviewed,
        "pending": total - reviewed,
        "review_rate": round(reviewed / max(total, 1) * 100, 1),
        "by_rating": by_rating,
        "by_tag": tag_counts,
    }
