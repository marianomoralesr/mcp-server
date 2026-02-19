#!/usr/bin/env python3
"""
merge_datasets.py v2 — Alineado con producción MCP

Combina together_train.jsonl (v5, 986) + v10_real_train.jsonl (1,557)
en un archivo limpio para Qwen fine-tuning.

Pipeline por entrada:
  0. Normalizar comillas tipográficas
  1. Reemplazar system prompt (v10)
  2. Eliminar mensajes con contenido vacío
  3. Eliminar tool calls de herramientas no-producción + sus tool responses
  4. Normalizar nombres de argumentos en tool_calls
  5. Convertir enganche montos absolutos → porcentajes
  6. Fusionar mensajes assistant consecutivos
  7. Eliminar saludos duplicados post-presentación
  8. Limpiar URLs fabricadas
  9. Corregir secuencia de roles
"""

import json
import re
from pathlib import Path

# ── Rutas ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

# Train split
TOGETHER_TRAIN_PATH = BASE_DIR / "v5_dataset" / "together" / "together_train.jsonl"
V10_TRAIN_PATH = BASE_DIR / "v10_real_train.jsonl"
OUTPUT_TRAIN_PATH = BASE_DIR / "merged_v10_together_train.jsonl"

# Eval split
TOGETHER_EVAL_PATH = BASE_DIR / "v5_dataset" / "together" / "together_eval.jsonl"
V10_EVAL_PATH = BASE_DIR / "v10_real_eval.jsonl"
OUTPUT_EVAL_PATH = BASE_DIR / "merged_v10_together_eval.jsonl"

# ── Herramientas que NO existen en producción MCP ──────────────────────
NON_PRODUCTION_TOOLS = {
    "solicitar_datos_contacto",
    "enviar_cotizacion_email",
    "agendar_cita",
}

# ── Argumentos canónicos — alineados con producción MCP ────────────────
CANONICAL_ARGS = {
    "buscar_vehiculos": {
        "marca", "modelo", "anio_min", "anio_max",
        "precio_min", "precio_max", "tipo", "transmision",
        "combustible", "ubicacion", "kilometraje_max", "garantia",
        "limit",
    },
    "obtener_vehiculo": {"id", "slug", "record_id"},
    "buscar_alternativas": {
        "marca_original", "modelo_original", "presupuesto",
        "tipo_uso", "carroceria", "ubicacion",
    },
    "comparar_vehiculos": {"vehiculo_ids"},
    "calcular_financiamiento": {
        "vehiculo_id", "precio_vehiculo",
        "enganche_porcentaje", "plazo_meses", "tasa_anual",
    },
    "obtener_info_negocio": {"tema"},
    "estadisticas_inventario": {"agrupacion"},
    "buscar_informacion": {"consulta"},
    "obtener_faqs": {"categoria"},
}

# ── Mapeo de variantes → nombre canónico ───────────────────────────────
ARG_RENAME = {
    "buscar_vehiculos": {
        "año_minimo": "anio_min",
        "año_maximo": "anio_max",
        "anio_minimo": "anio_min",
        "anio_maximo": "anio_max",
        "autoano_desde": "anio_min",
        "autoano_hasta": "anio_max",
        "autoano": "anio_min",
        "anio": "anio_min",
        "precio_maximo": "precio_max",
        "precio_minimo": "precio_min",
        "presupuesto_maximo": "precio_max",
        "presupuesto": "precio_max",
        "carroceria": "tipo",
        "tipo_carroceria": "tipo",
        "tipo_vehiculo": "tipo",
        "sucursal": "ubicacion",
        "limite": "limit",
    },
    "obtener_vehiculo": {
        "id_vehiculo": "id",
        "vehiculo_id": "id",
    },
    "buscar_alternativas": {
        "precio_max": "presupuesto",
        "precio_maximo": "presupuesto",
        "vehiculo_id": None,   # Drop — no existe en producción
        "limit": None,
        "limite": None,
    },
    "comparar_vehiculos": {
        "ids": "vehiculo_ids",
        "ids_vehiculos": "vehiculo_ids",
        "vehiculos": "vehiculo_ids",
    },
    "calcular_financiamiento": {
        "enganche": "enganche_porcentaje",
        "enganche_monto": "enganche_porcentaje",
        "plazo": "plazo_meses",
    },
    "obtener_info_negocio": {
        "tipo_info": "tema",
        "tipo_informacion": "tema",
        "informacion": "tema",
    },
    "buscar_informacion": {
        "pregunta": "consulta",
        "query": "consulta",
        "tema": "consulta",
        "categoria": "consulta",
    },
    "obtener_faqs": {
        "tema": "categoria",
    },
}

