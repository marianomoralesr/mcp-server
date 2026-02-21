#!/usr/bin/env python3
"""
===============================================================
 fix_tool_calls_v10.py — Corrección masiva de tool calls en JSONL
===============================================================

Corrige todos los problemas detectados en los datasets de training:

  1. Argument keys incorrectos → renombrar al schema MCP canónico
  2. Tipos incorrectos: strings "350000" → int 350000
  3. Precios con formato: "$350,000" → 350000
  4. Tool inválido: agendar_cita → solicitar_datos_contacto
  5. estadisticas_inventario: limpiar argumentos (no acepta ninguno)
  6. comparar_vehiculos: merge de id_vehiculo_1/2 → vehiculo_ids[]
  7. enviar_cotizacion_email: email→email_destino, vehiculo→vehiculo_id
  8. Eliminar argument keys que no existen en el schema

Uso:
    # Dry-run (no escribe, solo reporta)
    python3 fix_tool_calls_v10.py --dry-run

    # Aplicar fixes a los archivos principales de training
    python3 fix_tool_calls_v10.py

    # Aplicar a TODOS los jsonl
    python3 fix_tool_calls_v10.py --all

    # Solo un archivo específico
    python3 fix_tool_calls_v10.py --file merged_v10_together_train.jsonl
===============================================================
"""

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ─── Schema MCP canónico ──────────────────────────────────────

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
        "email_destino", "nombre_cliente", "vehiculo_id",
        "enganche_porcentaje", "plazo_meses"
    },
}

# Campos que DEBEN ser numéricos
NUMERIC_FIELDS = {
    "precio_minimo", "precio_maximo", "precio_vehiculo", "presupuesto",
    "año_minimo", "año_maximo", "kilometraje_max", "enganche_porcentaje",
    "plazo_meses", "tasa_anual", "vehiculo_id", "id", "limite",
}

# Campos que son arrays de números
ARRAY_NUMERIC_FIELDS = {"vehiculo_ids"}

# ─── Mapeo de renombrado por tool ─────────────────────────────

# buscar_vehiculos: mapeo de alias → nombre canónico
RENAME_buscar_vehiculos = {
    # Año
    "anio_min": "año_minimo",
    "anio_max": "año_maximo",
    "anio_minimo": "año_minimo",
    "anio_maximo": "año_maximo",
    "anio": None,  # ambiguo, se descarta
    "autoano": None,
    "autoano_desde": "año_minimo",
    "autoano_hasta": "año_maximo",
    "año_min": "año_minimo",
    "año_max": "año_maximo",
    "ano_minimo": "año_minimo",
    "ano_maximo": "año_maximo",
    "ano_min": "año_minimo",
    "ano_max": "año_maximo",
    "year_min": "año_minimo",
    "year_max": "año_maximo",
    "year": None,
    "año": None,
    # Precio
    "precio_max": "precio_maximo",
    "precio_min": "precio_minimo",
    "presupuesto_maximo": "precio_maximo",
    "presupuesto_minimo": "precio_minimo",
    "presupuesto": "precio_maximo",
    "precio": "precio_maximo",
    "budget": "precio_maximo",
    # Carrocería
    "carroceria": "tipo_carroceria",
    "tipo": "tipo_carroceria",
    "tipo_vehiculo": "tipo_carroceria",
    "categoria": "tipo_carroceria",
    "body_type": "tipo_carroceria",
    # Ubicación
    "sucursal": "ubicacion",
    "ciudad": "ubicacion",
    "location": "ubicacion",
    # Kilometraje
    "kilometraje": "kilometraje_max",
    "km_max": "kilometraje_max",
    "km": "kilometraje_max",
    # Límite
    "limit": "limite",
    "max_results": "limite",
    "cantidad": "limite",
}

RENAME_obtener_vehiculo = {
    "id_vehiculo": "id",
    "vehiculo_id": "id",
    "vehicle_id": "id",
    "titulo": None,  # no existe en schema, descartar
}

