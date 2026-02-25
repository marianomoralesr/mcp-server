#!/usr/bin/env python3
"""
===============================================================
 normalize_tool_responses.py — Normaliza respuestas de tools
 en datasets JSONL para que coincidan con el MCP canónico
===============================================================

Corrige inconsistencias entre los campos del dataset de training
y los que el MCP server realmente retorna en inference:

  1. Campos de DB crudos → campos transformados del MCP
     (autoano → año, liga_web → url, enganchemin → enganche_minimo)
  2. Unwrap wrapper {name:..., content:{...}} → JSON directo
  3. calcular_financiamiento: normalizar 49+ variantes → 1 estructura
  4. Tipos mixtos: precio número → "$NNN,NNN" string formateado
  5. vehiculo_id → id en enviar_cotizacion_email inputs
  6. Normalizar keys de respuesta por tool

Uso:
    # Dry-run (no escribe, solo reporta)
    python3 normalize_tool_responses.py --dry-run

    # Aplicar normalización
    python3 normalize_tool_responses.py

    # Solo un archivo
    python3 normalize_tool_responses.py --file merged_v10_together_train.jsonl
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

# ─── Constantes ──────────────────────────────────────────────

BASE_URL = "https://autostrefa.mx/autos/"

CANONICAL_NOTA = (
    "Estos valores son estimados. La tasa real depende del "
    "perfil crediticio del cliente y puede variar."
)

# ─── Renombrado de campos de vehículo ────────────────────────

VEHICLE_FIELD_RENAMES = {
    "autoano": "año",
    "anio": "año",
    "ano": "año",
    "liga_web": "url",
    "liga_mariana": None,          # eliminar
    "enganchemin": "enganche_minimo",
    "mensualidad_minima": "mensualidad_desde",
    "plazomax": "plazo_maximo",
    "feature_image_url": "imagen_principal",
    "slug": None,                  # eliminar (se usa para construir url)
    "url_imagen": "imagen_principal",
    "imagenes": None,              # no canónico, eliminar
    "historial": None,             # no canónico
    "seguro": None,                # no canónico
    "color": None,                 # no canónico en lista
    "sucursal": "ubicacion",       # alias
    "version": None,               # no canónico
    "equipamiento": None,          # no canónico
    "equipamiento_destacado": None,
    "num_puertas": None,           # no canónico
    "traccion": None,              # no canónico
}

# Campos canónicos de un vehiculo item (lista/búsqueda)
VEHICLE_LIST_FIELDS = {
    "id", "titulo", "marca", "modelo", "año", "precio", "precio_numerico",
    "transmision", "combustible", "carroceria", "ubicacion", "kilometraje",
    "garantia", "enganche_minimo", "mensualidad_desde", "url",
}

# Campos adicionales para detalle (obtener_vehiculo)
VEHICLE_DETAIL_FIELDS = VEHICLE_LIST_FIELDS | {
    "motor", "cilindros", "descripcion", "enganche_recomendado",
    "mensualidad_recomendada", "plazo_maximo", "con_oferta", "oferta",
    "promociones", "imagen_principal", "galeria_exterior", "galeria_interior",
}

# Campos adicionales para comparar_vehiculos
VEHICLE_COMPARE_FIELDS = VEHICLE_LIST_FIELDS | {
    "motor", "cilindros", "kilometraje_numerico",
}

# Renombrado de campos de calcular_financiamiento
CALC_RENAMES = {
    "mensualidad": "mensualidad_estimada",
    "pago_mensual": "mensualidad_estimada",
    "pago_mensual_estimado": "mensualidad_estimada",
    "mensualidad_aproximada": "mensualidad_estimada",
    "mensualidad_minima": "mensualidad_estimada",
    "monto_financiar": "monto_a_financiar",
    "monto_financiado": "monto_a_financiar",
    "saldo_financiar": "monto_a_financiar",
    "monto_total_financiado": "monto_a_financiar",
    "saldo": "monto_a_financiar",
    "enganche_monto": "enganche",
    "costo_total": "costo_financiamiento",
    "costo_total_credito": "costo_financiamiento",
    "total_pagado": "total_a_pagar",
    "pago_total": "total_a_pagar",
    "total_pagar_credito": "total_a_pagar",
    "precio_total": "total_a_pagar",
    "tasa_interes": "tasa_anual",
    "tasa_interes_anual": "tasa_anual",
    "tasa_anual_aplicada": "tasa_anual",
    "tasa_interes_aproximada": "tasa_anual",
    "tasa": "tasa_anual",
    "plazo": "plazo_meses",
    "plazo_recomendado": None,    # no canónico
    "precio": "precio_vehiculo",
    "seguro_anual": None,         # no canónico
    "vehiculo_id": None,          # input, no output
    "vehiculo": None,             # no canónico en output
    "mensaje": None,              # no canónico
}

CALC_CANONICAL_FIELDS = {
    "precio_vehiculo", "enganche_porcentaje", "enganche",
    "monto_a_financiar", "tasa_anual", "plazo_meses",
    "mensualidad_estimada", "total_a_pagar", "costo_financiamiento",
    "nota", "correcciones", "datos_reales",
}

# Campos canónicos de estadisticas_inventario
ESTADISTICAS_CANONICAL_FIELDS = {
    "total_vehiculos", "rango_precios", "rango_anos",
    "marcas_disponibles", "mensaje",
}

# ─── Funciones auxiliares ────────────────────────────────────


def format_price(value) -> str:
    """249900 → "$249,900" """
    if isinstance(value, str):
        # Ya está formateado
        if "$" in value:
            return value
        # Intentar parsear
        num = parse_price(value)
        if isinstance(num, (int, float)):
            value = num
        else:
            return value
    if isinstance(value, float):
        value = round(value)
    if not isinstance(value, int):
        return str(value)
    return f"${value:,}"


def format_km(value) -> str:
    """52405 → "52,405 km" """
    if isinstance(value, str):
        if "km" in value.lower():
            return value
        num = parse_number(value)
        if isinstance(num, (int, float)):
            value = num
        else:
            return value
    if isinstance(value, float):
        value = round(value)
    if not isinstance(value, int):
        return str(value)
    if value == 0:
        return "0 km"
    return f"{value:,} km"


def format_tasa(value) -> str:
    """15 → "15%", 0.15 → "15%" """
    if isinstance(value, str):
        if "%" in value:
            return value
        try:
            value = float(value)
        except (ValueError, TypeError):
            return value
    if isinstance(value, (int, float)):
        # Si es menor a 1, probablemente es decimal (0.15 = 15%)
        if 0 < value < 1:
            value = value * 100
        if value == int(value):
            return f"{int(value)}%"
        return f"{value}%"
    return str(value)


def parse_price(value) -> int | str:
    """'$249,900' → 249900"""
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value == int(value) else value
    if not isinstance(value, str):
        return value
    s = value.strip()
    s = s.replace("$", "").replace(" ", "").replace("\u00a0", "")
    s = re.sub(r'\s*(MXN|mxn|pesos|Pesos)\s*$', '', s)
    s = s.replace(",", "")
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return value


def parse_number(value):
    """Parsea un string numérico."""
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    s = value.strip().replace(",", "").replace(" ", "")
    s = re.sub(r'\s*km\s*$', '', s, flags=re.IGNORECASE)
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return value


def is_numeric(value) -> bool:
    """Checa si un valor es numérico o un string parseable a número."""
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        result = parse_number(value)
        return isinstance(result, (int, float))
    return False


def extract_json_from_tags(content: str, tag_name: str):
    """
    Extrae JSON de dentro de tags XML.
    Retorna (pre, obj, post) o None si no se encuentra.
    Maneja typos comunes en closing tags (tool_reponse, tool_rsponse, etc.)
    """
    # Try exact match first, then common typos
    close_variants = [
        f'</{tag_name}>',
        '</tool_reponse>',    # missing 's'
        '</tool_rsponse>',    # missing 'e'
        '</tool_responce>',   # c instead of s
        '</toolresponse>',    # missing underscore
    ]

    m = None
    used_close = None
    for close_tag in close_variants:
        pattern = rf'<{tag_name}>\s*(.*?)\s*{re.escape(close_tag)}'
        m = re.search(pattern, content, re.DOTALL)
        if m:
            used_close = close_tag
            break

    if not m:
        # Fallback: opening tag exists but no closing tag
        open_tag = f'<{tag_name}>'
        open_pos = content.find(open_tag)
        if open_pos >= 0:
            after_open = content[open_pos + len(open_tag):].strip()
            if after_open.startswith("{") or after_open.startswith("["):
                # Try balanced brace extraction
                json_str = after_open
                bracket = '{' if json_str.startswith("{") else '['
                close_bracket = '}' if bracket == '{' else ']'
                depth = 0
                end = -1
                for i, c in enumerate(json_str):
                    if c == bracket:
                        depth += 1
                    elif c == close_bracket:
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > 0:
                    try:
                        obj = json.loads(json_str[:end])
                        pre = content[:open_pos]
                        post = content[open_pos + len(open_tag) + len(after_open):]
                        return (pre, obj, post)
                    except json.JSONDecodeError:
                        pass
        return None

    json_str = m.group(1).strip()
    pre = content[:m.start()]
    post = content[m.end():]

    # Parsear con balanced braces
    if not json_str.startswith("{") and not json_str.startswith("["):
        return None

    try:
        obj = json.loads(json_str)
        return (pre, obj, post)
    except json.JSONDecodeError:
        # Intentar con balanced braces
        if json_str.startswith("{"):
            depth = 0
            end = -1
            for i, c in enumerate(json_str):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                try:
                    obj = json.loads(json_str[:end])
                    return (pre, obj, post)
                except json.JSONDecodeError:
                    pass
    return None


def unwrap_content_name(obj, tool_name: str = None):
    """Si obj tiene {name:..., content:{...}}, retorna content.
    También maneja content como array para tools que retornan listas."""
    if not isinstance(obj, dict):
        return obj
    if "name" not in obj or "content" not in obj:
        return obj
    if len(obj) > 3:  # name, content, maybe role
        return obj

    content = obj["content"]

    # content es dict → retornar directo
    if isinstance(content, dict):
        return content

    # content es array → envolver en el wrapper key apropiado
    if isinstance(content, list):
        if tool_name == "buscar_vehiculos":
            return {"vehiculos": content, "total": len(content)}
        elif tool_name == "buscar_alternativas":
            return {"alternativas": content, "total": len(content)}
        elif tool_name == "comparar_vehiculos":
            return {"vehiculos": content}
        elif tool_name == "buscar_informacion":
            return {"resultados": content}
        elif tool_name == "obtener_info_negocio":
            return {"informacion": content}
        elif tool_name == "obtener_faqs":
            return {"faqs": content}
        else:
            # Genérico: devolver como dict con key "items"
            return {"items": content}

    # content es string
    if isinstance(content, str):
        if tool_name == "obtener_info_negocio":
            return {"informacion": [{"id": "1", "titulo": "", "contenido": content}]}
        elif tool_name == "buscar_informacion":
            return {"resultados": [{"id": "1", "categoria": "general", "titulo": "Información", "contenido": content}]}
        # No se puede unwrap, devolver original
        return obj

    return obj


def build_url_from_slug(slug: str) -> str:
    """Construye URL canónica desde slug."""
    if slug and isinstance(slug, str):
        return BASE_URL + slug
    return None


# ─── Normalización de items de vehículo ──────────────────────


def normalize_vehicle_item(item: dict, mode: str = "list", stats: Counter = None) -> dict:
    """
    Normaliza un item de vehículo aplicando renombrados y formateo.
    mode: "list" | "detail" | "compare"
    """
    if not isinstance(item, dict):
        return item

    result = {}

    # Guardar slug y liga_web antes de eliminarlos (para construir url)
    slug = item.get("slug")
    liga_web = item.get("liga_web")

    # Paso 1: Renombrar campos
    for key, value in item.items():
        if key in VEHICLE_FIELD_RENAMES:
            new_key = VEHICLE_FIELD_RENAMES[key]
            if new_key is None:
                if stats:
                    stats["fields_removed"] += 1
                continue
            if new_key not in result:  # no sobreescribir
                result[new_key] = value
                if stats:
                    stats["fields_renamed"] += 1
            continue
        result[key] = value

    # Paso 2: Construir url si falta
    if not result.get("url"):
        if liga_web and isinstance(liga_web, str) and liga_web.startswith("http"):
            result["url"] = liga_web
        elif slug:
            result["url"] = build_url_from_slug(slug)
        else:
            result["url"] = None

    # Paso 3: Formatear campos numéricos
    # precio
    if "precio" in result:
        raw_precio = result["precio"]
        if isinstance(raw_precio, (int, float)):
            # Guardar precio_numerico antes de formatear
            if "precio_numerico" not in result:
                result["precio_numerico"] = int(raw_precio) if isinstance(raw_precio, float) and raw_precio == int(raw_precio) else raw_precio
            result["precio"] = format_price(raw_precio)
            if stats:
                stats["types_formatted"] += 1
        elif isinstance(raw_precio, str) and "$" not in raw_precio:
            num = parse_price(raw_precio)
            if isinstance(num, (int, float)):
                if "precio_numerico" not in result:
                    result["precio_numerico"] = int(num) if isinstance(num, float) and num == int(num) else num
                result["precio"] = format_price(num)
                if stats:
                    stats["types_formatted"] += 1
        # Si ya está formateado ($...), extraer precio_numerico si falta
        if "precio_numerico" not in result:
            parsed = parse_price(result.get("precio", ""))
            if isinstance(parsed, (int, float)):
                result["precio_numerico"] = int(parsed) if isinstance(parsed, float) and parsed == int(parsed) else parsed

    # kilometraje
    if "kilometraje" in result:
        raw_km = result["kilometraje"]
        if isinstance(raw_km, (int, float)):
            # Para comparar_vehiculos, guardar kilometraje_numerico
            if mode == "compare" and "kilometraje_numerico" not in result:
                result["kilometraje_numerico"] = int(raw_km)
            result["kilometraje"] = format_km(raw_km)
            if stats:
                stats["types_formatted"] += 1

    # enganche_minimo
    if "enganche_minimo" in result and isinstance(result["enganche_minimo"], (int, float)):
        result["enganche_minimo"] = format_price(result["enganche_minimo"])
        if stats:
            stats["types_formatted"] += 1

    # mensualidad_desde
    if "mensualidad_desde" in result and isinstance(result["mensualidad_desde"], (int, float)):
        result["mensualidad_desde"] = format_price(result["mensualidad_desde"])
        if stats:
            stats["types_formatted"] += 1

    # enganche_recomendado
    if "enganche_recomendado" in result and isinstance(result["enganche_recomendado"], (int, float)):
        result["enganche_recomendado"] = format_price(result["enganche_recomendado"])
        if stats:
            stats["types_formatted"] += 1

    # mensualidad_recomendada
    if "mensualidad_recomendada" in result and isinstance(result["mensualidad_recomendada"], (int, float)):
        result["mensualidad_recomendada"] = format_price(result["mensualidad_recomendada"])
        if stats:
            stats["types_formatted"] += 1

    # oferta
    if "oferta" in result and isinstance(result["oferta"], (int, float)):
        result["oferta"] = format_price(result["oferta"])
        if stats:
            stats["types_formatted"] += 1

    # Paso 4: Filtrar a campos canónicos según modo
    if mode == "list":
        allowed = VEHICLE_LIST_FIELDS
    elif mode == "detail":
        allowed = VEHICLE_DETAIL_FIELDS
    elif mode == "compare":
        allowed = VEHICLE_COMPARE_FIELDS
    else:
        allowed = VEHICLE_LIST_FIELDS

    filtered = {}
    for key in result:
        if key in allowed:
            filtered[key] = result[key]
        elif stats:
            stats["fields_removed"] += 1

    return filtered


# ─── Normalización por tool ──────────────────────────────────


def normalize_buscar_vehiculos(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de buscar_vehiculos."""
    if "error" in obj:
        return obj

    # Extraer lista de vehículos
    vehiculos = None
    for key in ("vehiculos", "resultados", "autos"):
        if key in obj and isinstance(obj[key], list):
            vehiculos = obj[key]
            if key != "vehiculos" and stats:
                stats["wrapper_key_renamed"] += 1
            break

    if vehiculos is None:
        return obj

    # Normalizar cada item
    normalized = [normalize_vehicle_item(v, mode="list", stats=stats) for v in vehiculos]

    result = {"vehiculos": normalized}

    # total
    if "total" in obj:
        result["total"] = obj["total"]
    else:
        result["total"] = len(normalized)

    # correcciones
    if "correcciones" in obj:
        result["correcciones"] = obj["correcciones"]

    # mensaje (solo si vehiculos vacío)
    if "mensaje" in obj:
        result["mensaje"] = obj["mensaje"]

    return result


