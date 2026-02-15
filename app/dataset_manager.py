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
            try:
                stat = jsonl_file.stat()
                # Estimar líneas por tamaño (promedio ~2KB por conversación)
                estimated_lines = max(1, int(stat.st_size / 2048))
                files.append({
                    "path": str(jsonl_file),
                    "filename": jsonl_file.name,
                    "size_bytes": stat.st_size,
                    "line_count": estimated_lines,
                    "directory": str(jsonl_file.parent.relative_to(base_path.parent)),
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


# ── Supabase client ──────────────────────────────────────────

_supabase_client = None


def _get_supabase(url: str, key: str):
    """Inicializa o retorna el cliente Supabase."""
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client
        _supabase_client = create_client(url, key)
    return _supabase_client


def save_conversation(url: str, key: str, data: dict) -> dict:
    """Upsert conversación en dataset_conversations."""
    sb = _get_supabase(url, key)

    messages = data.get("messages") or []
    has_tc, tools_used = _has_tool_calling(messages)
    validation = validate_qwen_format(messages)

    record = {
        "source_file": data["source_file"],
        "line_number": data["line_number"],
        "messages": [
            {"role": m.get("role", ""), "content": m.get("content", "")}
            for m in messages
        ],
        "metadata": data.get("metadata") or {},
        "tags": data.get("tags") or [],
        "has_tool_calling": has_tc,
        "tools_used": tools_used,
        "format_valid": validation["valid"],
        "format_errors": validation["errors"],
        "target_format": data.get("target_format", "qwen"),
        "quality_rating": data.get("quality_rating"),
        "review_notes": data.get("review_notes"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if data.get("quality_rating") is not None:
        record["reviewed_at"] = datetime.now(timezone.utc).isoformat()

    result = sb.table("dataset_conversations").upsert(
        record, on_conflict="source_file,line_number"
    ).execute()

    return {"status": "saved", "data": result.data}


def get_saved_conversations(
    url: str, key: str,
    source_file: Optional[str] = None,
    rating: Optional[int] = None,
    tag: Optional[str] = None,
) -> list[dict]:
    """Obtiene conversaciones guardadas con filtros opcionales."""
    sb = _get_supabase(url, key)
    query = sb.table("dataset_conversations").select("*")

    if source_file:
        query = query.eq("source_file", source_file)
    if rating is not None:
        query = query.eq("quality_rating", rating)
    if tag:
        query = query.contains("tags", [tag])

    result = query.order("source_file").order("line_number").execute()
    return result.data


def update_rating(url: str, key: str, conv_id: str, rating: int, notes: Optional[str] = None) -> dict:
    """Actualiza rating y notas de una conversación."""
    sb = _get_supabase(url, key)
    update = {
        "quality_rating": rating,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if notes is not None:
        update["review_notes"] = notes

    result = sb.table("dataset_conversations").update(update).eq("id", conv_id).execute()
    return {"status": "updated", "data": result.data}


def update_tags(url: str, key: str, conv_id: str, tags: list[str]) -> dict:
    """Actualiza tags de una conversación."""
    sb = _get_supabase(url, key)
    result = sb.table("dataset_conversations").update({
        "tags": tags,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", conv_id).execute()
    return {"status": "updated", "data": result.data}


def update_messages(url: str, key: str, conv_id: str, messages: list[dict]) -> dict:
    """Guarda mensajes editados."""
    sb = _get_supabase(url, key)
    has_tc, tools_used = _has_tool_calling(messages)
    validation = validate_qwen_format(messages)

    result = sb.table("dataset_conversations").update({
        "messages": messages,
        "has_tool_calling": has_tc,
        "tools_used": tools_used,
        "format_valid": validation["valid"],
        "format_errors": validation["errors"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", conv_id).execute()
    return {"status": "updated", "data": result.data}


def get_review_stats(url: str, key: str) -> dict:
    """Estadísticas de revisión."""
    sb = _get_supabase(url, key)

    all_rows = sb.table("dataset_conversations").select(
        "quality_rating, tags, reviewed_at"
    ).execute().data

    total = len(all_rows)
    reviewed = sum(1 for r in all_rows if r.get("reviewed_at"))
    by_rating = {}
    tag_counts = {}

    for r in all_rows:
        rating = r.get("quality_rating")
        if rating is not None:
            by_rating[rating] = by_rating.get(rating, 0) + 1
        for tag in (r.get("tags") or []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "total": total,
        "reviewed": reviewed,
        "pending": total - reviewed,
        "review_rate": round(reviewed / max(total, 1) * 100, 1),
        "by_rating": by_rating,
        "by_tag": tag_counts,
    }
