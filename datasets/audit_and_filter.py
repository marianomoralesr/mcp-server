#!/usr/bin/env python3
"""
===============================================================
 audit_and_filter.py — Auditoría exhaustiva + filtrado agresivo
 del dataset de training para eliminar conversaciones que puedan
 confundir al modelo durante fine-tuning
===============================================================

Fase 1: Auditoría (1 de cada 4 conversaciones, detallada)
  - Verifica coherencia entre user intent y tool elegido
  - Verifica que argumentos del tool call sean válidos
  - Verifica que la tool response tenga la shape canónica
  - Verifica que el assistant interprete correctamente la response
  - Especial: financiamiento → debe usar calcular_financiamiento

Fase 2: Filtrado (TODAS las conversaciones)
  - Elimina cualquier conversación con issues graves
  - Prioridad: calidad > cantidad (OK si quedan ~1000)
  - Escribe archivo limpio + reporte de eliminadas

Uso:
    # Solo auditoría (no filtra)
    python3 audit_and_filter.py --audit-only

    # Auditoría + filtrado
    python3 audit_and_filter.py

    # Solo un archivo
    python3 audit_and_filter.py --file merged_v10_together_train.jsonl
===============================================================
"""

import argparse
import json
import math
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ─── Constantes del MCP canónico ─────────────────────────────

VALID_TOOLS = {
    "buscar_vehiculos", "obtener_vehiculo", "buscar_alternativas",
    "comparar_vehiculos", "estadisticas_inventario", "calcular_financiamiento",
    "buscar_informacion", "obtener_info_negocio", "obtener_faqs",
    "solicitar_datos_contacto", "enviar_cotizacion_email"
}

VALID_ARGS = {
    "buscar_vehiculos": {
        "marca", "modelo", "año_minimo", "año_maximo",
        "precio_minimo", "precio_maximo", "tipo_carroceria",
        "transmision", "combustible", "ubicacion",
        "kilometraje_max", "garantia", "motor", "limite"
    },
    "obtener_vehiculo": {"id", "slug"},
    "buscar_alternativas": {
        "marca_original", "presupuesto", "modelo_original",
        "tipo_uso", "carroceria", "ubicacion"
    },
    "comparar_vehiculos": {"vehiculo_ids"},
    "estadisticas_inventario": set(),
    "calcular_financiamiento": {
        "precio_vehiculo", "vehiculo_id", "enganche_porcentaje",
        "plazo_meses", "tasa_anual"
    },
    "buscar_informacion": {"pregunta", "categoria"},
    "obtener_info_negocio": {"tema"},
    "obtener_faqs": {"categoria"},
    "solicitar_datos_contacto": {
        "nombre", "telefono", "email",
        "vehiculo_interes", "comentarios"
    },
    "enviar_cotizacion_email": {
        "email_destino", "nombre_cliente", "id",
        "enganche_porcentaje", "plazo_meses"
    },
}

# Canonical response top-level keys per tool
CANONICAL_RESPONSE_KEYS = {
    "buscar_vehiculos": {"vehiculos", "total", "correcciones", "mensaje"},
    "obtener_vehiculo": {
        "id", "titulo", "marca", "modelo", "año", "precio", "precio_numerico",
        "transmision", "combustible", "carroceria", "motor", "cilindros",
        "ubicacion", "kilometraje", "garantia", "descripcion",
        "enganche_minimo", "enganche_recomendado", "mensualidad_desde",
        "mensualidad_recomendada", "plazo_maximo", "con_oferta", "oferta",
        "promociones", "imagen_principal", "galeria_exterior", "galeria_interior",
        "url", "mensaje",
    },
    "calcular_financiamiento": {
        "precio_vehiculo", "enganche_porcentaje", "enganche",
        "monto_a_financiar", "tasa_anual", "plazo_meses",
        "mensualidad_estimada", "total_a_pagar", "costo_financiamiento",
        "nota", "correcciones", "datos_reales",
    },
    "buscar_alternativas": {"alternativas", "total", "correcciones", "mensaje"},
    "comparar_vehiculos": {"vehiculos", "mensaje"},
    "estadisticas_inventario": {
        "total_vehiculos", "rango_precios", "rango_anos",
        "marcas_disponibles", "mensaje",
    },
    "buscar_informacion": {"resultados", "mensaje"},
    "obtener_info_negocio": {"informacion", "mensaje"},
    "obtener_faqs": {"faqs", "mensaje"},
    "solicitar_datos_contacto": {"mensaje", "datos_registrados", "lead_existente"},
    "enviar_cotizacion_email": {"mensaje", "enviado"},
}

