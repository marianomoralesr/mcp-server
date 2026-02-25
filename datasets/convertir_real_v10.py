#!/usr/bin/env python3
"""
Conversión de conversaciones reales de Mariana (ChatML OpenAI)
al formato Qwen 3 con tool calling para fine-tuning v10.

Pipeline: source_conversations_mariana_chatml.json → v10_real_{train,eval}.jsonl
"""

import json, re, random, sys
from pathlib import Path
from collections import Counter
from typing import Optional, List, Dict, Any, Tuple

# ─── CONSTANTES ────────────────────────────────────────────────────────

BRANDS_SET = {
    "toyota", "honda", "nissan", "mazda", "kia", "hyundai",
    "chevrolet", "chevy", "ford", "volkswagen", "vw",
    "bmw", "mercedes", "audi", "seat", "renault", "peugeot",
    "suzuki", "mitsubishi", "jeep", "dodge", "ram",
    "volvo", "mini", "fiat", "porsche", "jaguar",
    "buick", "gmc", "cadillac", "lincoln", "acura", "infiniti",
    "lexus", "genesis", "subaru", "chrysler", "mg", "baic",
    "jac", "changan", "haval", "omoda", "chery", "jetour",
    "cupra", "smart", "land rover", "mercedes-benz", "mercedes benz",
}

BRAND_NORMALIZE = {
    "chevy": "Chevrolet", "vw": "Volkswagen",
    "mercedes benz": "Mercedes-Benz", "mercedes": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz", "land rover": "Land Rover",
}

BODY_TYPE_MAP = {
    "suv": "SUV", "sub": "SUV", "camioneta": "SUV", "familiar": "SUV",
    "sedan": "Sedán", "sedán": "Sedán",
    "hatchback": "Hatchback", "hatch": "Hatchback",
    "pickup": "Pick Up", "pick up": "Pick Up", "pik up": "Pick Up",
    "van": "Van", "minivan": "Van",
    "redilas": "Pick Up", "cerrada": "Pick Up",
    "coupe": "Coupé", "coupé": "Coupé",
}

# ─── REGEX PATTERNS ────────────────────────────────────────────────────

YEAR_RE = re.compile(r'\b(20[12]\d)\b')
PRECIO_RE = re.compile(r'Precio:\s*\$?([\d,]+)', re.I)
KM_RE = re.compile(r'[Kk]ilo?metraje:\s*([\d,]+)\s*km', re.I)
UBICACION_RE = re.compile(r'Ubicaci[óo]n:\s*([^\n\-•]+)', re.I)
MOTOR_RE = re.compile(r'Motor:\s*([\d.]+\s*L)', re.I)
TRANSMISION_RE = re.compile(r'Transmisi[óo]n:\s*(\w+)', re.I)
GARANTIA_RE = re.compile(r'Garant[ií]a:\s*([^\n\-•]+)', re.I)
ENGANCHE_MIN_RE = re.compile(r'Enganche\s+m[ií]nimo:\s*\$?([\d,]+)', re.I)
ENGANCHE_REC_RE = re.compile(r'Enganche\s+recomendado:\s*\$?([\d,]+)', re.I)
MENSUALIDAD_MIN_RE = re.compile(
    r'Mensualidad\s+(?:m[ií]nima|con\s+enganche\s+m[ií]nimo):\s*\$?([\d,]+)', re.I)
MENSUALIDAD_REC_RE = re.compile(r'Mensualidad\s+recomendada:\s*\$?([\d,]+)', re.I)
PLAZO_RE = re.compile(r'Plazo\s+m[áa]ximo:\s*(\d+)', re.I)
URL_RE = re.compile(r'https?://autostrefa\.mx/autos/([\w-]+)')
NO_RESULTS_RE = re.compile(
    r'no\s+encontr[ée]|no\s+tenemos|no\s+hay\s+disponible|sin\s+resultados', re.I)
APARTADO_RE = re.compile(r'Apartado:\s*(S[ií])', re.I)
CARROCERIA_RE = re.compile(r'Carrocer[ií]a:\s*(\w+)', re.I)
COMBUSTIBLE_RE = re.compile(r'Combustible:\s*(\w+)', re.I)

# Quality patterns
GREETING_RE = re.compile(
    r"^\s*(hola|hey|buenos?\s*d[ií]as?|buenas?\s*tardes?|buenas?\s*noches?|"
    r"qu[eé]\s*tal|qué\s*onda|hi|hello|saludos|buen\s*d[ií]a)\s*[.!,?😊🙋👋]*\s*$",
    re.I)
NAME_ONLY_RE = re.compile(
    r"^\s*(?:me\s+llamo\s+|soy\s+|mi\s+nombre\s+es\s+)?\w+(?:\s+\w+)?\s*[.!]*\s*$", re.I)
VEHICLE_KW_RE = re.compile(
    r"(busc|quiero|necesito|interes|auto[s]?\b|carro|coche|camioneta|suv|sedan|"
    r"pick\s*up|presupuesto|precio|financ|crédito|credito|enganche|mensualidad|"
    r"comprar|cotiz|info\b|información|vender|intercambio|"
    r"toyota|honda|nissan|mazda|kia|hyundai|chevrolet|ford|volkswagen|"
    r"bmw|mercedes|audi|seat|renault|peugeot|suzuki|jeep|dodge|ram|"
    r"mitsubishi|subaru|buick|cadillac|lincoln|volvo|mini|fiat|mg\b|"
    r"corolla|civic|sentra|versa|march|cx-?[3579]|rav4|tucson|sportage|"
    r"sienna|prius|avanza|mirage|taigun|jimny|tracker|trax|kicks|"
    r"forte|rio\b|accent|aveo|spark|onix|jetta|golf|tiguan|polo|vento|"
    r"ranger|hilux|frontier|tacoma|wrangler|mustang|camaro|"
    r"cx-?30|cx-?50|hrv|crv|xtrail|qashqai|rogue|outlander|"
    r"3008|2008|5008|captiva|equinox|traverse|tahoe|suburban|"
    r"horario|sucursal|garantía|devoluc|documento|promo)", re.I)