RENAME_buscar_alternativas = {
    # marca_original ya es válido
    "marca": "marca_original",
    "modelo": "modelo_original",
    "precio_maximo": "presupuesto",
    "precio_max": "presupuesto",
    "presupuesto_maximo": "presupuesto",
    "tipo_carroceria": "carroceria",
    "tipo": "carroceria",
    "tipo_vehiculo": "carroceria",
    "sucursal": "ubicacion",
    "uso": "tipo_uso",
}

RENAME_comparar_vehiculos = {
    "ids": "vehiculo_ids",
    "ids_vehiculos": "vehiculo_ids",
    "vehiculos": "vehiculo_ids",
    # Los pares id_vehiculo_1/2, id_vehiculo1/2, vehiculo_id1/2
    # se manejan con lógica especial (merge_comparar_ids)
}

RENAME_calcular_financiamiento = {
    "precio": "precio_vehiculo",
    "precio_auto": "precio_vehiculo",
    "monto": "precio_vehiculo",
    "costo": "precio_vehiculo",
    "enganche": "enganche_porcentaje",
    "enganche_monto": None,  # monto no es porcentaje, descartar
    "porcentaje_enganche": "enganche_porcentaje",
    "plazo": "plazo_meses",
    "meses": "plazo_meses",
    "tasa_interes": "tasa_anual",
    "tasa": "tasa_anual",
    "interes": "tasa_anual",
    "id_vehiculo": "vehiculo_id",
    "id": "vehiculo_id",
}

RENAME_buscar_informacion = {
    "consulta": "pregunta",
    "query": "pregunta",
    "tema": "categoria",
    "busqueda": "pregunta",
    "texto": "pregunta",
}

RENAME_obtener_info_negocio = {
    "tipo_info": "tema",
    "informacion": "tema",
    "consulta": "tema",
    # Todos los demás args son inválidos y se descartan
    "sucursal": None,
    "ubicacion": None,
    "marca": None,
    "modelo": None,
    "nombre": None,
    "vehiculo": None,
    "precio": None,
    "tipo_vehiculo": None,
    "anio": None,
}

RENAME_obtener_faqs = {
    "tema": "categoria",
    "tipo": "categoria",
    "pregunta": "categoria",
}

RENAME_solicitar_datos_contacto = {
    "nombre_cliente": "nombre",
    "razon": "comentarios",
    "pregunta": "comentarios",
    "ubicacion": None,  # no está en schema
    "sucursal": None,
    "motivo": "comentarios",
    "interes": "vehiculo_interes",
    "vehiculo": "vehiculo_interes",
}

RENAME_enviar_cotizacion_email = {
    "email": "email_destino",
    "correo": "email_destino",
    "email_cliente": "email_destino",
    "nombre": "nombre_cliente",
    "nombre_destinatario": "nombre_cliente",
    "vehiculo": None,  # string, no mapeable a vehiculo_id (numérico)
    "enganche": "enganche_porcentaje",
    "plazo": "plazo_meses",
    "meses": "plazo_meses",
    "id_vehiculo": "vehiculo_id",
    # Campos totalmente inválidos que se descartan
    "mensualidad": None,
    "precio": None,
    "cotizacion": None,
    "tasa": None,
    "marca": None,
    "modelo": None,
    "año": None,
    "color": None,
}

# Mapeo global: tool_name → dict de renombrado
RENAME_MAP = {
    "buscar_vehiculos": RENAME_buscar_vehiculos,
    "obtener_vehiculo": RENAME_obtener_vehiculo,
    "buscar_alternativas": RENAME_buscar_alternativas,
    "comparar_vehiculos": RENAME_comparar_vehiculos,
    "calcular_financiamiento": RENAME_calcular_financiamiento,
    "buscar_informacion": RENAME_buscar_informacion,
    "obtener_info_negocio": RENAME_obtener_info_negocio,
    "obtener_faqs": RENAME_obtener_faqs,
    "solicitar_datos_contacto": RENAME_solicitar_datos_contacto,
    "enviar_cotizacion_email": RENAME_enviar_cotizacion_email,
}