# Vehicle item canonical fields
VEHICLE_CANONICAL_FIELDS = {
    "id", "titulo", "marca", "modelo", "año", "precio", "precio_numerico",
    "transmision", "combustible", "carroceria", "ubicacion", "kilometraje",
    "garantia", "enganche_minimo", "mensualidad_desde", "url",
    # detail extras
    "motor", "cilindros", "descripcion", "enganche_recomendado",
    "mensualidad_recomendada", "plazo_maximo", "con_oferta", "oferta",
    "promociones", "imagen_principal", "galeria_exterior", "galeria_interior",
    # compare extra
    "kilometraje_numerico",
}

# Raw DB fields that should NOT appear
RAW_DB_FIELDS = {
    "autoano", "anio", "ano", "liga_web", "liga_mariana", "enganchemin",
    "mensualidad_minima", "plazomax", "feature_image_url", "slug",
}

# Financiamiento keywords in user messages
FINANCIAMIENTO_KEYWORDS = {
    "financiamiento", "financiar", "mensualidad", "mensualidades",
    "crédito", "credito", "enganche", "plazo", "plazos",
    "cuánto quedaría", "cuanto quedaria", "pagos mensuales",
    "pago mensual", "a meses", "a crédito", "a credito",
    "cuotas", "interés", "interes", "tasa",
}

# ─── Issue severity levels ────────────────────────────────────

CRITICAL = "CRITICAL"   # Eliminates conversation
WARNING = "WARNING"     # Flagged but kept (unless many warnings)
INFO = "INFO"           # Informational only

# ─── Helper functions ─────────────────────────────────────────


def extract_tool_calls(content: str) -> list:
    """Extract tool calls from assistant message."""
    calls = []
    idx = 0
    while True:
        start = content.find("<tool_call>", idx)
        if start == -1:
            break
        end = content.find("</tool_call>", start)
        if end == -1:
            break
        inner = content[start + len("<tool_call>"):end].strip()
        if inner.startswith("{"):
            depth = 0
            json_end = -1
            for j, c in enumerate(inner):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        json_end = j + 1
                        break
            if json_end > 0:
                try:
                    obj = json.loads(inner[:json_end])
                    calls.append(obj)
                except json.JSONDecodeError:
                    pass
        idx = end + len("</tool_call>")
    return calls