USTED_PATTERNS = [
    (re.compile(r"\busted\b", re.I), "tú"),
    (re.compile(r"\bsu nombre\b", re.I), "tu nombre"),
    (re.compile(r"\bsu presupuesto\b", re.I), "tu presupuesto"),
    (re.compile(r"\bsu auto\b", re.I), "tu auto"),
    (re.compile(r"\bsu vehículo\b", re.I), "tu vehículo"),
    (re.compile(r"\ble interesa\b", re.I), "te interesa"),
    (re.compile(r"\ble gustaría\b", re.I), "te gustaría"),
    (re.compile(r"\ble parece\b", re.I), "te parece"),
    (re.compile(r"\ble puedo\b", re.I), "te puedo"),
    (re.compile(r"\ble ayudo\b", re.I), "te ayudo"),
    (re.compile(r"\ble recomiendo\b", re.I), "te recomiendo"),
    (re.compile(r"\ble muestro\b", re.I), "te muestro"),
    (re.compile(r"\ble comparto\b", re.I), "te comparto"),
    (re.compile(r"\bme comparte\b", re.I), "me compartes"),
    (re.compile(r"\bme diga\b", re.I), "me digas"),
    (re.compile(r"\bdígame\b", re.I), "dime"),
    (re.compile(r"\bdisculpe\b", re.I), "disculpa"),
]
FORMAL_RE = re.compile(
    r"(encantad[ao]\s+de\s+conocer|es\s+un\s+placer|un\s+gusto\s+conocer)", re.I)
BOLD_DETAIL_RE = re.compile(
    r"\*\*([^*]+(?:(?:\$|MXN|Automático|Manual|Gasolina|sucursal)[^*]*))\*\*", re.I)
VEHICLE_BULLET_RE = re.compile(r"[•\-\*]\s*\*\*.*?\*\*")
TOOL_CALL_RE_V9 = re.compile(r"<tool_call>\s*\{.*?\}\s*</tool_call>", re.DOTALL)
CTA_RE = re.compile(
    r"(aquí\s+est[oa]y|no\s+dudes|con\s+gusto|estoy\s+para|"
    r"te\s+espero|quedo\s+a\s+tus|a\s+tus\s+órdenes)", re.I)

# Stats tracker
stats = Counter()


# ─── UTILIDADES ────────────────────────────────────────────────────────

def parse_num(s: str) -> int:
    return int(re.sub(r'[,\s]', '', s))


def clean_enc(text: str) -> str:
    """Elimina caracteres de control problemáticos."""
    text = text.replace('\u000b', '\n').replace('\u000c', '\n')
    text = re.sub(r'[\x00-\x08\x0e-\x1f]', '', text)
    return text


def extract_brand_model(title: str) -> Tuple[Optional[str], Optional[str]]:
    """Extrae marca y modelo de un título como 'Mazda CX-5 i Sport 2023'."""
    t = title.strip()
    t = re.sub(r'\s*[—\-]\s*(?:Monterrey|Guadalupe|Saltillo|Reynosa).*$', '', t, flags=re.I)
    t = re.sub(r'\*+', '', t).strip()

    year_m = YEAR_RE.search(t)
    if year_m:
        t_no_year = t[:year_m.start()].strip()
    else:
        t_no_year = t

    for brand in sorted(BRANDS_SET, key=len, reverse=True):
        if brand.lower() in t_no_year.lower():
            norm = BRAND_NORMALIZE.get(brand.lower(), brand.title())
            idx = t_no_year.lower().index(brand.lower())
            modelo = t_no_year[idx + len(brand):].strip()
            modelo = re.sub(r'^[\s\-·]+', '', modelo).strip()
            if not modelo:
                modelo = None
            return norm, modelo
    return None, None


# ─── PARSERS DE TOOL CALLS ────────────────────────────────────────────