def normalize_obtener_vehiculo(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de obtener_vehiculo."""
    if "error" in obj:
        return obj
    if "mensaje" in obj and "id" not in obj:
        return obj

    return normalize_vehicle_item(obj, mode="detail", stats=stats)


def normalize_calcular_financiamiento(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de calcular_financiamiento."""
    if "error" in obj:
        return obj

    result = {}

    # Paso 1: Renombrar campos
    for key, value in obj.items():
        if key in CALC_RENAMES:
            new_key = CALC_RENAMES[key]
            if new_key is None:
                if stats:
                    stats["fields_removed"] += 1
                continue
            if new_key not in result:
                result[new_key] = value
                if stats:
                    stats["fields_renamed"] += 1
            continue
        result[key] = value

    # Paso 2: Formatear campos de precio como strings
    for field in ("precio_vehiculo", "enganche", "monto_a_financiar",
                  "mensualidad_estimada", "total_a_pagar", "costo_financiamiento"):
        if field in result:
            val = result[field]
            if isinstance(val, (int, float)):
                if field == "mensualidad_estimada":
                    val = round(val)
                elif field == "total_a_pagar":
                    val = round(val)
                elif field == "costo_financiamiento":
                    val = round(val)
                result[field] = format_price(val)
                if stats:
                    stats["types_formatted"] += 1

    # tasa_anual: formatear
    if "tasa_anual" in result:
        result["tasa_anual"] = format_tasa(result["tasa_anual"])

    # enganche_porcentaje: asegurar que es número
    if "enganche_porcentaje" in result:
        val = result["enganche_porcentaje"]
        if isinstance(val, str):
            num = parse_number(val)
            if isinstance(num, (int, float)):
                result["enganche_porcentaje"] = num
                if stats:
                    stats["types_formatted"] += 1

    # plazo_meses: asegurar que es número
    if "plazo_meses" in result:
        val = result["plazo_meses"]
        if isinstance(val, str):
            num = parse_number(val)
            if isinstance(num, (int, float)):
                result["plazo_meses"] = int(num)
                if stats:
                    stats["types_formatted"] += 1

    # nota: agregar si falta
    if "nota" not in result:
        result["nota"] = CANONICAL_NOTA
        if stats:
            stats["nota_added"] += 1

    # Paso 3: Filtrar a campos canónicos
    filtered = {}
    for key in result:
        if key in CALC_CANONICAL_FIELDS:
            filtered[key] = result[key]
        elif stats:
            stats["fields_removed"] += 1

    return filtered


def normalize_buscar_alternativas(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de buscar_alternativas."""
    if "error" in obj:
        return obj

    # Extraer lista
    alternativas = None
    for key in ("alternativas", "vehiculos", "resultados", "autos"):
        if key in obj and isinstance(obj[key], list):
            alternativas = obj[key]
            if key != "alternativas" and stats:
                stats["wrapper_key_renamed"] += 1
            break

    if alternativas is None:
        return obj

    normalized = [normalize_vehicle_item(v, mode="list", stats=stats) for v in alternativas]

    result = {"alternativas": normalized}

    if "total" in obj:
        result["total"] = obj["total"]
    else:
        result["total"] = len(normalized)

    if "correcciones" in obj:
        result["correcciones"] = obj["correcciones"]

    if "mensaje" in obj:
        result["mensaje"] = obj["mensaje"]

    return result


def normalize_comparar_vehiculos(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de comparar_vehiculos."""
    if "error" in obj:
        return obj
    if "mensaje" in obj and "vehiculos" not in obj and "comparacion" not in obj:
        # Check for vehiculo_N keys before giving up
        has_vehiculo_keys = any(
            re.match(r'vehiculo_\d+', k) for k in obj if k != "mensaje"
        )
        if not has_vehiculo_keys:
            return obj

    # Extraer lista
    vehiculos = None
    for key in ("vehiculos", "comparacion", "resultados"):
        if key in obj:
            val = obj[key]
            if isinstance(val, list):
                vehiculos = val
                if key != "vehiculos" and stats:
                    stats["wrapper_key_renamed"] += 1
                break
            elif isinstance(val, dict):
                # comparacion como dict: {titulo: {datos}, ...}
                vehiculos = []
                for titulo, datos in val.items():
                    if isinstance(datos, dict):
                        if "titulo" not in datos:
                            datos["titulo"] = titulo
                        vehiculos.append(datos)
                if vehiculos and stats:
                    stats["wrapper_key_renamed"] += 1
                break

    # Si no hay lista, buscar vehiculo_1, vehiculo_2, vehiculo1, vehiculo2, etc.
    if vehiculos is None:
        numbered = []
        for key in sorted(obj.keys()):
            if re.match(r'vehiculo_?\d+', key) and isinstance(obj[key], dict):
                numbered.append(obj[key])
        if numbered:
            vehiculos = numbered
            if stats:
                stats["wrapper_key_renamed"] += 1

    if vehiculos is None:
        return obj

    normalized = [normalize_vehicle_item(v, mode="compare", stats=stats) for v in vehiculos]

    return {"vehiculos": normalized}


def normalize_estadisticas_inventario(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de estadisticas_inventario."""
    if "error" in obj:
        return obj

    # Si tiene una lista de vehiculos, es una respuesta incorrecta
    # (el tool real no devuelve vehiculos), pero intentamos extraer estadísticas
    if "vehiculos" in obj and isinstance(obj["vehiculos"], list):
        vehiculos = obj["vehiculos"]
        if not vehiculos:
            return {
                "total_vehiculos": 0,
                "mensaje": "Nuestro inventario se está actualizando en este momento. "
                           "Por favor intenta de nuevo en unos minutos."
            }

        # Extraer estadísticas de la lista
        precios = []
        anos = []
        marcas = Counter()
        for v in vehiculos:
            p = v.get("precio")
            if isinstance(p, (int, float)):
                precios.append(p)
            elif isinstance(p, str):
                num = parse_price(p)
                if isinstance(num, (int, float)):
                    precios.append(num)

            a = v.get("autoano") or v.get("año") or v.get("anio")
            if isinstance(a, (int, float)):
                anos.append(int(a))

            m = v.get("marca")
            if m:
                marcas[m] += 1

        result = {"total_vehiculos": obj.get("total", len(vehiculos))}
        if precios:
            result["rango_precios"] = {
                "minimo": format_price(min(precios)),
                "maximo": format_price(max(precios)),
                "promedio": format_price(round(sum(precios) / len(precios))),
            }
        if anos:
            result["rango_anos"] = {
                "desde": min(anos),
                "hasta": max(anos),
            }
        if marcas:
            result["marcas_disponibles"] = [
                {"marca": m, "cantidad": c}
                for m, c in marcas.most_common()
            ]
        if stats:
            stats["estadisticas_rebuilt"] += 1
        return result

    # Caso: ya tiene estructura parcial canónica
    result = {}

    # total_vehiculos
    for key in ("total_vehiculos", "total", "total_autos"):
        if key in obj:
            result["total_vehiculos"] = obj[key]
            break

    if "total_vehiculos" not in result:
        result["total_vehiculos"] = 0

    # rango_precios
    if "rango_precios" in obj and isinstance(obj["rango_precios"], dict):
        rp = obj["rango_precios"]
        result["rango_precios"] = {
            "minimo": format_price(rp.get("minimo", rp.get("min", 0))),
            "maximo": format_price(rp.get("maximo", rp.get("max", 0))),
            "promedio": format_price(rp.get("promedio", rp.get("avg", 0))),
        }
    elif "precio_min" in obj or "precio_max" in obj:
        result["rango_precios"] = {
            "minimo": format_price(obj.get("precio_min", 0)),
            "maximo": format_price(obj.get("precio_max", 0)),
            "promedio": format_price(obj.get("precio_promedio", 0)),
        }
        if stats:
            stats["fields_renamed"] += 1

    # rango_anos
    if "rango_anos" in obj and isinstance(obj["rango_anos"], dict):
        result["rango_anos"] = obj["rango_anos"]
    elif "rango_anos" not in obj:
        # Intentar construir de otros campos
        pass

    # marcas_disponibles
    if "marcas_disponibles" in obj:
        md = obj["marcas_disponibles"]
        if isinstance(md, list):
            # Normalizar items: pueden ser strings o dicts
            normalized = []
            for item in md:
                if isinstance(item, dict):
                    normalized.append({
                        "marca": item.get("marca", item.get("nombre", "")),
                        "cantidad": item.get("cantidad", item.get("count", 0)),
                    })
                elif isinstance(item, str):
                    normalized.append({"marca": item, "cantidad": 0})
            result["marcas_disponibles"] = normalized

    # mensaje
    if "mensaje" in obj:
        result["mensaje"] = obj["mensaje"]

    # Si no pudimos construir algo útil, devolver con estructura mínima
    if "rango_precios" not in result and "marcas_disponibles" not in result and "mensaje" not in result:
        # Respuesta no reconocida, pasar campos canónicos que existan
        for key in ESTADISTICAS_CANONICAL_FIELDS:
            if key in obj:
                result[key] = obj[key]

    return result


def normalize_buscar_informacion(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de buscar_informacion."""
    if "error" in obj:
        return obj

    # Caso: respuesta directa como string
    if "respuesta" in obj and isinstance(obj["respuesta"], str):
        result = {
            "resultados": [{
                "id": "1",
                "categoria": "general",
                "titulo": "Información",
                "contenido": obj["respuesta"],
            }]
        }
        if stats:
            stats["info_restructured"] += 1
        return result

    # Caso: ya tiene resultados
    resultados = None
    for key in ("resultados", "informacion"):
        if key in obj and isinstance(obj[key], list):
            resultados = obj[key]
            if key != "resultados" and stats:
                stats["wrapper_key_renamed"] += 1
            break

    if resultados is not None:
        # Normalizar items
        normalized = []
        for item in resultados:
            if isinstance(item, dict):
                n = {
                    "id": str(item.get("id", "")),
                    "categoria": item.get("categoria", item.get("category", "general")),
                    "titulo": item.get("titulo", item.get("title", "")),
                    "contenido": item.get("contenido", item.get("content", "")),
                }
                if n["titulo"] or n["contenido"]:
                    normalized.append(n)
                    if "title" in item or "content" in item or "category" in item:
                        if stats:
                            stats["fields_renamed"] += 1
            elif isinstance(item, str):
                normalized.append({
                    "id": str(len(normalized) + 1),
                    "categoria": "general",
                    "titulo": "Información",
                    "contenido": item,
                })

        result = {"resultados": normalized}
        if "mensaje" in obj:
            result["mensaje"] = obj["mensaje"]
        return result

    # Caso: campos sueltos tipo título + contenido
    if "titulo" in obj and "contenido" in obj:
        return {
            "resultados": [{
                "id": "1",
                "categoria": obj.get("categoria", "general"),
                "titulo": obj["titulo"],
                "contenido": obj["contenido"],
            }]
        }

    return obj


def normalize_obtener_info_negocio(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de obtener_info_negocio."""
    if "error" in obj:
        return obj

    # Si tiene 'informacion' como lista, ya es casi canónico
    if "informacion" in obj and isinstance(obj["informacion"], list):
        items = obj["informacion"]
        normalized = []
        for item in items:
            if isinstance(item, dict):
                normalized.append({
                    "id": str(item.get("id", len(normalized) + 1)),
                    "titulo": item.get("titulo", item.get("title", "")),
                    "contenido": item.get("contenido", item.get("content", "")),
                })
            elif isinstance(item, str):
                normalized.append({
                    "id": str(len(normalized) + 1),
                    "titulo": "Información",
                    "contenido": item,
                })
        result = {"informacion": normalized}
        if "mensaje" in obj:
            result["mensaje"] = obj["mensaje"]
        return result

    # Si tiene 'informacion' como string
    if "informacion" in obj and isinstance(obj["informacion"], str):
        tema = obj.get("tema", "general")
        return {
            "informacion": [{
                "id": "1",
                "titulo": tema,
                "contenido": obj["informacion"],
            }]
        }
        if stats:
            stats["info_restructured"] += 1

    # Si tiene 'resultados' como lista (alias)
    if "resultados" in obj and isinstance(obj["resultados"], list):
        items = obj["resultados"]
        normalized = []
        for item in items:
            if isinstance(item, dict):
                normalized.append({
                    "id": str(item.get("id", len(normalized) + 1)),
                    "titulo": item.get("titulo", item.get("title", "")),
                    "contenido": item.get("contenido", item.get("content", "")),
                })
        result = {"informacion": normalized}
        if stats:
            stats["wrapper_key_renamed"] += 1
        if "mensaje" in obj:
            result["mensaje"] = obj["mensaje"]
        return result

    # Caso complejo: la respuesta es un dict con datos heterogéneos del negocio
    # Intentar convertir a formato canónico
    tema = obj.get("tema", "")

    # Filtrar solo campos que parecen contenido (no metadata)
    content_parts = []
    skip_keys = {"tema", "mensaje", "error", "status", "message", "exito", "ok"}
    for key, value in obj.items():
        if key in skip_keys:
            continue
        if isinstance(value, str) and value.strip():
            content_parts.append(f"**{key}**: {value}")
        elif isinstance(value, list):
            if all(isinstance(x, str) for x in value):
                content_parts.append(f"**{key}**: " + ", ".join(value))
            elif all(isinstance(x, dict) for x in value):
                for x in value:
                    parts = [f"{k}: {v}" for k, v in x.items() if isinstance(v, (str, int, float))]
                    if parts:
                        content_parts.append(" | ".join(parts))
        elif isinstance(value, dict):
            parts = [f"{k}: {v}" for k, v in value.items() if isinstance(v, (str, int, float))]
            if parts:
                content_parts.append(f"**{key}**: " + ", ".join(parts))
        elif isinstance(value, (int, float, bool)):
            content_parts.append(f"**{key}**: {value}")

    if content_parts:
        result = {
            "informacion": [{
                "id": "1",
                "titulo": tema if tema else "Información del negocio",
                "contenido": "\n".join(content_parts),
            }]
        }
        if "mensaje" in obj:
            result["mensaje"] = obj["mensaje"]
        if stats:
            stats["info_restructured"] += 1
        return result

    # Si solo tiene mensaje, devolver estructura vacía
    if "mensaje" in obj:
        return {"informacion": [], "mensaje": obj["mensaje"]}

    return obj


def normalize_obtener_faqs(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de obtener_faqs."""
    if "error" in obj:
        return obj

    if "faqs" in obj and isinstance(obj["faqs"], list):
        normalized = []
        for item in obj["faqs"]:
            if isinstance(item, dict):
                normalized.append({
                    "id": str(item.get("id", len(normalized) + 1)),
                    "pregunta": item.get("pregunta", item.get("title", item.get("titulo", ""))),
                    "respuesta": item.get("respuesta", item.get("content", item.get("contenido", ""))),
                })
        result = {"faqs": normalized}
        if "mensaje" in obj:
            result["mensaje"] = obj["mensaje"]
        return result

    return obj


def normalize_solicitar_datos_contacto(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de solicitar_datos_contacto."""
    if "error" in obj:
        return obj

    result = {}

    # mensaje
    result["mensaje"] = obj.get("mensaje", obj.get("message", ""))

    # datos_registrados o lead_existente
    if obj.get("datos_registrados"):
        result["datos_registrados"] = True
    elif obj.get("lead_existente"):
        result["lead_existente"] = True
    elif obj.get("ok") or obj.get("confirmacion") or obj.get("status") == "ok":
        # Inferir datos_registrados de campos genéricos de éxito
        result["datos_registrados"] = True

    if not result["mensaje"]:
        # Si no hay mensaje pero hay datos, generar uno genérico
        result["mensaje"] = "Datos de contacto registrados correctamente."

    return result


def normalize_enviar_cotizacion_email(obj: dict, stats: Counter) -> dict:
    """Normaliza respuesta de enviar_cotizacion_email."""
    if "error" in obj:
        return obj

    result = {
        "mensaje": obj.get("mensaje", obj.get("message", "")),
        "enviado": obj.get("enviado", obj.get("sent", obj.get("ok", False))),
    }

    if isinstance(result["enviado"], str):
        result["enviado"] = result["enviado"].lower() in ("true", "1", "yes", "si", "sí")

    if not result["mensaje"]:
        if result["enviado"]:
            result["mensaje"] = "Cotización enviada exitosamente."
        else:
            result["mensaje"] = "No se pudo enviar la cotización."

    return result


# ─── Dispatcher principal ────────────────────────────────────

NORMALIZERS = {
    "buscar_vehiculos": normalize_buscar_vehiculos,
    "obtener_vehiculo": normalize_obtener_vehiculo,
    "calcular_financiamiento": normalize_calcular_financiamiento,
    "buscar_alternativas": normalize_buscar_alternativas,
    "comparar_vehiculos": normalize_comparar_vehiculos,
    "estadisticas_inventario": normalize_estadisticas_inventario,
    "buscar_informacion": normalize_buscar_informacion,
    "obtener_info_negocio": normalize_obtener_info_negocio,
    "obtener_faqs": normalize_obtener_faqs,
    "solicitar_datos_contacto": normalize_solicitar_datos_contacto,
    "enviar_cotizacion_email": normalize_enviar_cotizacion_email,
}


def normalize_tool_response(tool_name: str, obj: dict, stats: Counter) -> dict:
    """Normaliza una respuesta de tool según su nombre."""
    normalizer = NORMALIZERS.get(tool_name)
    if normalizer:
        return normalizer(obj, stats)
    return obj


def normalize_tool_call_args(tool_name: str, args: dict, stats: Counter) -> dict:
    """Normaliza argumentos de tool call del assistant."""
    if tool_name == "enviar_cotizacion_email":
        if "vehiculo_id" in args:
            args["id"] = args.pop("vehiculo_id")
            if stats:
                stats["input_args_renamed"] += 1
    return args


# ─── Extracción de tool_call del assistant message ───────────


def extract_tool_call_spans(content: str):
    """
    Extrae spans de <tool_call>JSON</tool_call> usando balanced braces.
    Retorna lista de (start, end, json_string).
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


def get_tool_name_from_assistant(msg_content: str) -> str | None:
    """Extrae el nombre del tool de un mensaje assistant con <tool_call>."""
    m = re.search(r'"name"\s*:\s*"([^"]+)"', msg_content)
    if m:
        return m.group(1)
    return None


# ─── Procesamiento principal ─────────────────────────────────


def process_file(fpath: str, dry_run: bool = False) -> dict:
    """Procesa un archivo JSONL, normaliza tool responses, escribe resultado."""
    stats = Counter()
    lines_in = 0
    fixed_lines = []

    with open(fpath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
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

            for i, msg in enumerate(messages):
                role = msg.get("role", "")
                content = msg.get("content", "")

                # ── Normalizar tool responses ──
                if role == "tool" and isinstance(content, str) and "<tool_response>" in content:
                    parsed = extract_json_from_tags(content, "tool_response")
                    if parsed is None:
                        continue

                    pre, response_obj, post = parsed

                    # Encontrar el tool name del assistant anterior
                    tool_name = None
                    for k in range(i - 1, -1, -1):
                        if messages[k].get("role") == "assistant":
                            tool_name = get_tool_name_from_assistant(
                                messages[k].get("content", "")
                            )
                            break

                    if not tool_name:
                        continue

                    stats[f"tool_{tool_name}_seen"] += 1

                    # Unwrap {name:..., content:{...}}
                    original_obj = response_obj
                    response_obj = unwrap_content_name(response_obj, tool_name)
                    if response_obj is not original_obj:
                        stats["wrappers_unwrapped"] += 1

                    # Normalizar
                    normalized = normalize_tool_response(tool_name, response_obj, stats)

                    if normalized != original_obj:
                        stats[f"tool_{tool_name}_normalized"] += 1
                        # Re-serializar
                        json_str = json.dumps(normalized, ensure_ascii=False)
                        new_content = f"{pre}<tool_response>\n{json_str}\n</tool_response>{post}"
                        msg["content"] = new_content
                        modified = True

                # ── Normalizar tool call arguments ──
                elif role == "assistant" and isinstance(content, str) and "<tool_call>" in content:
                    spans = extract_tool_call_spans(content)
                    if not spans:
                        continue

                    new_content = content
                    for start, end, tc_json in reversed(spans):
                        try:
                            tc_obj = json.loads(tc_json)
                        except json.JSONDecodeError:
                            continue

                        tc_name = tc_obj.get("name", "")
                        tc_args = tc_obj.get("arguments", {})
                        if isinstance(tc_args, str):
                            try:
                                tc_args = json.loads(tc_args)
                            except json.JSONDecodeError:
                                continue

                        old_args = dict(tc_args)
                        new_args = normalize_tool_call_args(tc_name, tc_args, stats)

                        if new_args != old_args:
                            tc_obj["arguments"] = new_args
                            fixed_json = json.dumps(
                                tc_obj, ensure_ascii=False, separators=(",", ": ")
                            )
                            replacement = f"<tool_call>\n{fixed_json}\n</tool_call>"
                            new_content = new_content[:start] + replacement + new_content[end:]
                            modified = True

                    if new_content != content:
                        msg["content"] = new_content

            if modified:
                stats["conversations_modified"] += 1

            fixed_lines.append(json.dumps(obj, ensure_ascii=False))

    stats["lines_in"] = lines_in
    stats["lines_out"] = len(fixed_lines)

    # Escribir resultado
    if not dry_run and stats["conversations_modified"] > 0:
        backup_path = fpath + ".pre_normalize"
        if not os.path.exists(backup_path):
            shutil.copy2(fpath, backup_path)

        with open(fpath, "w", encoding="utf-8") as f:
            for line in fixed_lines:
                f.write(line + "\n")

    return dict(stats)


# ─── Main ─────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Normaliza tool responses en datasets JSONL para coincidir con MCP canónico"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo reportar, no escribir cambios")
    parser.add_argument("--file", type=str, default=None,
                        help="Procesar solo este archivo")
    parser.add_argument("--dir", type=str,
                        default=os.path.join(os.path.dirname(__file__)),
                        help="Directorio base de datasets")
    args = parser.parse_args()

    BASE = os.path.abspath(args.dir)

    # Archivos principales
    main_files = [
        "merged_v10_together_train.jsonl",
        "merged_v10_together_eval.jsonl",
    ]

    if args.file:
        target = os.path.join(BASE, args.file) if not os.path.isabs(args.file) else args.file
        if not os.path.exists(target):
            print(f"ERROR: Archivo no encontrado: {target}")
            sys.exit(1)
        files_to_process = [target]
    else:
        files_to_process = []
        for fname in main_files:
            fpath = os.path.join(BASE, fname)
            if os.path.exists(fpath):
                files_to_process.append(fpath)

    if not files_to_process:
        print("No se encontraron archivos JSONL para procesar")
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "APLICANDO NORMALIZACIÓN"
    print("=" * 80)
    print(f"  normalize_tool_responses.py — {mode}")
    print(f"  Archivos: {len(files_to_process)}")
    print("=" * 80)

    global_stats = Counter()

    for fpath in files_to_process:
        rel = os.path.relpath(fpath, BASE)
        print(f"\n  Procesando: {rel}")
        file_stats = process_file(fpath, dry_run=args.dry_run)

        for k, v in file_stats.items():
            global_stats[k] += v

        print(f"    Líneas: {file_stats.get('lines_in', 0)}")
        print(f"    Conversaciones modificadas: {file_stats.get('conversations_modified', 0)}")

    # ── Resumen global ──
    print(f"\n{'=' * 80}")
    print("  RESUMEN GLOBAL")
    print(f"{'=' * 80}")
    print(f"  Total conversaciones:             {global_stats['lines_in']}")
    print(f"  Conversaciones modificadas:       {global_stats['conversations_modified']}")
    print(f"  Wrappers {{name,content}} unwrapped: {global_stats['wrappers_unwrapped']}")
    print(f"  Campos renombrados:               {global_stats['fields_renamed']}")
    print(f"  Campos eliminados:                {global_stats['fields_removed']}")
    print(f"  Tipos formateados (num→str):      {global_stats['types_formatted']}")
    print(f"  Wrapper keys renombrados:         {global_stats['wrapper_key_renamed']}")
    print(f"  Notas agregadas (calc_fin):       {global_stats['nota_added']}")
    print(f"  Info reestructurada:              {global_stats['info_restructured']}")
    print(f"  Estadísticas reconstruidas:       {global_stats['estadisticas_rebuilt']}")
    print(f"  Input args renombrados:           {global_stats['input_args_renamed']}")
    print(f"  Errores JSON línea:               {global_stats['json_line_errors']}")

    # Per-tool breakdown
    print(f"\n  {'─' * 60}")
    print("  Por tool:")
    tool_names = sorted(set(
        k.replace("tool_", "").replace("_seen", "").replace("_normalized", "")
        for k in global_stats
        if k.startswith("tool_")
    ))
    for tn in tool_names:
        seen = global_stats.get(f"tool_{tn}_seen", 0)
        norm = global_stats.get(f"tool_{tn}_normalized", 0)
        if seen > 0:
            pct = (norm / seen * 100) if seen else 0
            print(f"    {tn:35s}  {norm:5d}/{seen:5d} normalizadas ({pct:.1f}%)")

    if args.dry_run:
        print(f"\n  MODO DRY-RUN: no se escribieron cambios")
        print(f"  Ejecuta sin --dry-run para aplicar las normalizaciones")
    else:
        print(f"\n  Backups guardados como *.pre_normalize")
        print(f"  Normalizaciones aplicadas exitosamente")
        print(f"  Verificar: líneas in={global_stats['lines_in']}, out={global_stats['lines_out']}")


if __name__ == "__main__":
    main()