# ─── Mapeo de tema válido para obtener_info_negocio ───────────

VALID_TEMAS = {
    "horarios", "ubicaciones", "contacto", "garantias",
    "financiamiento", "documentos_requeridos", "proceso_compra",
    "devoluciones", "intercambio", "servicios"
}

TEMA_ALIASES = {
    "horario": "horarios",
    "ubicacion": "ubicaciones",
    "sucursales": "ubicaciones",
    "sucursal": "ubicaciones",
    "garantia": "garantias",
    "garantías": "garantias",
    "documentos": "documentos_requeridos",
    "docs": "documentos_requeridos",
    "requisitos": "documentos_requeridos",
    "compra": "proceso_compra",
    "proceso": "proceso_compra",
    "devolucion": "devoluciones",
    "devolución": "devoluciones",
    "cambio": "intercambio",
    "servicio": "servicios",
    "telefono": "contacto",
    "email": "contacto",
    "correo": "contacto",
    "financiar": "financiamiento",
    "credito": "financiamiento",
    "crédito": "financiamiento",
}

# ─── Funciones de conversión ──────────────────────────────────


def parse_money(value):
    """Convierte strings de precio a número: '$350,000' → 350000."""
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value

    s = value.strip()
    # Quitar $ y espacios
    s = s.replace("$", "").replace(" ", "").replace("\u00a0", "")
    # Quitar MXN/pesos sufijo
    s = re.sub(r'\s*(MXN|mxn|pesos|Pesos)\s*$', '', s)
    # Quitar comas de miles
    s = s.replace(",", "")
    # Intentar convertir
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return value  # no se pudo convertir, devolver original


def to_number(value):
    """Convierte un string a número si es posible."""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return parse_money(value)
    return value


def normalize_tema(value):
    """Normaliza el valor de 'tema' en obtener_info_negocio."""
    if not isinstance(value, str):
        return value
    v = value.lower().strip()
    if v in VALID_TEMAS:
        return v
    if v in TEMA_ALIASES:
        return TEMA_ALIASES[v]
    # Fuzzy match
    for valid in VALID_TEMAS:
        if valid in v or v in valid:
            return valid
    return value


def merge_comparar_ids(args):
    """
    Merge pares de ID separados en vehiculo_ids[].
    Maneja: id_vehiculo_1/2, id_vehiculo1/2, vehiculo_id1/2, etc.
    """
    ids = []

    # Recoger IDs ya en formato array
    if "vehiculo_ids" in args:
        val = args["vehiculo_ids"]
        if isinstance(val, list):
            ids.extend(val)
        elif isinstance(val, (int, float)):
            ids.append(int(val))
        elif isinstance(val, str):
            # Puede ser "[1, 2]" o "1, 2"
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    ids.extend(parsed)
            except:
                for part in re.split(r'[,\s]+', val):
                    part = part.strip()
                    if part:
                        try:
                            ids.append(int(part))
                        except ValueError:
                            pass

    # Recoger "ids" (alias)
    for key in ("ids", "ids_vehiculos"):
        if key in args:
            val = args[key]
            if isinstance(val, list):
                ids.extend(val)
            elif isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        ids.extend(parsed)
                except:
                    pass

    # Recoger pares numerados
    pair_patterns = [
        ("id_vehiculo_1", "id_vehiculo_2", "id_vehiculo_3", "id_vehiculo_4"),
        ("id_vehiculo1", "id_vehiculo2", "id_vehiculo3", "id_vehiculo4"),
        ("vehiculo_id1", "vehiculo_id2", "vehiculo_id3", "vehiculo_id4"),
        ("vehiculo_id_1", "vehiculo_id_2", "vehiculo_id_3", "vehiculo_id_4"),
        ("id1", "id2", "id3", "id4"),
    ]
    for pattern_keys in pair_patterns:
        for pk in pattern_keys:
            if pk in args:
                val = args[pk]
                num = to_number(val)
                if isinstance(num, (int, float)):
                    ids.append(int(num))

    # Recoger "modelos" si es una lista de IDs numéricos
    if "modelos" in args:
        val = args["modelos"]
        if isinstance(val, list):
            for v in val:
                num = to_number(v)
                if isinstance(num, (int, float)):
                    ids.append(int(num))

    # "vehiculos" como lista de IDs
    if "vehiculos" in args:
        val = args["vehiculos"]
        if isinstance(val, list):
            for v in val:
                num = to_number(v)
                if isinstance(num, (int, float)):
                    ids.append(int(num))

    # Deduplicar preservando orden
    seen = set()
    unique_ids = []
    for vid in ids:
        num = to_number(vid)
        if isinstance(num, (int, float)):
            num = int(num)
        if num not in seen:
            seen.add(num)
            unique_ids.append(num)

    return {"vehiculo_ids": unique_ids} if unique_ids else {"vehiculo_ids": []}