def parse_car_field(car: str, extra_args: dict = None) -> List[Tuple[str, dict]]:
    """Convierte el campo 'car' de Sub_Agent_-_Inventario1 a tool calls MCP.
    Retorna lista de (tool_name, arguments)."""
    text = car.strip()
    extra = extra_args or {}
    calls = []

    # Caso vacío → estadisticas_inventario
    if not text:
        calls.append(("estadisticas_inventario", {}))
        return calls

    args = {}
    text_lower = text.lower()

    # Detectar tipo carrocería
    for kw, body in BODY_TYPE_MAP.items():
        if kw in text_lower:
            args["tipo_carroceria"] = body
            break

    # Extraer año
    year_m = YEAR_RE.search(text)
    if year_m:
        y = int(year_m.group(1))
        args["año_minimo"] = y
        args["año_maximo"] = y

    # Extraer marca y modelo
    marca, modelo = extract_brand_model(text)
    if marca:
        args["marca"] = marca
    if modelo:
        args["modelo"] = modelo

    # Extraer precio si aparece en el texto
    price_m = re.findall(r'(\d[\d,]+)', text)
    if price_m and not marca and not year_m:
        nums = [parse_num(p) for p in price_m if parse_num(p) >= 10000]
        if len(nums) == 2:
            args["precio_minimo"] = min(nums)
            args["precio_maximo"] = max(nums)
        elif len(nums) == 1:
            args["precio_maximo"] = nums[0]

    if args:
        calls.append(("buscar_vehiculos", args))
    elif not args and text:
        calls.append(("buscar_vehiculos", {"marca": text.title()}))

    # Agregar calcular_financiamiento si hay params de financiamiento
    if any(k in extra for k in ['enganche', 'enganche_personalizado', 'plazo',
                                  'plazo_meses', 'mensualidad_personalizada']):
        fin_args = {}
        eng = extra.get('enganche') or extra.get('enganche_personalizado')
        if eng:
            fin_args["precio_vehiculo"] = eng * 5  # estimación
            fin_args["enganche_porcentaje"] = 20
        plazo = extra.get('plazo') or extra.get('plazo_meses')
        if plazo:
            fin_args["plazo_meses"] = int(plazo)
        calls.append(("calcular_financiamiento", fin_args))

    return calls


def parse_cita_input(input_text: str) -> dict:
    """Parsea el campo 'input' de Sub_Agent_-_Agendar_citas1 a params estructurados."""
    args = {}
    t = input_text.strip()

    # Nombre
    name_m = re.search(r'(?:Nombre(?:\s+completo)?:\s*|para\s+)([A-ZÁÉÍÓÚ][a-záéíóú]+(?:\s+[A-ZÁÉÍÓÚ][a-záéíóú]+)*)', t)
    if name_m:
        args["nombre_cliente"] = name_m.group(1).strip()

    # Sucursal
    for suc in ["Monterrey", "Guadalupe", "Saltillo", "Reynosa"]:
        if suc.lower() in t.lower():
            args["sucursal"] = suc
            break

    # Fecha
    fecha_m = re.search(r'(?:Día|Fecha|día|fecha)[\s:]*([^\n,]+)', t)
    if fecha_m:
        args["fecha"] = fecha_m.group(1).strip()

    # Hora
    hora_m = re.search(r'(?:Hora|hora)[\s:]*([^\n,]+)', t)
    if hora_m:
        args["hora"] = hora_m.group(1).strip()

    # Vehículo
    veh_m = re.search(r'(?:ver|para)\s+((?:Toyota|Honda|Nissan|Mazda|Kia|Chevrolet|Ford|Hyundai|Suzuki|Peugeot|Renault|BMW|Jeep|Dodge|Ram|Volkswagen)\s+[^\n,]+)', t, re.I)
    if veh_m:
        args["vehiculo_interes"] = veh_m.group(1).strip()

    if "nombre_cliente" not in args:
        args["nombre_cliente"] = "Cliente"
    if "sucursal" not in args:
        args["sucursal"] = "Monterrey"

    return args


# ─── PARSERS DE TOOL RESPONSES ────────────────────────────────────────

def parse_vehicle_block(block: str) -> Optional[dict]:
    """Parsea un bloque de texto de vehículo a dict estructurado."""
    v = {}

    # Título - primera línea
    title_m = re.match(r'(?:\d+\)\s*)?(.+?)(?:\n|$)', block)
    if not title_m:
        return None
    raw_title = title_m.group(1).strip()
    raw_title = re.sub(r'\s*\(opci[oó]n\s+(?:alterna|similar)[^)]*\)', '', raw_title, flags=re.I)
    raw_title = re.sub(r'\s*[—\-]\s*$', '', raw_title).strip()

    loc_in_title = re.search(r'\s*[—\-]\s*(Monterrey|Guadalupe|Saltillo|Reynosa)', raw_title, re.I)
    if loc_in_title:
        raw_title = raw_title[:loc_in_title.start()].strip()

    v["titulo"] = raw_title
    marca, modelo = extract_brand_model(raw_title)
    if marca:
        v["marca"] = marca
    if modelo:
        v["modelo"] = modelo
    year_m = YEAR_RE.search(raw_title)
    if year_m:
        v["autoano"] = int(year_m.group(1))

    # Campos del bloque
    m = PRECIO_RE.search(block)
    if m:
        v["precio"] = f"${m.group(1)}"
    m = KM_RE.search(block)
    if m:
        v["kilometraje"] = f"{m.group(1)} km"
    m = UBICACION_RE.search(block)
    if m:
        v["ubicacion"] = m.group(1).strip()
    elif loc_in_title:
        v["ubicacion"] = loc_in_title.group(1)
    m = MOTOR_RE.search(block)
    if m:
        v["motor"] = m.group(1)
    m = TRANSMISION_RE.search(block)
    if m:
        v["transmision"] = m.group(1)
    m = GARANTIA_RE.search(block)
    if m:
        v["garantia"] = m.group(1).strip()
    m = ENGANCHE_MIN_RE.search(block)
    if m:
        v["enganchemin"] = parse_num(m.group(1))
    m = ENGANCHE_REC_RE.search(block)
    if m:
        v["enganche_recomendado"] = parse_num(m.group(1))
    m = MENSUALIDAD_MIN_RE.search(block)
    if m:
        v["mensualidad_minima"] = parse_num(m.group(1))
    m = MENSUALIDAD_REC_RE.search(block)
    if m:
        v["mensualidad_recomendada"] = parse_num(m.group(1))
    m = PLAZO_RE.search(block)
    if m:
        v["plazomax"] = int(m.group(1))
    m = URL_RE.search(block)
    if m:
        v["slug"] = m.group(1)
        v["liga_web"] = m.group(0)
    m = CARROCERIA_RE.search(block)
    if m:
        v["carroceria"] = m.group(1)
    m = COMBUSTIBLE_RE.search(block)
    if m:
        v["combustible"] = m.group(1)

    # Mínimo: título + precio
    if "titulo" not in v or "precio" not in v:
        return None
    return v