# Pares de IDs individuales en comparar_vehiculos → vehiculo_ids array
COMPARAR_ID_PAIRS = [
    ("id_vehiculo_1", "id_vehiculo_2"),
    ("id_vehiculo1", "id_vehiculo2"),
    ("vehiculo_id1", "vehiculo_id2"),
]

# Regex para <tool_call>{...}</tool_call> con un nivel de anidamiento
TOOL_CALL_RE = re.compile(
    r"(<tool_call>\s*)"
    r"(\{(?:[^{}]|\{[^{}]*\})*\})"
    r"(\s*</tool_call>)",
    re.DOTALL,
)

# Comillas tipográficas → ASCII (sanitizeJsonText las rompe)
TYPOGRAPHIC_QUOTES = str.maketrans({
    "\u201C": '"', "\u201D": '"', "\u201E": '"', "\u201F": '"',
    "\u2033": '"', "\u2036": '"',
    "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
    "\u2032": "'", "\u2035": "'",
})

# Detecta introducción de Mariana
INTRO_RE = re.compile(r"[Ss]oy Mariana|me llamo Mariana")

# Prefijo de saludo redundante
GREETING_PREFIX_RE = re.compile(
    r"^[¡!]?\s*[Hh]ola[,!]?\s*"
    r"(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?"
    r"[,!]*\s*"
    r"(?:😊)?\s*"
    r"[,!.]*\s*"
)

# URLs fabricadas — el modelo NO debe generar URLs
FABRICATED_URL_RE = re.compile(
    r'https?://(?:maps\.app\.goo\.gl|maps\.google\.com|goo\.gl|autostrefa\.mx)\S*',
    re.IGNORECASE,
)


# ── Funciones ─────────────────────────────────────────────────────────

def get_canonical_system_prompt():
    """Extrae system prompt de together y alinea herramientas con producción MCP."""
    with open(TOGETHER_TRAIN_PATH) as f:
        entry = json.loads(f.readline())
    prompt = entry["messages"][0]["content"]

    # Encontrar el bloque <tools>...</tools> con definiciones JSON
    tools_match = re.search(r'(<tools>\s*\n)(.*?)(\n\s*</tools>)', prompt, re.DOTALL)
    if not tools_match:
        return prompt

    tools_content = tools_match.group(2)

    # Parsear cada definición de herramienta (un JSON por línea)
    tool_defs = []
    for line in tools_content.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            tool_defs.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Filtrar y modificar herramientas
    updated = []
    for tool in tool_defs:
        fn = tool.get("function", {})
        name = fn.get("name", "")

        # Saltar herramientas no-producción
        if name in NON_PRODUCTION_TOOLS:
            continue

        if name == "calcular_financiamiento":
            fn["parameters"]["properties"] = {
                "vehiculo_id": {"type": "integer", "description": "ID del vehículo"},
                "precio_vehiculo": {"type": "number", "description": "Precio del vehículo (alternativo a vehiculo_id)"},
                "enganche_porcentaje": {"type": "number", "description": "Porcentaje de enganche (ej: 20 para 20%). Default: 20"},
                "plazo_meses": {"type": "integer", "description": "Plazo del crédito en meses. Default: 48"},
                "tasa_anual": {"type": "number", "description": "Tasa de interés anual (ej: 15 para 15%). Default: 15"},
            }

        elif name == "buscar_alternativas":
            fn["parameters"]["properties"] = {
                "marca_original": {"type": "string", "description": "Marca del vehículo original (se excluirá de resultados)"},
                "modelo_original": {"type": "string", "description": "Modelo del vehículo original"},
                "presupuesto": {"type": "number", "description": "Presupuesto del cliente en MXN"},
                "tipo_uso": {"type": "string", "description": "Tipo de uso: familiar, trabajo, etc."},
                "carroceria": {"type": "string", "description": "Tipo de carrocería deseada"},
                "ubicacion": {"type": "string", "description": "Ubicación preferida"},
            }

        elif name == "buscar_vehiculos":
            props = fn.get("parameters", {}).get("properties", {})
            if "combustible" not in props:
                props["combustible"] = {"type": "string", "description": "Tipo de combustible"}
            if "kilometraje_max" not in props:
                props["kilometraje_max"] = {"type": "number", "description": "Kilometraje máximo"}
            if "garantia" not in props:
                props["garantia"] = {"type": "string", "description": "Tipo de garantía"}
            fn["parameters"]["properties"] = props

        tool["function"] = fn
        updated.append(tool)

    # Reconstruir bloque <tools>
    new_tools = '\n'.join(json.dumps(t, ensure_ascii=False) for t in updated)
    new_block = f"{tools_match.group(1)}{new_tools}{tools_match.group(3)}"
    prompt = prompt[:tools_match.start()] + new_block + prompt[tools_match.end():]
    return prompt