def convert_agendar_to_contacto(args):
    """Convierte argumentos de agendar_cita a solicitar_datos_contacto."""
    new_args = {}
    mapping = {
        "nombre_cliente": "nombre",
        "nombre": "nombre",
        "telefono": "telefono",
        "tel": "telefono",
        "email": "email",
        "correo": "email",
        "vehiculo": "vehiculo_interes",
        "vehiculo_interes": "vehiculo_interes",
    }
    comment_parts = []
    for k, v in args.items():
        if k in mapping:
            new_args[mapping[k]] = v
        else:
            # Cosas como sucursal, fecha, hora → meter en comentarios
            if isinstance(v, str) and v.strip():
                comment_parts.append(f"{k}: {v}")

    if comment_parts:
        existing = new_args.get("comentarios", "")
        extra = "; ".join(comment_parts)
        new_args["comentarios"] = f"{existing}; {extra}".strip("; ") if existing else extra

    return new_args


# ─── Fix de un tool_call individual ───────────────────────────


def fix_tool_call(tc_obj, stats):
    """
    Recibe un dict {"name": "...", "arguments": {...}} y lo corrige.
    Devuelve el dict corregido y actualiza stats.
    """
    name = tc_obj.get("name", "")
    args = tc_obj.get("arguments", {})

    # Si arguments es string, parsear
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if not isinstance(args, dict):
        args = {}

    # --- 1. Tool inválido: agendar_cita → solicitar_datos_contacto ---
    if name == "agendar_cita":
        stats["tool_renamed"] += 1
        stats["agendar_cita_converted"] += 1
        name = "solicitar_datos_contacto"
        args = convert_agendar_to_contacto(args)
        return {"name": name, "arguments": args}

    # Si el tool no está en schema, dejarlo (podría ser intencional)
    if name not in VALID_TOOLS:
        stats["unknown_tool"] += 1
        return tc_obj

    # --- 2. Caso especial: estadisticas_inventario (no acepta args) ---
    if name == "estadisticas_inventario":
        if args:
            stats["estadisticas_args_stripped"] += len(args)
            args = {}
        return {"name": name, "arguments": args}

    # --- 3. Caso especial: comparar_vehiculos (merge IDs) ---
    if name == "comparar_vehiculos":
        new_args = merge_comparar_ids(args)
        if new_args != args:
            stats["comparar_ids_merged"] += 1
        args = new_args
        # Asegurar que vehiculo_ids sea lista de ints
        if "vehiculo_ids" in args:
            fixed = []
            for v in args["vehiculo_ids"]:
                num = to_number(v)
                if isinstance(num, (int, float)):
                    fixed.append(int(num))
            args["vehiculo_ids"] = fixed
        return {"name": name, "arguments": args}

    # --- 4. Renombrar argument keys ---
    rename_map = RENAME_MAP.get(name, {})
    new_args = {}
    for key, value in args.items():
        if key in VALID_ARGS.get(name, set()):
            # Ya es válido
            new_args[key] = value
        elif key in rename_map:
            new_key = rename_map[key]
            if new_key is None:
                # Descartar este campo
                stats["args_dropped"] += 1
                continue
            if new_key not in new_args:  # no sobreescribir
                new_args[new_key] = value
                stats["args_renamed"] += 1
            else:
                stats["args_dropped"] += 1  # ya existe, descartar duplicado
        else:
            # Key no reconocido y no está en rename_map
            # Intentar match parcial antes de descartar
            dropped = False
            for alias, canonical in rename_map.items():
                if alias in key or key in alias:
                    if canonical and canonical not in new_args:
                        new_args[canonical] = value
                        stats["args_renamed"] += 1
                        dropped = True
                        break
            if not dropped:
                # Descartar keys totalmente desconocidos
                stats["args_dropped"] += 1

    args = new_args

    # --- 5. Normalizar tema en obtener_info_negocio ---
    if name == "obtener_info_negocio" and "tema" in args:
        old_tema = args["tema"]
        args["tema"] = normalize_tema(old_tema)
        if args["tema"] != old_tema:
            stats["tema_normalized"] += 1

    # --- 6. Convertir tipos: strings → numbers ---
    for key in list(args.keys()):
        if key in NUMERIC_FIELDS:
            old_val = args[key]
            new_val = to_number(old_val)
            if new_val != old_val:
                args[key] = new_val
                stats["types_fixed"] += 1
        elif key in ARRAY_NUMERIC_FIELDS:
            if isinstance(args[key], list):
                fixed = []
                for v in args[key]:
                    num = to_number(v)
                    if isinstance(num, (int, float)):
                        fixed.append(int(num))
                    else:
                        fixed.append(v)
                if fixed != args[key]:
                    stats["types_fixed"] += 1
                args[key] = fixed

    # --- 7. obtener_vehiculo: si "id" es string no-numérico, mover a "slug" ---
    if name == "obtener_vehiculo" and "id" in args:
        val = args["id"]
        num = to_number(val)
        if isinstance(num, (int, float)):
            args["id"] = int(num)
        elif isinstance(val, str) and not val.isdigit():
            # Es un slug, no un ID numérico
            args["slug"] = val
            del args["id"]
            stats["id_to_slug"] += 1

    # --- 8. calcular_financiamiento/enviar_cotizacion: vehiculo_id string → number ---
    if name in ("calcular_financiamiento", "enviar_cotizacion_email") and "vehiculo_id" in args:
        val = args["vehiculo_id"]
        num = to_number(val)
        if isinstance(num, (int, float)):
            args["vehiculo_id"] = int(num)
        elif isinstance(val, str) and not val.isdigit():
            # No es numérico, descartar (no podemos mapear slug a ID)
            del args["vehiculo_id"]
            stats["args_dropped"] += 1

    return {"name": name, "arguments": args}