def parse_inventario_response(text: str) -> dict:
    """Parsea respuesta de Sub_Agent_-_Inventario1 a JSON estructurado."""
    text = clean_enc(text)

    # Sin resultados
    if NO_RESULTS_RE.search(text) and not PRECIO_RE.search(text):
        stats["resp_no_results"] += 1
        return {"vehiculos": [], "total": 0,
                "mensaje": "No encontramos vehículos con esos criterios en este momento."}

    # Dividir en bloques de vehículos
    blocks = re.split(r'\n(?=\d+\)\s)', text)
    vehicles = []
    for block in blocks:
        block = block.strip()
        if not re.match(r'\d+\)', block):
            continue
        v = parse_vehicle_block(block)
        if v:
            vehicles.append(v)

    if vehicles:
        stats["resp_parsed_ok"] += 1
        return {"vehiculos": vehicles, "total": len(vehicles)}

    # Fallback: intentar extraer un solo vehículo del texto completo
    single = parse_vehicle_block(f"1) {text}")
    if single:
        stats["resp_parsed_single"] += 1
        return {"vehiculos": [single], "total": 1}

    # Fallback final: cotización o texto no parseable
    # Extraer datos de cotización si existen
    precio_m = PRECIO_RE.search(text)
    eng_m = ENGANCHE_MIN_RE.search(text)
    mens_m = MENSUALIDAD_MIN_RE.search(text)
    if precio_m and (eng_m or mens_m):
        stats["resp_parsed_cotizacion"] += 1
        result = {"cotizacion": True}
        if precio_m:
            result["precio_vehiculo"] = f"${precio_m.group(1)}"
        if eng_m:
            result["enganche"] = parse_num(eng_m.group(1))
        if mens_m:
            result["mensualidad"] = parse_num(mens_m.group(1))
        plazo_m = PLAZO_RE.search(text)
        if plazo_m:
            result["plazo_meses"] = int(plazo_m.group(1))
        return result

    stats["resp_parse_failed"] += 1
    return {"vehiculos": [], "total": 0, "texto_original": text[:500]}