def extract_tool_response(content: str):
    """Extract JSON from tool response message."""
    # Try multiple closing tag variants
    close_variants = [
        '</tool_response>',
        '</tool_reponse>',
        '</tool_rsponse>',
        '</tool_responce>',
    ]

    for close_tag in close_variants:
        pattern = r'<tool_response>\s*(.*?)\s*' + re.escape(close_tag)
        m = re.search(pattern, content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                # Try balanced braces
                inner = m.group(1).strip()
                if inner.startswith("{"):
                    depth = 0
                    end = -1
                    for i, c in enumerate(inner):
                        if c == '{':
                            depth += 1
                        elif c == '}':
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    if end > 0:
                        try:
                            return json.loads(inner[:end])
                        except:
                            pass

    # Fallback: opening tag without closing
    open_pos = content.find('<tool_response>')
    if open_pos >= 0:
        after = content[open_pos + len('<tool_response>'):].strip()
        if after.startswith("{"):
            depth = 0
            end = -1
            for i, c in enumerate(after):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                try:
                    return json.loads(after[:end])
                except:
                    pass

    return None


def parse_price_num(value) -> float | None:
    """Parse a price string/number to float."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.replace("$", "").replace(",", "").replace(" ", "").strip()
        try:
            return float(s)
        except:
            return None
    return None


def has_financiamiento_intent(user_msgs: list) -> bool:
    """Check if user is asking about financing."""
    text = " ".join(user_msgs).lower()
    return any(kw in text for kw in FINANCIAMIENTO_KEYWORDS)


# ─── Audit functions ──────────────────────────────────────────


def audit_conversation(conv: dict, line_num: int) -> list:
    """
    Audit a single conversation for quality issues.
    Returns list of (severity, issue_description) tuples.
    """
    issues = []
    msgs = conv.get("messages", [])

    if not msgs:
        issues.append((CRITICAL, "empty_conversation"))
        return issues

    # ── Basic structure checks ──

    # Must have system, user, assistant
    roles = [m.get("role") for m in msgs]

    if roles[0] != "system":
        issues.append((CRITICAL, "no_system_prompt"))

    if "user" not in roles:
        issues.append((CRITICAL, "no_user_message"))
        return issues

    if "assistant" not in roles:
        issues.append((CRITICAL, "no_assistant_message"))
        return issues

    # Check for empty messages
    for i, msg in enumerate(msgs):
        content = msg.get("content", "")
        if msg.get("role") in ("user", "assistant") and not content.strip():
            issues.append((WARNING, f"empty_{msg['role']}_message_at_{i}"))

    # ── Tool call validation ──

    user_messages = [m.get("content", "") for m in msgs if m.get("role") == "user"]
    tool_calls_found = []
    tool_responses_found = []

    for i, msg in enumerate(msgs):
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "assistant" and "<tool_call>" in content:
            calls = extract_tool_calls(content)
            for tc in calls:
                name = tc.get("name", "")
                args = tc.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except:
                        args = {}

                tool_calls_found.append((i, name, args))

                # Check: valid tool name
                if name not in VALID_TOOLS:
                    issues.append((CRITICAL, f"invalid_tool_name:{name}"))
                    continue

                # Check: valid argument keys
                valid = VALID_ARGS.get(name, set())
                for key in args:
                    if key not in valid:
                        issues.append((WARNING, f"invalid_arg:{name}.{key}"))

                # Check: required args
                if name == "calcular_financiamiento":
                    if "precio_vehiculo" not in args and "vehiculo_id" not in args:
                        issues.append((CRITICAL, f"calc_fin_missing_precio_and_vehiculo_id"))

                if name == "obtener_vehiculo":
                    if "id" not in args and "slug" not in args:
                        issues.append((CRITICAL, f"obtener_vehiculo_missing_id_and_slug"))

                if name == "comparar_vehiculos":
                    ids = args.get("vehiculo_ids", [])
                    if not isinstance(ids, list) or len(ids) < 2:
                        issues.append((WARNING, f"comparar_vehiculos_insufficient_ids"))

                if name == "enviar_cotizacion_email":
                    if "email_destino" not in args:
                        issues.append((WARNING, f"enviar_cotizacion_missing_email"))
                    if "id" not in args and "vehiculo_id" not in args:
                        issues.append((WARNING, f"enviar_cotizacion_missing_vehicle_id"))
                    # Check for old vehiculo_id instead of id
                    if "vehiculo_id" in args:
                        issues.append((WARNING, f"enviar_cotizacion_uses_vehiculo_id_not_id"))

                # Check: type correctness
                if name in ("buscar_vehiculos", "buscar_alternativas"):
                    for num_field in ("precio_minimo", "precio_maximo", "año_minimo",
                                      "año_maximo", "kilometraje_max", "presupuesto"):
                        if num_field in args and isinstance(args[num_field], str):
                            issues.append((WARNING, f"string_numeric_arg:{name}.{num_field}"))

                if name == "calcular_financiamiento":
                    for num_field in ("precio_vehiculo", "enganche_porcentaje", "plazo_meses", "tasa_anual"):
                        if num_field in args and isinstance(args[num_field], str):
                            issues.append((WARNING, f"string_numeric_arg:{name}.{num_field}"))

        elif role == "tool":
            response_obj = extract_tool_response(content)

            # Find corresponding tool call
            tc_name = None
            for k in range(i - 1, -1, -1):
                if msgs[k].get("role") == "assistant":
                    tc_content = msgs[k].get("content", "")
                    tm = re.search(r'"name"\s*:\s*"([^"]+)"', tc_content)
                    if tm:
                        tc_name = tm.group(1)
                    break

            tool_responses_found.append((i, tc_name, response_obj))

            if response_obj is None:
                issues.append((CRITICAL, f"unparseable_tool_response_at_{i}"))
                continue

            if tc_name and tc_name not in VALID_TOOLS:
                continue  # already flagged

            # Check: response has error field
            if isinstance(response_obj, dict) and "error" in response_obj:
                # Error responses are OK structurally, but check the assistant handles it
                continue

            # Check: remaining wrapper
            if (isinstance(response_obj, dict) and "name" in response_obj
                    and "content" in response_obj
                    and isinstance(response_obj.get("content"), (dict, list, str))):
                issues.append((CRITICAL, f"unwrapped_name_content_wrapper:{tc_name}"))

            # Check: raw DB fields
            response_str = json.dumps(response_obj, ensure_ascii=False)
            for raw_field in RAW_DB_FIELDS:
                if f'"{raw_field}"' in response_str:
                    issues.append((CRITICAL, f"raw_db_field:{raw_field}_in_{tc_name}"))

            # Check: canonical response structure
            if tc_name and tc_name in CANONICAL_RESPONSE_KEYS:
                canonical = CANONICAL_RESPONSE_KEYS[tc_name]
                resp_keys = set(response_obj.keys()) if isinstance(response_obj, dict) else set()
                non_canonical = resp_keys - canonical - {"error"}
                if non_canonical:
                    issues.append((WARNING, f"non_canonical_keys:{tc_name}:{','.join(sorted(non_canonical))}"))

            # Check: vehicle items in list responses
            if tc_name in ("buscar_vehiculos", "buscar_alternativas", "comparar_vehiculos"):
                list_key = {
                    "buscar_vehiculos": "vehiculos",
                    "buscar_alternativas": "alternativas",
                    "comparar_vehiculos": "vehiculos",
                }.get(tc_name)
                items = response_obj.get(list_key, []) if isinstance(response_obj, dict) else []
                if isinstance(items, list):
                    for item in items[:3]:  # check first 3
                        if isinstance(item, dict):
                            item_keys = set(item.keys())
                            non_canonical_item = item_keys - VEHICLE_CANONICAL_FIELDS
                            if non_canonical_item:
                                issues.append((WARNING, f"non_canonical_vehicle_fields:{tc_name}:{','.join(sorted(non_canonical_item))}"))
                                break  # one warning per response is enough

            # Check: calcular_financiamiento specifics
            if tc_name == "calcular_financiamiento" and isinstance(response_obj, dict):
                # mensualidad_estimada should exist and be formatted
                if "mensualidad_estimada" not in response_obj:
                    issues.append((WARNING, f"calc_fin_missing_mensualidad_estimada"))
                else:
                    val = response_obj["mensualidad_estimada"]
                    if isinstance(val, (int, float)):
                        issues.append((WARNING, f"calc_fin_mensualidad_not_formatted"))

                # tasa_anual should be string with %
                if "tasa_anual" in response_obj:
                    val = response_obj["tasa_anual"]
                    if isinstance(val, (int, float)):
                        issues.append((WARNING, f"calc_fin_tasa_not_formatted"))

                # nota should exist
                if "nota" not in response_obj:
                    issues.append((WARNING, f"calc_fin_missing_nota"))

                # Mathematical consistency check
                precio = parse_price_num(response_obj.get("precio_vehiculo"))
                enganche = parse_price_num(response_obj.get("enganche"))
                monto = parse_price_num(response_obj.get("monto_a_financiar"))
                enganche_pct = response_obj.get("enganche_porcentaje")
                if isinstance(enganche_pct, str):
                    try:
                        enganche_pct = float(enganche_pct.replace("%", ""))
                    except:
                        enganche_pct = None

                if precio and enganche and monto:
                    expected_monto = precio - enganche
                    if abs(expected_monto - monto) > precio * 0.05:  # 5% tolerance
                        issues.append((WARNING, f"calc_fin_math_inconsistency:monto"))

                if precio and enganche_pct and enganche:
                    expected_enganche = precio * (enganche_pct / 100)
                    if abs(expected_enganche - enganche) > precio * 0.05:
                        issues.append((WARNING, f"calc_fin_math_inconsistency:enganche"))

    # ── Cross-message coherence checks ──

    # Check: tool call followed by tool response
    for idx, name, args in tool_calls_found:
        # Find corresponding tool response
        found_response = False
        for resp_idx, resp_name, resp_obj in tool_responses_found:
            if resp_idx > idx and resp_name == name:
                found_response = True
                break
        if not found_response:
            issues.append((CRITICAL, f"tool_call_without_response:{name}"))

    # Check: tool response without prior tool call
    for resp_idx, resp_name, resp_obj in tool_responses_found:
        found_call = False
        for call_idx, call_name, call_args in tool_calls_found:
            if call_idx < resp_idx and call_name == resp_name:
                found_call = True
                break
        if not found_call:
            issues.append((CRITICAL, f"orphan_tool_response:{resp_name}"))

    # Check: financiamiento intent → should use calcular_financiamiento
    if has_financiamiento_intent(user_messages):
        tool_names_used = [n for _, n, _ in tool_calls_found]
        # Only flag if user explicitly asks for calculation AND no calc tool used
        fin_explicit = any(
            kw in " ".join(user_messages).lower()
            for kw in ["mensualidad", "mensualidades", "cuánto quedaría",
                        "cuanto quedaria", "calcul", "pago mensual", "pagos mensuales"]
        )
        if fin_explicit and "calcular_financiamiento" not in tool_names_used:
            # Check if the assistant at least mentions financing info from a vehicle
            issues.append((INFO, f"financiamiento_intent_without_calc_tool"))

    # ── Assistant response quality ──

    for i, msg in enumerate(msgs):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")

        # Check for hallucinated prices (mentioning specific prices without tool data)
        # This is a heuristic - if assistant mentions a price but there's no
        # prior tool response with that data
        if not content.strip():
            continue

        # Check: assistant response length
        text_only = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
        if len(text_only) > 2000:
            issues.append((WARNING, f"assistant_response_too_long:{len(text_only)}chars"))

        # Check: assistant uses formal "usted" instead of "tú"
        if re.search(r'\busted\b', text_only.lower()):
            issues.append((INFO, "uses_usted"))

    # ── Conversation flow checks ──

    # Ensure role alternation makes sense
    prev_role = None
    for i, msg in enumerate(msgs):
        role = msg.get("role")
        if role == "tool" and prev_role != "assistant":
            issues.append((CRITICAL, f"tool_response_not_after_assistant_at_{i}"))
        if role == "user" and prev_role == "user":
            issues.append((WARNING, f"consecutive_user_messages_at_{i}"))
        prev_role = role

    # ── calcular_financiamiento completeness ──
    for resp_idx, resp_name, resp_obj in tool_responses_found:
        if resp_name == "calcular_financiamiento" and isinstance(resp_obj, dict):
            if "error" in resp_obj:
                continue
            # Must have at minimum these fields for a useful response
            required_calc_fields = {"mensualidad_estimada", "enganche", "monto_a_financiar"}
            present = set(resp_obj.keys())
            missing = required_calc_fields - present
            if missing:
                issues.append((WARNING, f"calc_fin_incomplete:missing_{','.join(sorted(missing))}"))

            # total_a_pagar is important for the model to learn
            if "total_a_pagar" not in resp_obj:
                issues.append((WARNING, f"calc_fin_missing_total_a_pagar"))

    # ── Empty search results without helpful message ──
    for resp_idx, resp_name, resp_obj in tool_responses_found:
        if resp_name in ("buscar_vehiculos", "buscar_alternativas"):
            if isinstance(resp_obj, dict):
                list_key = "vehiculos" if resp_name == "buscar_vehiculos" else "alternativas"
                items = resp_obj.get(list_key, [])
                if isinstance(items, list) and len(items) == 0 and "mensaje" not in resp_obj:
                    issues.append((WARNING, f"empty_results_no_guidance:{resp_name}"))

    # ── Too-short conversations (< 4 messages total) ──
    non_system = [m for m in msgs if m.get("role") != "system"]
    if len(non_system) < 2:
        issues.append((CRITICAL, "conversation_too_short"))

    return issues


# ─── Filter decision ─────────────────────────────────────────


def should_remove(issues: list, aggressive: bool = True) -> tuple:
    """
    Decide if a conversation should be removed.
    Returns (remove: bool, reason: str).

    aggressive=True: remove on ANY warning or critical (quality > quantity)
    aggressive=False: remove only on critical or 3+ warnings
    """
    critical_count = sum(1 for sev, _ in issues if sev == CRITICAL)
    warning_count = sum(1 for sev, _ in issues if sev == WARNING)

    if critical_count > 0:
        critical_issues = [desc for sev, desc in issues if sev == CRITICAL]
        return True, f"CRITICAL({critical_count}): {'; '.join(critical_issues[:3])}"

    if aggressive:
        # Any warning = remove
        if warning_count > 0:
            warning_issues = [desc for sev, desc in issues if sev == WARNING]
            return True, f"WARNING({warning_count}): {'; '.join(warning_issues[:3])}"
    else:
        # 3+ warnings = remove
        if warning_count >= 3:
            warning_issues = [desc for sev, desc in issues if sev == WARNING]
            return True, f"WARNINGS({warning_count}): {'; '.join(warning_issues[:3])}"

    return False, ""


# ─── Main processing ─────────────────────────────────────────


def process_file(fpath: str, audit_only: bool = False, audit_sample_rate: int = 4, aggressive: bool = True):
    """Process a JSONL file: audit + optionally filter."""
    conversations = []
    with open(fpath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                conversations.append(json.loads(line))

    total = len(conversations)
    print(f"\n  Total conversaciones: {total}")

    # ── Phase 1: Audit (sample 1 in N) ──
    audit_results = []
    all_issues = Counter()

    # Audit ALL conversations for filtering, but only report detailed on sample
    for i, conv in enumerate(conversations):
        issues = audit_conversation(conv, i + 1)
        audit_results.append(issues)
        for sev, desc in issues:
            # Normalize description for counting (remove line numbers)
            base_desc = re.sub(r'_at_\d+', '', desc)
            base_desc = re.sub(r':\d+chars', '', base_desc)
            all_issues[f"{sev}:{base_desc}"] += 1

    # Print detailed audit for sample
    sample_audit = []
    for i in range(0, total, audit_sample_rate):
        if audit_results[i]:
            sample_audit.append((i, audit_results[i]))

    print(f"\n  ── Auditoría detallada (1 de cada {audit_sample_rate}) ──")
    print(f"  Conversaciones auditadas en muestra: {len(range(0, total, audit_sample_rate))}")
    print(f"  Con issues en muestra: {len(sample_audit)}")

    # Issue summary
    print(f"\n  ── Resumen de issues (todas las conversaciones) ──")
    critical_total = sum(v for k, v in all_issues.items() if k.startswith("CRITICAL:"))
    warning_total = sum(v for k, v in all_issues.items() if k.startswith("WARNING:"))
    info_total = sum(v for k, v in all_issues.items() if k.startswith("INFO:"))
    print(f"  CRITICAL: {critical_total}")
    print(f"  WARNING:  {warning_total}")
    print(f"  INFO:     {info_total}")

    print(f"\n  Top issues:")
    for desc, count in sorted(all_issues.items(), key=lambda x: -x[1])[:25]:
        print(f"    {count:5d}  {desc}")

    if audit_only:
        return None, None

    # ── Phase 2: Filter ──
    kept = []
    removed = []
    removal_reasons = Counter()

    for i, conv in enumerate(conversations):
        issues = audit_results[i]
        remove, reason = should_remove(issues, aggressive=aggressive)
        if remove:
            removed.append((i + 1, conv, reason))
            primary = reason.split(":")[1].split(";")[0].strip() if ":" in reason else reason
            removal_reasons[primary] += 1
        else:
            kept.append(conv)

    # ── Deduplication ──
    # Remove conversations with identical user message sequences
    seen_user_texts = {}
    deduped = []
    dedup_removed = 0
    for conv in kept:
        msgs = conv.get("messages", [])
        user_msgs = [m.get("content", "").strip().lower() for m in msgs if m.get("role") == "user"]
        user_key = "|".join(user_msgs)

        if user_key in seen_user_texts:
            dedup_removed += 1
            removed.append((0, conv, "DEDUP: duplicate user messages"))
            removal_reasons["duplicate_conversation"] += 1
        else:
            seen_user_texts[user_key] = True
            deduped.append(conv)

    kept = deduped
    if dedup_removed > 0:
        print(f"  Deduplicadas: {dedup_removed}")

    print(f"\n  ── Filtrado ──")
    print(f"  Mantenidas: {len(kept)}")
    print(f"  Eliminadas: {len(removed)}")

    if removal_reasons:
        print(f"\n  Razones de eliminación:")
        for reason, count in removal_reasons.most_common(20):
            print(f"    {count:5d}  {reason}")

    return kept, removed


def main():
    parser = argparse.ArgumentParser(
        description="Auditoría + filtrado de calidad del dataset de training"
    )
    parser.add_argument("--audit-only", action="store_true",
                        help="Solo auditar, no filtrar")
    parser.add_argument("--file", type=str, default=None,
                        help="Procesar solo este archivo")
    parser.add_argument("--dir", type=str,
                        default=os.path.dirname(os.path.abspath(__file__)),
                        help="Directorio base")
    parser.add_argument("--sample-rate", type=int, default=4,
                        help="Rate de muestreo para auditoría detallada (default: 4 = 1 de cada 4)")
    parser.add_argument("--conservative", action="store_true",
                        help="Modo conservador: solo eliminar con CRITICAL o 3+ warnings")
    args = parser.parse_args()

    BASE = args.dir

    main_files = [
        "merged_v10_together_train.jsonl",
        "merged_v10_together_eval.jsonl",
    ]

    if args.file:
        target = os.path.join(BASE, args.file) if not os.path.isabs(args.file) else args.file
        files = [target]
    else:
        files = [os.path.join(BASE, f) for f in main_files if os.path.exists(os.path.join(BASE, f))]

    mode = "AUDITORÍA" if args.audit_only else "AUDITORÍA + FILTRADO"
    print("=" * 80)
    print(f"  audit_and_filter.py — {mode}")
    print(f"  Archivos: {len(files)}")
    print("=" * 80)

    for fpath in files:
        rel = os.path.relpath(fpath, BASE)
        print(f"\n{'─' * 60}")
        print(f"  Archivo: {rel}")

        kept, removed = process_file(
            fpath,
            audit_only=args.audit_only,
            audit_sample_rate=args.sample_rate,
            aggressive=not args.conservative,
        )

        if kept is not None:
            # Write to NEW files — never alter originals
            filtered_path = fpath.replace(".jsonl", "_filtered.jsonl")
            with open(filtered_path, "w", encoding="utf-8") as f:
                for conv in kept:
                    f.write(json.dumps(conv, ensure_ascii=False) + "\n")

            # Write removed conversations log
            removed_path = fpath.replace(".jsonl", "_removed.jsonl")
            with open(removed_path, "w", encoding="utf-8") as f:
                for line_num, conv, reason in removed:
                    entry = {"_line": line_num, "_reason": reason, **conv}
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            filtered_rel = os.path.relpath(filtered_path, BASE)
            removed_rel = os.path.relpath(removed_path, BASE)
            print(f"\n  Archivo filtrado escrito: {filtered_rel}")
            print(f"  Originales: NO modificados")
            print(f"  Eliminadas guardadas en: {removed_rel}")

    print(f"\n{'=' * 80}")
    print("  Proceso completado")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