def normalize_args(fn_name: str, args: dict) -> dict:
    """Renombra variantes y descarta args no canónicos."""
    if not isinstance(args, dict):
        return args

    # Caso especial: comparar_vehiculos con IDs individuales → array
    if fn_name == "comparar_vehiculos":
        for k1, k2 in COMPARAR_ID_PAIRS:
            if k1 in args and k2 in args:
                args["vehiculo_ids"] = [args.pop(k1), args.pop(k2)]

    renames = ARG_RENAME.get(fn_name, {})
    canonical = CANONICAL_ARGS.get(fn_name, set())
    new_args = {}
    for key, val in args.items():
        mapped = renames.get(key, key)
        if mapped is None:
            continue  # Arg marcado para eliminar
        if mapped in canonical:
            new_args[mapped] = val
    return new_args


def normalize_tool_calls_in_content(content: str):
    """Normaliza argumentos en todos los <tool_call> de un mensaje."""
    if not content or "<tool_call>" not in content:
        return content, 0

    fixes = 0

    def _replace(match):
        nonlocal fixes
        prefix, json_str, suffix = match.groups()
        try:
            call = json.loads(json_str)
        except json.JSONDecodeError:
            return match.group(0)

        fn_name = call.get("name", "")
        args = call.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return match.group(0)

        new_args = normalize_args(fn_name, args)
        if new_args != args:
            fixes += 1
        call["arguments"] = new_args
        return f"{prefix}{json.dumps(call, ensure_ascii=False)}{suffix}"

    new_content = TOOL_CALL_RE.sub(_replace, content)
    return new_content, fixes


def remove_non_production_tools(messages: list):
    """Elimina tool calls de herramientas no-producción y sus tool responses."""
    removed = 0
    result = []
    pending_skip = 0

    for msg in messages:
        # Saltar tool responses de herramientas eliminadas
        if msg["role"] == "tool" and pending_skip > 0:
            pending_skip -= 1
            removed += 1
            continue

        if msg["role"] == "assistant" and "<tool_call>" in (msg.get("content") or ""):
            content = msg["content"]
            calls_removed = 0

            def _filter(match):
                nonlocal calls_removed
                prefix, json_str, suffix = match.groups()
                try:
                    call = json.loads(json_str)
                    if call.get("name") in NON_PRODUCTION_TOOLS:
                        calls_removed += 1
                        return ""
                except json.JSONDecodeError:
                    pass
                return match.group(0)

            new_content = TOOL_CALL_RE.sub(_filter, content).strip()
            pending_skip += calls_removed
            removed += calls_removed

            if not new_content:
                continue  # Mensaje vacío tras eliminar tool call
            msg = dict(msg)
            msg["content"] = new_content

        result.append(msg)

    return result, removed