def parse_info_response(text: str) -> dict:
    """Parsea respuesta de Informacion_TREFA1."""
    text = clean_enc(text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            resultados = []
            for item in data:
                r = {}
                if "content" in item:
                    r["contenido"] = item["content"][:1000]
                if "documentId" in item:
                    r["id"] = item["documentId"]
                resultados.append(r)
            return {"resultados": resultados, "total": len(resultados)}
    except json.JSONDecodeError:
        pass
    return {"resultados": [{"contenido": text[:1000]}], "total": 1}


def parse_cita_response(text: str) -> dict:
    """Parsea respuesta de Sub_Agent_-_Agendar_citas1."""
    text = clean_enc(text)
    try:
        data = json.loads(text)
        if isinstance(data, list) and data:
            msg = data[0].get("output", text)
            return {"confirmacion": True, "mensaje": msg[:500]}
    except json.JSONDecodeError:
        pass
    return {"confirmacion": True, "mensaje": text[:500]}


# ─── TRANSFORMACIONES ──────────────────────────────────────────────────

def t2_merge_consecutive_users(messages: list) -> list:
    """Fusiona mensajes de usuario consecutivos."""
    result = []
    for msg in messages:
        if result and result[-1]["role"] == "user" and msg["role"] == "user":
            result[-1] = {
                "role": "user",
                "content": result[-1]["content"] + "\n" + msg["content"]
            }
            stats["t2_merged"] += 1
        else:
            result.append(dict(msg))
    return result


def t3_t6_split_and_serialize(messages: list) -> list:
    """T3: Separa content de tool_calls. T6: Serializa parallel calls.
    Produce: [assistant:visible] [assistant:tool_call] [tool:response] ... [assistant:visible_post]"""
    result = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg["role"] == "assistant" and "tool_calls" in msg:
            visible_content = msg.get("content", "").strip()
            tool_calls = msg["tool_calls"]

            # Recopilar tool responses que siguen
            tool_responses = []
            j = i + 1
            while j < len(messages) and messages[j]["role"] == "tool":
                tool_responses.append(messages[j])
                j += 1

            # Serializar cada tool_call con su response
            for k, tc in enumerate(tool_calls):
                result.append({
                    "role": "assistant",
                    "_tool_call_raw": tc,
                    "content": ""  # placeholder, se llena en t4
                })
                if k < len(tool_responses):
                    result.append({
                        "role": "tool",
                        "_source_name": tc["function"]["name"],
                        "_raw_content": tool_responses[k].get("content", ""),
                        "content": ""  # placeholder, se llena en t5
                    })
                stats["t3_serialized"] += 1

            # Agregar contenido visible DESPUÉS de todos los tool call/response
            if visible_content:
                result.append({
                    "role": "assistant",
                    "content": visible_content,
                })
                stats["t3_visible_split"] += 1

            i = j  # saltar tool responses ya procesados
        else:
            result.append(dict(msg))
            i += 1

    return result


def t4_remap_tools(messages: list) -> list:
    """Remapea tool calls del formato fuente al formato MCP."""
    result = []
    for msg in messages:
        if msg["role"] == "assistant" and "_tool_call_raw" in msg:
            tc = msg["_tool_call_raw"]
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                fn_args = {}

            if fn_name == "Sub_Agent_-_Inventario1":
                car = fn_args.get("car", "")
                extra = {k: v for k, v in fn_args.items() if k != "car"}
                mapped_calls = parse_car_field(car, extra)
                # Usar solo el primer call para este turno
                tool_name, tool_args = mapped_calls[0]
                call_json = json.dumps(
                    {"name": tool_name, "arguments": tool_args},
                    ensure_ascii=False)
                msg = {"role": "assistant",
                       "content": f"<tool_call>\n{call_json}\n</tool_call>",
                       "_mapped_tool": tool_name}
                stats[f"t4_remap_{tool_name}"] += 1

            elif fn_name == "Sub_Agent_-_Agendar_citas1":
                input_text = fn_args.get("input", "")
                cita_args = parse_cita_input(input_text)
                call_json = json.dumps(
                    {"name": "agendar_cita", "arguments": cita_args},
                    ensure_ascii=False)
                msg = {"role": "assistant",
                       "content": f"<tool_call>\n{call_json}\n</tool_call>",
                       "_mapped_tool": "agendar_cita"}
                stats["t4_remap_agendar_cita"] += 1

            elif fn_name == "Informacion_TREFA1":
                call_json = json.dumps(
                    {"name": "obtener_info_negocio",
                     "arguments": {"tema": "garantias"}},
                    ensure_ascii=False)
                msg = {"role": "assistant",
                       "content": f"<tool_call>\n{call_json}\n</tool_call>",
                       "_mapped_tool": "obtener_info_negocio"}
                stats["t4_remap_obtener_info_negocio"] += 1
            else:
                stats["t4_unknown_tool"] += 1
                msg = None  # descartar tool desconocida

        if msg is not None:
            result.append(msg)
    return result


def t5_convert_responses(messages: list) -> list:
    """Convierte tool responses a JSON estructurado en <tool_response> tags."""
    result = []
    for msg in messages:
        if msg["role"] == "tool" and "_raw_content" in msg:
            source_name = msg.get("_source_name", "")
            raw = msg["_raw_content"]

            if source_name == "Sub_Agent_-_Inventario1":
                parsed = parse_inventario_response(raw)
            elif source_name == "Informacion_TREFA1":
                parsed = parse_info_response(raw)
            elif source_name == "Sub_Agent_-_Agendar_citas1":
                parsed = parse_cita_response(raw)
            else:
                parsed = {"contenido": raw[:500]}

            resp_json = json.dumps(parsed, ensure_ascii=False)
            msg = {"role": "tool",
                   "content": f"<tool_response>\n{resp_json}\n</tool_response>"}
        result.append(msg)
    return result


def t7_clean_fields(messages: list) -> list:
    """Elimina campos internos y ChatML."""
    clean = []
    for msg in messages:
        m = {"role": msg["role"], "content": msg.get("content", "")}
        # Limpiar encoding
        m["content"] = clean_enc(m["content"])
        # Eliminar content vacío en mensajes que no son tool_call
        if not m["content"].strip() and "<tool_call>" not in m.get("content", ""):
            continue
        clean.append(m)
    return clean


# ─── VALIDACIÓN GATES ──────────────────────────────────────────────────

def validate_gates(messages: list) -> Tuple[bool, str]:
    """Valida gates de entrada. Retorna (valid, reason)."""

    # G1: longitud mínima
    if len(messages) < 3:  # system + user + assistant
        return False, "G1_too_short"

    # G2: primer mensaje system
    if messages[0]["role"] != "system":
        return False, "G2_no_system"

    # G3: flujo de roles válido
    for i in range(1, len(messages)):
        curr = messages[i]["role"]
        prev = messages[i - 1]["role"]
        if curr == "tool" and prev != "assistant":
            # tool sin assistant previo (a menos que sea tool tras tool en serialización)
            if prev != "tool":
                return False, "G3_tool_without_assistant"

    # G4: cada tool_call tiene tool_response
    tc_count = sum(1 for m in messages if "<tool_call>" in m.get("content", ""))
    tr_count = sum(1 for m in messages if m["role"] == "tool")
    if tc_count != tr_count:
        return False, f"G4_mismatch_tc{tc_count}_tr{tr_count}"

    # G5: JSON válido en tool_calls y responses
    for m in messages:
        content = m.get("content", "")
        if "<tool_call>" in content:
            try:
                j = re.search(r'\{.*\}', content, re.DOTALL)
                if j:
                    json.loads(j.group(0))
            except json.JSONDecodeError:
                return False, "G5_invalid_json_toolcall"
        if "<tool_response>" in content:
            try:
                inner = re.search(r'<tool_response>\s*(.*?)\s*</tool_response>',
                                  content, re.DOTALL)
                if inner:
                    json.loads(inner.group(1))
            except json.JSONDecodeError:
                return False, "G5_invalid_json_response"

    # G6: tool names conocidos
    known_tools = {
        "buscar_vehiculos", "obtener_vehiculo", "buscar_alternativas",
        "comparar_vehiculos", "estadisticas_inventario", "calcular_financiamiento",
        "buscar_informacion", "obtener_info_negocio", "obtener_faqs",
        "solicitar_datos_contacto", "enviar_cotizacion_email",
        "agendar_cita", "evaluar_intercambio",
    }
    for m in messages:
        if "<tool_call>" in m.get("content", ""):
            j = re.search(r'"name":\s*"([^"]+)"', m["content"])
            if j and j.group(1) not in known_tools:
                return False, f"G6_unknown_tool_{j.group(1)}"

    return True, "ok"


# ─── CALIDAD ───────────────────────────────────────────────────────────

def get_visible_turns(messages: list) -> list:
    """Retorna índices de turnos assistant con texto visible."""
    visible = []
    for i, m in enumerate(messages):
        if m["role"] == "assistant":
            content = m.get("content", "")
            if "<tool_call>" not in content and content.strip():
                visible.append(i)
    return visible


def apply_quality(messages: list) -> Tuple[list, list, bool]:
    """Aplica criterios de calidad C1-C14. Retorna (messages, fixes, should_remove)."""
    fixes = []

    visible = get_visible_turns(messages)

    # C1: Primer turno visible debe contener "Mariana" y terminar con "?"
    if visible:
        first_v = messages[visible[0]]["content"]
        has_mariana = "mariana" in first_v.lower()
        has_q = "?" in first_v

        if not has_mariana and has_q:
            # FIX: inyectar saludo de Mariana antes del primer turno visible
            greeting = ("¡Hola! Soy Mariana de Autos TREFA 😊. "
                        "Voy a revisar lo que tenemos para ti, ¿va?")
            # Insertar greeting como nuevo turno assistant antes del actual
            idx_first = visible[0]
            messages.insert(idx_first, {"role": "assistant", "content": greeting})
            fixes.append("C1_fix_injected_greeting")
            stats["c1_fix_injected"] += 1
        elif not has_mariana and not has_q:
            # Sin Mariana y sin pregunta — inyectar saludo + agregar ? al final
            greeting = ("¡Hola! Soy Mariana de Autos TREFA 😊. "
                        "Voy a revisar lo que tenemos para ti, ¿va?")
            idx_first = visible[0]
            messages.insert(idx_first, {"role": "assistant", "content": greeting})
            fixes.append("C1_fix_injected_greeting_full")
            stats["c1_fix_injected_full"] += 1
        elif has_mariana and not has_q:
            # Tiene Mariana pero sin pregunta — agregar pregunta
            messages[visible[0]]["content"] = first_v.rstrip() + " ¿En qué te puedo ayudar?"
            fixes.append("C1_fix_added_question")
            stats["c1_fix_added_q"] += 1

    # C2: No tools antes de criterio
    user_has_criteria = False
    for m in messages:
        if m["role"] == "user":
            if VEHICLE_KW_RE.search(m.get("content", "")):
                user_has_criteria = True
        if m["role"] == "assistant" and not user_has_criteria:
            if "<tool_call>" in m.get("content", ""):
                tc_json = re.search(r'"name":\s*"([^"]+)"', m["content"])
                if tc_json and tc_json.group(1) in {"buscar_vehiculos", "buscar_alternativas"}:
                    fixes.append("C2_tool_before_criteria")
                    return messages, fixes, True

    # C9: No tools tras saludo simple
    user_msgs = [m for m in messages if m["role"] == "user"]
    if user_msgs:
        first_u = user_msgs[0].get("content", "").strip()
        is_simple = (GREETING_RE.match(first_u) or
                     (NAME_ONLY_RE.match(first_u) and not VEHICLE_KW_RE.search(first_u)
                      and len(first_u.split()) <= 4))
        if is_simple:
            # Buscar primer assistant después del primer user
            for m in messages:
                if m["role"] == "assistant" and "<tool_call>" in m.get("content", ""):
                    tc_json = re.search(r'"name":\s*"([^"]+)"', m["content"])
                    if tc_json and tc_json.group(1) in {"buscar_vehiculos", "buscar_alternativas"}:
                        fixes.append("C9_tool_after_greeting")
                        return messages, fixes, True
                elif m["role"] == "assistant" and m.get("content", "").strip():
                    break  # primer assistant visible sin tool, ok

    # C3: Máximo 3 opciones
    new_msgs = []
    for m in messages:
        if m["role"] == "assistant":
            bullets = VEHICLE_BULLET_RE.findall(m.get("content", ""))
            if len(bullets) > 3:
                lines = m["content"].split("\n")
                new_lines, bc = [], 0
                for line in lines:
                    if VEHICLE_BULLET_RE.search(line):
                        bc += 1
                        if bc > 3:
                            continue
                    new_lines.append(line)
                m = {**m, "content": "\n".join(new_lines)}
                fixes.append("C3_max_3")
        new_msgs.append(m)
    messages = new_msgs

    # C4: Negritas solo en nombre del auto
    new_msgs = []
    for m in messages:
        if m["role"] == "assistant":
            content = m.get("content", "")
            new_c = BOLD_DETAIL_RE.sub(
                lambda x: f"**{re.split(r'[—,$]', x.group(1))[0].strip()}**", content)
            if new_c != content:
                m = {**m, "content": new_c}
                fixes.append("C4_bold_fix")
        new_msgs.append(m)
    messages = new_msgs

    # C6: Tuteo
    new_msgs = []
    for m in messages:
        if m["role"] == "assistant":
            content = m.get("content", "")
            for pat, repl in USTED_PATTERNS:
                content, n = pat.subn(repl, content)
                if n > 0 and "C6_tuteo" not in fixes:
                    fixes.append("C6_tuteo")
            m = {**m, "content": content}
        new_msgs.append(m)
    messages = new_msgs

    # C7: Sin formalidades
    new_msgs = []
    for m in messages:
        if m["role"] == "assistant":
            content = m.get("content", "")
            new_c = FORMAL_RE.sub("", content)
            new_c = re.sub(r"  +", " ", new_c).strip()
            if new_c != content.strip():
                m = {**m, "content": new_c}
                fixes.append("C7_formal")
        new_msgs.append(m)
    messages = new_msgs

    return messages, fixes, False


# ─── PIPELINE PRINCIPAL ───────────────────────────────────────────────

def convert_conversation(conv: list, index: int) -> Tuple[Optional[dict], dict]:
    """Pipeline completo de conversión. Retorna (resultado, info)."""
    info = {"index": index, "original_len": len(conv)}

    # T2: Fusionar usuarios consecutivos
    msgs = t2_merge_consecutive_users(conv)

    # T3 + T6: Separar content/tool_calls y serializar
    msgs = t3_t6_split_and_serialize(msgs)

    # T4: Remapear tools
    msgs = t4_remap_tools(msgs)

    # T5: Convertir tool responses
    msgs = t5_convert_responses(msgs)

    # T7: Limpiar campos internos
    msgs = t7_clean_fields(msgs)

    # T1: Inyectar system prompt
    msgs.insert(0, {"role": "system", "content": "__SYSTEM_PROMPT__"})

    # Validar gates
    valid, reason = validate_gates(msgs)
    if not valid:
        info["rejected"] = reason
        stats[f"gate_{reason}"] += 1
        return None, info

    # Aplicar calidad
    msgs, fixes, should_remove = apply_quality(msgs)
    info["fixes"] = fixes
    if should_remove:
        info["rejected"] = fixes[0] if fixes else "quality"
        stats[f"quality_{info['rejected']}"] += 1
        return None, info

    # Metadata
    result = {
        "messages": msgs,
        "metadata": {
            "source": "real_conversations",
            "dataset_version": "v10_real",
            "original_index": index,
            "original_len": len(conv),
            "final_len": len(msgs),
            "quality_fixes": fixes if fixes else None,
        }
    }
    stats["converted_ok"] += 1
    return result, info


# ─── AUDITORÍA ─────────────────────────────────────────────────────────

def audit_conversations(conversations: list, n: int = 30) -> dict:
    """Audita n conversaciones aleatorias del output."""
    random.seed(42)
    sample = random.sample(conversations, min(n, len(conversations)))

    checks = {
        "total": len(sample),
        "pass": 0, "fail": 0,
        "issues": Counter(),
        "details": [],
    }

    for i, conv in enumerate(sample):
        msgs = conv["messages"]
        issues = []

        # A1: System prompt presente
        if msgs[0]["role"] != "system":
            issues.append("A1_no_system")

        # A2: Saludo con Mariana
        visible = [m for m in msgs if m["role"] == "assistant"
                   and "<tool_call>" not in m.get("content", "")
                   and m["content"].strip()]
        if visible:
            if "mariana" not in visible[0]["content"].lower():
                issues.append("A2_no_mariana")
            if "?" not in visible[0]["content"]:
                issues.append("A2_no_question")

        # A3: Tuteo (sin usted)
        for m in msgs:
            if m["role"] == "assistant" and re.search(r'\busted\b', m.get("content", ""), re.I):
                issues.append("A3_usted")
                break

        # A4: Tool call format
        for m in msgs:
            if m["role"] == "assistant" and "<tool_call>" in m.get("content", ""):
                try:
                    j = re.search(r'\{.*\}', m["content"], re.DOTALL)
                    parsed = json.loads(j.group(0))
                    if "name" not in parsed:
                        issues.append("A4_no_tool_name")
                except Exception:
                    issues.append("A4_invalid_json")

        # A5: Tool response format
        for m in msgs:
            if m["role"] == "tool":
                if "<tool_response>" not in m.get("content", ""):
                    issues.append("A5_no_response_tag")
                else:
                    try:
                        inner = re.search(
                            r'<tool_response>\s*(.*?)\s*</tool_response>',
                            m["content"], re.DOTALL)
                        json.loads(inner.group(1))
                    except Exception:
                        issues.append("A5_invalid_json")

        # A6: Max 3 opciones por turno
        for m in msgs:
            if m["role"] == "assistant":
                bullets = VEHICLE_BULLET_RE.findall(m.get("content", ""))
                if len(bullets) > 3:
                    issues.append("A6_more_than_3")

        # A7: Cierre con pregunta
        if visible:
            last_v = visible[-1]["content"].strip()
            last_line = last_v.split("\n")[-1]
            if "?" not in last_line and not CTA_RE.search(last_line):
                issues.append("A7_no_closing_question")

        # A8: Flujo de roles coherente
        for j in range(1, len(msgs)):
            if msgs[j]["role"] == "tool" and msgs[j-1]["role"] not in ("assistant", "tool"):
                issues.append("A8_bad_flow")
                break

        # A9: Sin mensajes user consecutivos
        for j in range(1, len(msgs)):
            if msgs[j]["role"] == "user" and msgs[j-1]["role"] == "user":
                issues.append("A9_consecutive_users")
                break

        # A10: Sin content vacío
        for m in msgs[1:]:
            if not m.get("content", "").strip():
                issues.append("A10_empty_content")
                break

        # A11: Tool names válidos
        known = {"buscar_vehiculos", "obtener_vehiculo", "buscar_alternativas",
                 "comparar_vehiculos", "estadisticas_inventario", "calcular_financiamiento",
                 "buscar_informacion", "obtener_info_negocio", "obtener_faqs",
                 "solicitar_datos_contacto", "enviar_cotizacion_email",
                 "agendar_cita", "evaluar_intercambio"}
        for m in msgs:
            if "<tool_call>" in m.get("content", ""):
                nm = re.search(r'"name":\s*"([^"]+)"', m["content"])
                if nm and nm.group(1) not in known:
                    issues.append(f"A11_unknown_{nm.group(1)}")

        # A12: Sin formalidades
        for m in msgs:
            if m["role"] == "assistant" and FORMAL_RE.search(m.get("content", "")):
                issues.append("A12_formal")
                break

        # Resultado
        passed = len(issues) == 0
        if passed:
            checks["pass"] += 1
        else:
            checks["fail"] += 1
        for iss in issues:
            checks["issues"][iss] += 1
        checks["details"].append({
            "idx": conv.get("metadata", {}).get("original_index", i),
            "msgs": len(msgs),
            "passed": passed,
            "issues": issues,
            "first_user": next(
                (m["content"][:60] for m in msgs if m["role"] == "user"), "?"),
        })

    return checks


# ─── MAIN ──────────────────────────────────────────────────────────────

def main():
    src = Path(__file__).parent.parent / "source_conversations_mariana_chatml.json"
    if not src.exists():
        print(f"ERROR: No se encontró {src}")
        sys.exit(1)

    print(f"{'='*60}")
    print(f"  Pipeline de Conversión v10 — Conversaciones Reales")
    print(f"{'='*60}")

    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    print(f"  Cargadas: {len(data)} conversaciones del documento fuente")

    converted = []
    rejected = []

    for i, conv in enumerate(data):
        result, info = convert_conversation(conv, i)
        if result:
            converted.append(result)
        else:
            rejected.append(info)

    print(f"\n  Resultados de conversión:")
    print(f"    Convertidas:  {len(converted)}")
    print(f"    Rechazadas:   {len(rejected)}")
    print(f"    Tasa éxito:   {len(converted)/len(data)*100:.1f}%")

    # Split train/eval 90/10
    random.seed(42)
    indices = list(range(len(converted)))
    random.shuffle(indices)
    split = int(len(indices) * 0.9)
    train = [converted[i] for i in indices[:split]]
    eval_ = [converted[i] for i in indices[split:]]

    # Guardar
    out_dir = Path(__file__).parent
    train_path = out_dir / "v10_real_train.jsonl"
    eval_path = out_dir / "v10_real_eval.jsonl"
    rejected_path = out_dir / "v10_real_rejected.jsonl"

    with open(train_path, "w", encoding="utf-8") as f:
        for c in train:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    with open(eval_path, "w", encoding="utf-8") as f:
        for c in eval_:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    with open(rejected_path, "w", encoding="utf-8") as f:
        for r in rejected:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n  Archivos generados:")
    print(f"    Train: {train_path.name} ({len(train)} conversaciones)")
    print(f"    Eval:  {eval_path.name} ({len(eval_)} conversaciones)")
    print(f"    Rechazadas: {rejected_path.name} ({len(rejected)})")

    # Estadísticas detalladas
    print(f"\n  Estadísticas del pipeline:")
    for key in sorted(stats.keys()):
        print(f"    {key}: {stats[key]}")

    # Razones de rechazo
    reject_reasons = Counter(r.get("rejected", "unknown") for r in rejected)
    print(f"\n  Razones de rechazo:")
    for reason, count in reject_reasons.most_common():
        print(f"    {reason}: {count}")

    # Distribución de longitudes
    lengths = [len(c["messages"]) for c in converted]
    print(f"\n  Longitud de conversaciones convertidas:")
    print(f"    Min: {min(lengths)}")
    print(f"    Max: {max(lengths)}")
    print(f"    Promedio: {sum(lengths)/len(lengths):.1f}")

    # Tools usados
    tool_usage = Counter()
    for c in converted:
        for m in c["messages"]:
            if "<tool_call>" in m.get("content", ""):
                nm = re.search(r'"name":\s*"([^"]+)"', m["content"])
                if nm:
                    tool_usage[nm.group(1)] += 1
    print(f"\n  Distribución de tools en output:")
    for tool, count in tool_usage.most_common():
        print(f"    {tool}: {count}")

    # ─── AUDITORÍA ───────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  AUDITORÍA DE SALIDA — 30 conversaciones aleatorias")
    print(f"{'='*60}")

    audit = audit_conversations(converted, 30)
    print(f"\n  Resultados:")
    print(f"    Pasaron:  {audit['pass']}/{audit['total']}")
    print(f"    Fallaron: {audit['fail']}/{audit['total']}")
    print(f"    Score:    {audit['pass']/audit['total']*100:.1f}%")

    if audit["issues"]:
        print(f"\n  Issues encontrados:")
        for iss, count in audit["issues"].most_common():
            print(f"    {iss}: {count}")

    print(f"\n  Detalle por conversación auditada:")
    for d in audit["details"]:
        status = "✓" if d["passed"] else "✗"
        issues_str = ", ".join(d["issues"]) if d["issues"] else "—"
        print(f"    {status} idx={d['idx']:>4} msgs={d['msgs']:>2}  "
              f"\"{d['first_user'][:45]}\"  [{issues_str}]")

    print(f"\n{'='*60}")
    print(f"  Pipeline completado")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