# ─── Procesar un mensaje completo ─────────────────────────────


def extract_tool_call_spans(content):
    """
    Extrae spans de <tool_call>JSON</tool_call> usando balanced braces.
    Devuelve lista de (start, end, json_string) donde start/end son
    posiciones en content del bloque completo <tool_call>...</tool_call>.
    """
    spans = []
    idx = 0
    tag_open = "<tool_call>"
    tag_close = "</tool_call>"

    while True:
        start = content.find(tag_open, idx)
        if start == -1:
            break
        end_tag = content.find(tag_close, start)
        if end_tag == -1:
            break

        inner = content[start + len(tag_open):end_tag].strip()

        # Encontrar JSON con braces balanceados
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
                spans.append((start, end_tag + len(tag_close), inner[:json_end]))

        idx = end_tag + len(tag_close)

    return spans


def fix_message_content(content, stats):
    """
    Encuentra todos los bloques <tool_call>{JSON}</tool_call> en content,
    los corrige y devuelve el content actualizado.
    Usa balanced brace extraction para soportar JSON anidado.
    """
    if not isinstance(content, str):
        return content

    spans = extract_tool_call_spans(content)
    if not spans:
        return content

    # Procesar de atrás hacia adelante para no alterar offsets
    result = content
    for start, end, tc_json in reversed(spans):
        try:
            tc_obj = json.loads(tc_json)
        except json.JSONDecodeError:
            stats["json_parse_errors"] += 1
            continue  # dejar sin cambios

        fixed = fix_tool_call(tc_obj, stats)
        fixed_json = json.dumps(fixed, ensure_ascii=False, separators=(",", ": "))
        replacement = f"<tool_call>\n{fixed_json}\n</tool_call>"
        result = result[:start] + replacement + result[end:]

    return result