def _extract_price(val):
    """Extrae precio numérico de varios formatos."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = re.sub(r'[$,\s]', '', val)
        cleaned = re.sub(r'\s*MXN\s*$', '', cleaned, flags=re.IGNORECASE)
        try:
            p = float(cleaned)
            return p if p > 1000 else None
        except ValueError:
            return None
    return None


def collect_vehicle_prices(messages: list) -> dict:
    """Recolecta precios de vehículos de tool responses en la conversación."""
    prices = {}

    for msg in messages:
        if msg["role"] != "tool":
            continue
        content = msg.get("content", "")
        if not content:
            continue

        try:
            data = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(data, dict):
            continue

        def _extract(d):
            vid = d.get("id")
            for pk in ("precio", "precio_numerico", "precio_vehiculo"):
                p = _extract_price(d.get(pk))
                if p:
                    if vid:
                        prices[str(vid)] = p
                    prices["_last"] = p
                    break

        _extract(data)
        for key in ("resultado", "vehiculo", "datos", "data"):
            nested = data.get(key)
            if isinstance(nested, dict):
                _extract(nested)
        for key in ("resultados", "vehiculos", "alternativas"):
            items = data.get(key, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        _extract(item)

    return prices


def convert_enganche_values(messages: list):
    """Convierte montos absolutos de enganche a porcentajes."""
    prices = collect_vehicle_prices(messages)

    conversions = 0

    for msg in messages:
        if msg["role"] != "assistant":
            continue
        content = msg.get("content") or ""
        if "calcular_financiamiento" not in content:
            continue

        def _convert(match):
            nonlocal conversions
            prefix, json_str, suffix = match.groups()
            try:
                call = json.loads(json_str)
            except json.JSONDecodeError:
                return match.group(0)

            if call.get("name") != "calcular_financiamiento":
                return match.group(0)

            args = call.get("arguments", {})
            enganche = args.get("enganche_porcentaje")
            if enganche is None:
                return match.group(0)

            try:
                eng_val = float(enganche)
            except (TypeError, ValueError):
                return match.group(0)

            if eng_val <= 100:
                return match.group(0)  # Ya es porcentaje

            # Buscar precio: primero en los propios args, luego en tool responses
            price = _extract_price(args.get("precio_vehiculo"))
            if not price:
                vid = str(args.get("vehiculo_id", ""))
                price = prices.get(vid) or prices.get("_last")

            if price and price > 0:
                pct = round(eng_val / price * 100)
                pct = max(1, min(90, pct))
                args["enganche_porcentaje"] = pct
            else:
                args["enganche_porcentaje"] = 20  # Default

            call["arguments"] = args
            conversions += 1
            return f"{prefix}{json.dumps(call, ensure_ascii=False)}{suffix}"

        new_content = TOOL_CALL_RE.sub(_convert, content)
        if new_content != content:
            msg["content"] = new_content

    return conversions


def clean_fabricated_urls(messages: list):
    """Elimina URLs fabricadas de mensajes assistant."""
    cleaned = 0
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        content = msg.get("content") or ""
        new_content = FABRICATED_URL_RE.sub("", content)
        new_content = re.sub(r'\(\s*\)', '', new_content)
        new_content = re.sub(r'  +', ' ', new_content)
        new_content = re.sub(r'\n\s*\n\s*\n', '\n\n', new_content)
        new_content = new_content.strip()
        if new_content != content:
            msg["content"] = new_content
            cleaned += 1
    return cleaned


def merge_consecutive_assistants(messages: list):
    """Fusiona mensajes assistant consecutivos con \\n\\n."""
    result = []
    merged_count = 0
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg["role"] != "assistant":
            result.append(msg)
            i += 1
            continue

        chain = [msg]
        j = i + 1
        while j < len(messages) and messages[j]["role"] == "assistant":
            chain.append(messages[j])
            j += 1

        if len(chain) == 1:
            result.append(msg)
        else:
            parts = []
            for m in chain:
                c = (m.get("content") or "").strip()
                if c:
                    parts.append(c)
            merged_msg = {"role": "assistant", "content": "\n\n".join(parts)}
            result.append(merged_msg)
            merged_count += len(chain) - 1

        i = j
    return result, merged_count


def _capitalize_first(text: str) -> str:
    if text and text[0].islower():
        return text[0].upper() + text[1:]
    return text


def strip_duplicate_greetings(messages: list):
    """Elimina saludos redundantes post-presentación de Mariana."""
    introduced = False
    stripped = 0
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        content = msg.get("content") or ""

        if not introduced:
            if not INTRO_RE.search(content):
                continue
            introduced = True

            paragraphs = content.split("\n\n")
            cleaned = [paragraphs[0]]
            intro_found = INTRO_RE.search(paragraphs[0])
            for p in paragraphs[1:]:
                if intro_found:
                    cleaned.append(_capitalize_first(
                        GREETING_PREFIX_RE.sub("", p, count=1)
                    ))
                else:
                    if INTRO_RE.search(p):
                        intro_found = True
                    cleaned.append(p)
            new_content = "\n\n".join(cleaned)
        else:
            paragraphs = content.split("\n\n")
            cleaned = [_capitalize_first(GREETING_PREFIX_RE.sub("", p, count=1))
                       for p in paragraphs]
            new_content = "\n\n".join(cleaned)

        if new_content != content:
            msg["content"] = new_content
            stripped += 1
    return stripped


def fix_role_sequence(messages: list):
    """Corrige secuencias de roles inválidas."""
    fixes = 0

    # Pass 1: eliminar tool responses huérfanas (tool seguido de user)
    cleaned = []
    for i, msg in enumerate(messages):
        if (msg["role"] == "tool"
                and i + 1 < len(messages)
                and messages[i + 1]["role"] == "user"):
            fixes += 1
            continue
        cleaned.append(msg)
    messages = cleaned

    # Pass 2: recortar trailing hasta que termine en assistant con texto
    while len(messages) > 1:
        last = messages[-1]
        if last["role"] == "assistant":
            content = (last.get("content") or "").strip()
            without_tc = re.sub(
                r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL
            ).strip()
            if without_tc:
                break
            else:
                messages.pop()
                fixes += 1
        elif last["role"] in ("user", "tool"):
            messages.pop()
            fixes += 1
        else:
            break

    return messages, fixes


def process_entry(entry: dict, system_prompt: str, source: str):
    """Procesa una entrada completa del dataset."""
    messages = [dict(m) for m in entry["messages"]]
    stats = {
        "empty_removed": 0, "non_prod_removed": 0,
        "tool_calls_fixed": 0, "enganche_converted": 0,
        "urls_cleaned": 0, "assistants_merged": 0,
        "greetings_stripped": 0, "role_fixes": 0,
    }

    # 0. Normalizar comillas tipográficas
    for msg in messages:
        c = msg.get("content")
        if c:
            msg["content"] = c.translate(TYPOGRAPHIC_QUOTES)

    # 1. Reemplazar system prompt (solo v10)
    if source == "v10":
        messages[0] = {"role": "system", "content": system_prompt}

    # 2. Eliminar mensajes con contenido vacío (no aplica a system)
    filtered = []
    for msg in messages:
        if msg["role"] != "system":
            content = msg.get("content") or ""
            if content.strip() == "":
                stats["empty_removed"] += 1
                continue
        filtered.append(msg)
    messages = filtered

    # 3. Eliminar tool calls de herramientas no-producción + tool responses
    messages, non_prod = remove_non_production_tools(messages)
    stats["non_prod_removed"] = non_prod

    # 4. Normalizar nombres de argumentos en tool_calls
    for msg in messages:
        content = msg.get("content") or ""
        if "<tool_call>" in content:
            new_content, fixes = normalize_tool_calls_in_content(content)
            if new_content != content:
                msg["content"] = new_content
                stats["tool_calls_fixed"] += 1

    # 5. Convertir enganche montos absolutos → porcentajes
    stats["enganche_converted"] = convert_enganche_values(messages)

    # 6. Fusionar assistant consecutivos
    messages, merged = merge_consecutive_assistants(messages)
    stats["assistants_merged"] = merged

    # 7. Eliminar saludos duplicados post-presentación
    stats["greetings_stripped"] = strip_duplicate_greetings(messages)

    # 8. Limpiar URLs fabricadas
    stats["urls_cleaned"] = clean_fabricated_urls(messages)

    # 9. Corregir secuencia de roles
    messages, role_fixes = fix_role_sequence(messages)
    stats["role_fixes"] = role_fixes

    return {"messages": messages}, stats


def validate_output(output_path: Path = None):
    """Valida el archivo de salida."""
    if output_path is None:
        output_path = OUTPUT_TRAIN_PATH
    consec_assistant = 0
    empty_content = 0
    total = 0
    non_canonical_args = []
    role_issues = []
    non_prod_tools = []
    fabricated_urls = []

    with open(output_path) as f:
        for line_num, line in enumerate(f, 1):
            entry = json.loads(line)
            total += 1
            msgs = entry["messages"]
            prev_role = None
            for j, msg in enumerate(msgs):
                if msg["role"] == "assistant" and prev_role == "assistant":
                    consec_assistant += 1
                if msg["role"] != "system":
                    c = msg.get("content") or ""
                    if c.strip() == "":
                        empty_content += 1
                if prev_role == "tool" and msg["role"] == "user":
                    role_issues.append(f"  L{line_num} msg{j}: tool->user")

                content = msg.get("content") or ""
                if msg["role"] == "assistant" and "<tool_call>" in content:
                    for m in TOOL_CALL_RE.finditer(content):
                        try:
                            call = json.loads(m.group(2))
                            fn = call.get("name", "")
                            if fn in NON_PRODUCTION_TOOLS:
                                non_prod_tools.append(f"  L{line_num}: {fn}")
                            args = call.get("arguments", {})
                            canonical = CANONICAL_ARGS.get(fn, set())
                            if canonical:
                                for k in args:
                                    if k not in canonical:
                                        non_canonical_args.append(
                                            f"  L{line_num}: {fn}.{k}"
                                        )
                        except json.JSONDecodeError:
                            pass

                if msg["role"] == "assistant" and FABRICATED_URL_RE.search(content):
                    fabricated_urls.append(f"  L{line_num}")

                prev_role = msg["role"]

            if msgs[-1]["role"] != "assistant":
                role_issues.append(
                    f"  L{line_num}: ends with {msgs[-1]['role']}"
                )

    return {
        "total": total,
        "consec_assistant": consec_assistant,
        "empty_content": empty_content,
        "non_canonical_args": non_canonical_args,
        "role_issues": role_issues,
        "non_prod_tools": non_prod_tools,
        "fabricated_urls": fabricated_urls,
    }


def process_split(together_path: Path, v10_path: Path, output_path: Path,
                   system_prompt: str, split_name: str):
    """Procesa un split completo (train o eval) y escribe el resultado."""
    all_entries = []
    stat_keys = [
        "entries", "empty_removed", "non_prod_removed",
        "tool_calls_fixed", "enganche_converted", "urls_cleaned",
        "assistants_merged", "greetings_stripped", "role_fixes",
    ]
    totals = {
        "together": {k: 0 for k in stat_keys},
        "v10": {k: 0 for k in stat_keys},
    }

    # Procesar together
    with open(together_path) as f:
        for line in f:
            entry = json.loads(line)
            processed, stats = process_entry(entry, system_prompt, "together")
            all_entries.append(processed)
            totals["together"]["entries"] += 1
            for k in stats:
                totals["together"][k] += stats[k]

    # Procesar v10
    with open(v10_path) as f:
        for line in f:
            entry = json.loads(line)
            processed, stats = process_entry(entry, system_prompt, "v10")
            all_entries.append(processed)
            totals["v10"]["entries"] += 1
            for k in stats:
                totals["v10"][k] += stats[k]

    # Escribir salida
    with open(output_path, "w") as f:
        for entry in all_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Estadísticas
    t = totals["together"]
    v = totals["v10"]
    total_entries = t["entries"] + v["entries"]

    print("=" * 70)
    print(f"  {split_name.upper()} SPLIT — Alineado con produccion MCP")
    print("=" * 70)
    print(f"\n{'Metrica':<40} {'together':>9} {'v10':>9} {'Total':>7}")
    print("-" * 70)
    print(f"{'Entries':<40} {t['entries']:>9} {v['entries']:>9} {total_entries:>7}")
    print(f"{'Mensajes vacios eliminados':<40} {t['empty_removed']:>9} {v['empty_removed']:>9} {t['empty_removed']+v['empty_removed']:>7}")
    print(f"{'Tool calls no-produccion removidos':<40} {t['non_prod_removed']:>9} {v['non_prod_removed']:>9} {t['non_prod_removed']+v['non_prod_removed']:>7}")
    print(f"{'Tool call args normalizados':<40} {t['tool_calls_fixed']:>9} {v['tool_calls_fixed']:>9} {t['tool_calls_fixed']+v['tool_calls_fixed']:>7}")
    print(f"{'Enganche -> porcentaje convertidos':<40} {t['enganche_converted']:>9} {v['enganche_converted']:>9} {t['enganche_converted']+v['enganche_converted']:>7}")
    print(f"{'URLs fabricadas limpiadas':<40} {t['urls_cleaned']:>9} {v['urls_cleaned']:>9} {t['urls_cleaned']+v['urls_cleaned']:>7}")
    print(f"{'Assistants fusionados':<40} {t['assistants_merged']:>9} {v['assistants_merged']:>9} {t['assistants_merged']+v['assistants_merged']:>7}")
    print(f"{'Saludos duplicados limpiados':<40} {t['greetings_stripped']:>9} {v['greetings_stripped']:>9} {t['greetings_stripped']+v['greetings_stripped']:>7}")
    print(f"{'Secuencia roles corregida':<40} {t['role_fixes']:>9} {v['role_fixes']:>9} {t['role_fixes']+v['role_fixes']:>7}")

    # Validación
    print(f"\n{'=' * 70}")
    print(f"  VALIDACION — {split_name.upper()}")
    print("=" * 70)
    val = validate_output(output_path)
    print(f"  Entries en salida:              {val['total']}")
    print(f"  Assistant consecutivos:         {val['consec_assistant']}")
    print(f"  Mensajes contenido vacio:       {val['empty_content']}")
    print(f"  Tools no-produccion:            {len(val['non_prod_tools'])}")
    if val['non_prod_tools']:
        for ex in val['non_prod_tools'][:5]:
            print(f"    {ex}")
    print(f"  Args no canonicos:              {len(val['non_canonical_args'])}")
    if val['non_canonical_args']:
        for ex in val['non_canonical_args'][:10]:
            print(f"    {ex}")
    print(f"  URLs fabricadas restantes:      {len(val['fabricated_urls'])}")
    print(f"  Secuencia roles invalida:       {len(val['role_issues'])}")
    if val['role_issues']:
        for ex in val['role_issues'][:5]:
            print(f"    {ex}")

    all_ok = (
        val['consec_assistant'] == 0
        and val['empty_content'] == 0
        and len(val['non_canonical_args']) == 0
        and len(val['role_issues']) == 0
        and len(val['non_prod_tools']) == 0
        and len(val['fabricated_urls']) == 0
    )
    if all_ok:
        print(f"\n  TOTAL: {val['total']} entries limpias — listo para {split_name}")
    else:
        issues = sum(1 for x in [
            val['consec_assistant'], val['empty_content'],
            len(val['non_canonical_args']), len(val['role_issues']),
            len(val['non_prod_tools']), len(val['fabricated_urls']),
        ] if x > 0)
        print(f"\n  {issues} tipo(s) de problema por resolver:")
        if val['consec_assistant']:
            print(f"    - {val['consec_assistant']} assistant consecutivos")
        if val['empty_content']:
            print(f"    - {val['empty_content']} mensajes vacios")
        if val['non_canonical_args']:
            print(f"    - {len(val['non_canonical_args'])} args no canonicos")
        if val['role_issues']:
            print(f"    - {len(val['role_issues'])} secuencias de roles invalidas")
        if val['non_prod_tools']:
            print(f"    - {len(val['non_prod_tools'])} tools no-produccion")
        if val['fabricated_urls']:
            print(f"    - {len(val['fabricated_urls'])} URLs fabricadas")

    print(f"\n  Archivo: {output_path}")
    return val


def main():
    system_prompt = get_canonical_system_prompt()

    # ── Train split ──
    print("\n")
    train_val = process_split(
        TOGETHER_TRAIN_PATH, V10_TRAIN_PATH, OUTPUT_TRAIN_PATH,
        system_prompt, "train"
    )

    # ── Eval split ──
    print("\n\n")
    eval_val = process_split(
        TOGETHER_EVAL_PATH, V10_EVAL_PATH, OUTPUT_EVAL_PATH,
        system_prompt, "eval"
    )

    # ── Resumen final ──
    print(f"\n\n{'=' * 70}")
    print("  RESUMEN FINAL")
    print("=" * 70)
    print(f"  Train: {train_val['total']} entries → {OUTPUT_TRAIN_PATH.name}")
    print(f"  Eval:  {eval_val['total']} entries → {OUTPUT_EVAL_PATH.name}")
    print(f"  Total: {train_val['total'] + eval_val['total']} entries")


if __name__ == "__main__":
    main()