# ─── Procesar un archivo JSONL completo ───────────────────────


def process_file(fpath, dry_run=False):
    """Procesa un archivo JSONL, corrige tool calls, escribe resultado."""
    stats = Counter()
    lines_in = 0
    lines_out = 0
    fixed_lines = []

    with open(fpath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            lines_in += 1

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                stats["json_line_errors"] += 1
                fixed_lines.append(line)
                continue

            messages = obj.get("messages", [])
            modified = False

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                # Solo procesar tool_calls en mensajes assistant (no system prompts con templates)
                if role == "assistant" and isinstance(content, str) and "<tool_call>" in content:
                    old_content = content
                    new_content = fix_message_content(content, stats)
                    if new_content != old_content:
                        msg["content"] = new_content
                        modified = True

            if modified:
                stats["conversations_modified"] += 1

            fixed_lines.append(json.dumps(obj, ensure_ascii=False))
            lines_out += 1

    stats["lines_in"] = lines_in
    stats["lines_out"] = lines_out

    # Escribir resultado
    if not dry_run and stats["conversations_modified"] > 0:
        # Backup
        backup_path = fpath + ".pre_fix"
        if not os.path.exists(backup_path):
            shutil.copy2(fpath, backup_path)

        with open(fpath, "w", encoding="utf-8") as f:
            for line in fixed_lines:
                f.write(line + "\n")

    return dict(stats)


# ─── Main ─────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Corrección masiva de tool calls en datasets JSONL TREFA v10"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo reportar, no escribir cambios")
    parser.add_argument("--all", action="store_true",
                        help="Procesar TODOS los JSONL (no solo los principales)")
    parser.add_argument("--file", type=str, default=None,
                        help="Procesar solo este archivo")
    parser.add_argument("--dir", type=str,
                        default="/Users/marianomorales/Downloads/fine-tuning/inference/datasets",
                        help="Directorio base de datasets")
    args = parser.parse_args()

    BASE = args.dir

    # Archivos principales de training
    main_files = [
        "merged_v10_together_train.jsonl",
        "merged_v10_together_eval.jsonl",
        "v10_real_train.jsonl",
        "v10_real_eval.jsonl",
        "v8_train.jsonl",
        "v8_eval.jsonl",
        "v8_completo.jsonl",
        "trefa_train.jsonl",
        "trefa_eval.jsonl",
        "trefa_train_cleaned.jsonl",
        "trefa_eval_cleaned.jsonl",
        "mariana_training_v1.jsonl",
        "mariana_training_v1_cleaned.jsonl",
        "semillas_mariana.jsonl",
        "semillas_solicitud_financiamiento.jsonl",
        "semillas_obtener_vehiculo.jsonl",
        "etapa1_40_calidad_gold.jsonl",
        "golden_qwen_mariana.jsonl",
        "saludos_mariana.jsonl",
        "estilo_conversacional_mariana.jsonl",
    ]

    # Determinar qué archivos procesar
    if args.file:
        target = os.path.join(BASE, args.file) if not os.path.isabs(args.file) else args.file
        if not os.path.exists(target):
            print(f"ERROR: Archivo no encontrado: {target}")
            sys.exit(1)
        files_to_process = [target]
    elif args.all:
        skip_patterns = ["removed_", "_rejected", ".bak", ".pre_fix", "_dry_run"]
        files_to_process = []
        for root, dirs, fnames in sorted(os.walk(BASE)):
            for fname in sorted(fnames):
                if fname.endswith(".jsonl") and not any(p in fname for p in skip_patterns):
                    files_to_process.append(os.path.join(root, fname))
    else:
        files_to_process = []
        for fname in main_files:
            fpath = os.path.join(BASE, fname)
            if os.path.exists(fpath):
                files_to_process.append(fpath)

        # También procesar gold_upgraded batches y v5/v6 datasets
        for subdir in ["gold_upgraded", "v5_dataset", "v6_dataset"]:
            subpath = os.path.join(BASE, subdir)
            if os.path.isdir(subpath):
                for fname in sorted(os.listdir(subpath)):
                    if fname.endswith(".jsonl") and "removed_" not in fname and ".pre_fix" not in fname:
                        files_to_process.append(os.path.join(subpath, fname))

    if not files_to_process:
        print("No se encontraron archivos JSONL para procesar")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "APLICANDO FIXES"
    print("=" * 80)
    print(f"  fix_tool_calls_v10.py — {mode}")
    print(f"  Archivos: {len(files_to_process)}")
    print("=" * 80)

    global_stats = Counter()
    file_results = []

    for fpath in files_to_process:
        rel = os.path.relpath(fpath, BASE)
        stats = process_file(fpath, dry_run=args.dry_run)

        for k, v in stats.items():
            global_stats[k] += v

        if stats.get("conversations_modified", 0) > 0:
            file_results.append((rel, stats))
            print(f"\n  {'─' * 60}")
            print(f"  {rel}")
            print(f"    Líneas: {stats['lines_in']}")
            print(f"    Conversaciones modificadas: {stats['conversations_modified']}")
            if stats.get("args_renamed"):
                print(f"    Args renombrados: {stats['args_renamed']}")
            if stats.get("args_dropped"):
                print(f"    Args descartados: {stats['args_dropped']}")
            if stats.get("types_fixed"):
                print(f"    Tipos corregidos (str→num): {stats['types_fixed']}")
            if stats.get("agendar_cita_converted"):
                print(f"    agendar_cita → solicitar_datos_contacto: {stats['agendar_cita_converted']}")
            if stats.get("estadisticas_args_stripped"):
                print(f"    estadisticas_inventario args limpiados: {stats['estadisticas_args_stripped']}")
            if stats.get("comparar_ids_merged"):
                print(f"    comparar_vehiculos IDs mergeados: {stats['comparar_ids_merged']}")
            if stats.get("tema_normalized"):
                print(f"    Temas normalizados: {stats['tema_normalized']}")
        else:
            print(f"  {rel}: sin cambios ({stats.get('lines_in', 0)} líneas)")

    # Resumen global
    print(f"\n{'=' * 80}")
    print("  RESUMEN GLOBAL")
    print(f"{'=' * 80}")
    print(f"  Archivos procesados:              {len(files_to_process)}")
    print(f"  Archivos modificados:             {len(file_results)}")
    print(f"  Total conversaciones:             {global_stats['lines_in']}")
    print(f"  Conversaciones modificadas:       {global_stats['conversations_modified']}")
    print(f"  Arguments renombrados:            {global_stats['args_renamed']}")
    print(f"  Arguments descartados:            {global_stats['args_dropped']}")
    print(f"  Tipos corregidos (str→num):       {global_stats['types_fixed']}")
    print(f"  agendar_cita convertidos:         {global_stats['agendar_cita_converted']}")
    print(f"  estadisticas args limpiados:      {global_stats['estadisticas_args_stripped']}")
    print(f"  comparar IDs mergeados:           {global_stats['comparar_ids_merged']}")
    print(f"  Temas normalizados:               {global_stats['tema_normalized']}")
    print(f"  Errores JSON parse:               {global_stats['json_parse_errors']}")
    print(f"  Errores JSON línea:               {global_stats['json_line_errors']}")

    if args.dry_run:
        print(f"\n  MODO DRY-RUN: no se escribieron cambios")
        print(f"  Ejecuta sin --dry-run para aplicar las correcciones")
    else:
        print(f"\n  Backups guardados como *.pre_fix")
        print(f"  Correcciones aplicadas exitosamente")


if __name__ == "__main__":
    main()
