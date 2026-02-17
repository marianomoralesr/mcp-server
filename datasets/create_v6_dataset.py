#!/usr/bin/env python3
"""
create_v6_dataset.py — Pipeline para crear dataset v6 TREFA/Mariana

Fases:
  1. Carga conversaciones de v5_dataset y gold_upgraded (_cleaned.jsonl)
  2. Detección programática de problemas (P1-P4)
  3. Corrección de P3 con Gemini Flash (insertar obtener_vehiculo)
  4. Validación de calidad con Gemini Flash
  5. Generación de v6_dataset/ con conversaciones aprobadas

Uso:
  export TREFA_GEMINI_API_KEY=tu_key
  python3 create_v6_dataset.py
  python3 create_v6_dataset.py --dry-run          # solo clasificar, sin API
  python3 create_v6_dataset.py --skip-fix          # solo validar, no corregir P3
  python3 create_v6_dataset.py --model gemini-2.0-flash
  python3 create_v6_dataset.py --threshold 7.5
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ============================================================
# CONFIG
# ============================================================
DATASETS_DIR = Path(__file__).parent
V5_DIR = DATASETS_DIR / "v5_dataset"
GOLD_DIR = DATASETS_DIR / "gold_upgraded"
V6_DIR = DATASETS_DIR / "v6_dataset"
CHECKPOINT_DIR = V6_DIR / "_checkpoints"

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_THRESHOLD = 7.5
EVAL_SPLIT = 0.10
RATE_LIMIT_DELAY = 0.3  # segundos entre llamadas API
MAX_RETRIES = 3
RETRY_DELAY = 5  # segundos

# Patrones para detectar problemas
RE_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
RE_TOOL_RESPONSE = re.compile(r"<tool_response>\s*(\{.*?\})\s*</tool_response>", re.DOTALL)
RE_XML_LEAK = re.compile(r"<(?:tool_call|tool_response|tools|function_call|\|tool_call\|)[>\s]")
RE_INTEREST = re.compile(
    r"(?:me\s+interesa|cuéntame\s+más|quiero\s+(?:saber|ver)|"
    r"el\s+(?:primero|segundo|tercero|primer|últim)|"
    r"(?:más\s+)?(?:info(?:rmación)?|detall)|"
    r"ese\s+(?:me\s+)?(?:gusta|llama|interesa)|"
    r"(?:dime|háblame)\s+(?:más|del)|"
    r"(?:cuánto|qué\s+tal)\s+(?:cuesta|sale|queda)|"
    r"me\s+(?:gusta|llama\s+la\s+atención)|"
    r"ese\s+está\s+(?:bien|padre|chido)|"
    r"(?:sí|si),?\s+(?:ese|el\s+\w+)|"
    r"vamos\s+con\s+(?:ese|el))",
    re.IGNORECASE,
)


# ============================================================
# HELPERS
# ============================================================
def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def load_jsonl(path: Path) -> list[dict]:
    """Carga archivo JSONL, retorna lista de conversaciones."""
    convs = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                convs.append(obj)
            except json.JSONDecodeError as e:
                log(f"  WARN: {path.name} línea {i+1}: JSON inválido: {e}")
    return convs


def save_jsonl(path: Path, convs: list[dict]):
    """Guarda lista de conversaciones como JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for conv in convs:
            f.write(json.dumps(conv, ensure_ascii=False) + "\n")


def normalize_metadata(conv: dict) -> dict:
    """Normaliza campo de metadata: _meta → metadata."""
    if "_meta" in conv and "metadata" not in conv:
        conv["metadata"] = conv.pop("_meta")
    if "metadata" not in conv:
        conv["metadata"] = {}
    return conv


def get_messages(conv: dict) -> list[dict]:
    return conv.get("messages", [])


def save_checkpoint(name: str, data):
    """Guarda checkpoint para resume."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_checkpoint(name: str):
    """Carga checkpoint si existe."""
    path = CHECKPOINT_DIR / f"{name}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ============================================================
# PHASE 1: LOAD CONVERSATIONS
# ============================================================
def load_all_conversations() -> list[dict]:
    """Carga todas las conversaciones de v5 y gold_upgraded."""
    all_convs = []

    # v5_dataset
    v5_file = V5_DIR / "dataset_v5_completo_cleaned.jsonl"
    if v5_file.exists():
        convs = load_jsonl(v5_file)
        for c in convs:
            c = normalize_metadata(c)
            c["metadata"]["_source_file"] = str(v5_file.name)
            c["metadata"]["_source_dir"] = "v5_dataset"
        all_convs.extend(convs)
        log(f"  v5_completo: {len(convs)} conversaciones")

    # gold_upgraded - solo batch_*_cleaned.jsonl y synthetic_cleaned_cleaned.jsonl
    if GOLD_DIR.exists():
        for f in sorted(GOLD_DIR.glob("*_cleaned.jsonl")):
            if ".bak" in f.name:
                continue
            # Excluir archivos intermedios y de descarte
            if f.name.startswith("removed_"):
                continue
            # Si existe _cleaned_cleaned, preferir esa y saltar la _cleaned simple
            double_cleaned = f.parent / f.name.replace("_cleaned.jsonl", "_cleaned_cleaned.jsonl")
            if double_cleaned.exists() and f.name != double_cleaned.name and "_cleaned_cleaned" not in f.name:
                continue
            # Excluir synthetic_cleaned.jsonl si existe synthetic_cleaned_cleaned.jsonl
            if f.name == "synthetic_cleaned.jsonl" and (f.parent / "synthetic_cleaned_cleaned.jsonl").exists():
                continue
            convs = load_jsonl(f)
            for c in convs:
                c = normalize_metadata(c)
                c["metadata"]["_source_file"] = str(f.name)
                c["metadata"]["_source_dir"] = "gold_upgraded"
            all_convs.extend(convs)
            log(f"  {f.name}: {len(convs)} conversaciones")

    log(f"Total cargadas: {len(all_convs)}")
    return all_convs


# ============================================================
# PHASE 2: DETECT ISSUES (programmatic)
# ============================================================
def extract_tool_calls(messages: list[dict]) -> list[dict]:
    """Extrae todas las tool calls de una conversación."""
    calls = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        for m in RE_TOOL_CALL.finditer(content):
            try:
                tc = json.loads(m.group(1))
                calls.append({"index": i, "name": tc.get("name"), "args": tc.get("arguments", {})})
            except json.JSONDecodeError:
                calls.append({"index": i, "name": "PARSE_ERROR", "args": {}})
    return calls


def detect_p1(messages: list[dict]) -> bool:
    """P1: Presenta vehículos antes de que el usuario pregunte."""
    user_asked = False
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            continue
        if role == "user":
            # El usuario pregunta por vehículos si menciona marca, modelo, tipo, precio, etc.
            if re.search(r"(?:busco|quiero|necesito|me\s+interesa|auto|carro|camioneta|suv|sedan|"
                         r"toyota|honda|mazda|nissan|volkswagen|vw|chevrolet|ford|hyundai|kia|bmw|"
                         r"mercedes|audi|precio|presupuesto|financ|crédito)", content, re.IGNORECASE):
                user_asked = True
        if role == "assistant" and not user_asked:
            # Busca presentación de vehículos (con datos específicos)
            if re.search(r"(?:\$\d{2,3},?\d{3}|(?:Mazda|Toyota|Honda|Nissan|BMW)\s+\w+\s+\d{4}|"
                         r"(?:te\s+)?(?:tenemos|tengo|recomiendo|muestro).*(?:vehículo|auto|carro))",
                         content, re.IGNORECASE):
                return True
    return False


def detect_p2(messages: list[dict]) -> bool:
    """P2: XML tags escapadas al usuario en mensajes del assistant."""
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        # Busca tool calls con texto adicional (no deben tener texto fuera del tag)
        if RE_TOOL_CALL.search(content):
            # Está bien si SOLO es el tool call
            cleaned = RE_TOOL_CALL.sub("", content).strip()
            if len(cleaned) > 50:  # Hay texto significativo junto al tool call
                return True
            continue
        # Busca tags sueltos
        if RE_XML_LEAK.search(content):
            return True
    return False


def detect_p3(messages: list[dict]) -> dict:
    """
    P3: Falta obtener_vehiculo después de interés del usuario.
    Retorna {has_p3, interest_index, vehicle_ids, buscar_results_index}
    """
    tool_calls = extract_tool_calls(messages)
    has_buscar = any(tc["name"] == "buscar_vehiculos" for tc in tool_calls)
    has_obtener = any(tc["name"] == "obtener_vehiculo" for tc in tool_calls)

    if not has_buscar:
        return {"has_p3": False, "reason": "no_buscar"}

    if has_obtener:
        return {"has_p3": False, "reason": "has_obtener"}

    # Buscar punto de interés del usuario después de resultados de búsqueda
    buscar_indices = [tc["index"] for tc in tool_calls if tc["name"] == "buscar_vehiculos"]
    last_buscar_idx = max(buscar_indices)

    # Buscar la respuesta tool_response después de buscar
    buscar_response_idx = None
    vehicle_ids = []
    for i in range(last_buscar_idx + 1, len(messages)):
        msg = messages[i]
        content = msg.get("content", "")
        if msg.get("role") == "tool" or (msg.get("role") == "user" and "<tool_response>" in content):
            m = RE_TOOL_RESPONSE.search(content)
            if m:
                try:
                    resp = json.loads(m.group(1))
                    # Extraer IDs de vehículos (varios formatos posibles)
                    vehs = []
                    if isinstance(resp.get("vehiculos"), list):
                        vehs = resp["vehiculos"]
                    elif isinstance(resp.get("content"), dict) and isinstance(resp["content"].get("vehiculos"), list):
                        vehs = resp["content"]["vehiculos"]
                    elif isinstance(resp.get("content"), list):
                        vehs = resp["content"]
                    if isinstance(vehs, list):
                        vehicle_ids = [v.get("id") for v in vehs if isinstance(v, dict) and v.get("id")]
                    buscar_response_idx = i
                except (json.JSONDecodeError, AttributeError):
                    pass
            break

    if not vehicle_ids:
        return {"has_p3": False, "reason": "no_results"}

    # Buscar interés del usuario después de la respuesta de búsqueda
    # (después de que Mariana presenta las opciones)
    interest_idx = None
    for i in range(last_buscar_idx + 2, len(messages)):
        msg = messages[i]
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if RE_INTEREST.search(content):
            interest_idx = i
            break

    if interest_idx is None:
        return {"has_p3": False, "reason": "no_interest"}

    return {
        "has_p3": True,
        "interest_index": interest_idx,
        "vehicle_ids": vehicle_ids,
        "buscar_response_index": buscar_response_idx,
    }


def detect_p4(messages: list[dict]) -> bool:
    """P4: Tool calls truncadas o mal formadas."""
    for msg in messages:
        content = msg.get("content", "")
        if "<tool_call>" in content and "</tool_call>" not in content:
            return True
        if "<tool_response>" in content and "</tool_response>" not in content:
            return True
        # JSON inválido dentro de tool_call
        for m in RE_TOOL_CALL.finditer(content):
            try:
                json.loads(m.group(1))
            except json.JSONDecodeError:
                return True
    return False


def detect_role_errors(messages: list[dict]) -> bool:
    """Detecta tool_response con role:user en vez de role:tool."""
    for msg in messages:
        if msg.get("role") == "user" and "<tool_response>" in msg.get("content", ""):
            return True
    return False


def classify_conversations(convs: list[dict]) -> list[dict]:
    """Clasifica cada conversación con sus problemas detectados."""
    log("Fase 2: Clasificando conversaciones...")
    for i, conv in enumerate(convs):
        msgs = get_messages(conv)
        if not msgs:
            conv["metadata"]["_issues"] = ["empty"]
            conv["metadata"]["_status"] = "discard"
            continue

        issues = []
        p3_info = detect_p3(msgs)

        if detect_p1(msgs):
            issues.append("P1_vehiculos_antes")
        if detect_p2(msgs):
            issues.append("P2_xml_leak")
        if p3_info["has_p3"]:
            issues.append("P3_falta_obtener")
            conv["metadata"]["_p3_info"] = p3_info
        if detect_p4(msgs):
            issues.append("P4_truncated")
        if detect_role_errors(msgs):
            issues.append("role_error")
        if len(msgs) < 4:
            issues.append("too_short")

        conv["metadata"]["_issues"] = issues
        if not issues:
            conv["metadata"]["_status"] = "clean"
        elif issues == ["P3_falta_obtener"]:
            conv["metadata"]["_status"] = "fixable_p3"
        else:
            conv["metadata"]["_status"] = "discard"

        if (i + 1) % 200 == 0:
            log(f"  Clasificadas {i+1}/{len(convs)}")

    # Resumen
    statuses = {}
    issue_counts = {}
    for conv in convs:
        st = conv["metadata"].get("_status", "unknown")
        statuses[st] = statuses.get(st, 0) + 1
        for iss in conv["metadata"].get("_issues", []):
            issue_counts[iss] = issue_counts.get(iss, 0) + 1

    log(f"  Clasificación: {statuses}")
    log(f"  Problemas: {issue_counts}")
    return convs


# ============================================================
# PHASE 3: GEMINI PROMPTS
# ============================================================
PROMPT_FIX_P3 = """Eres un ingeniero de calidad de datos de entrenamiento para Mariana, una asistente de ventas de autos IA para Autos TREFA (agencia de seminuevos en México).

Esta conversación tiene un problema: después de buscar vehículos (buscar_vehiculos), el usuario mostró interés en un vehículo específico, pero la asistente NO llamó a obtener_vehiculo para obtener detalles completos.

El flujo correcto es:
1. buscar_vehiculos → retorna lista resumen
2. Usuario expresa interés en un vehículo específico
3. Asistente llama obtener_vehiculo(id=X) para detalles completos
4. Asistente presenta la info detallada al usuario

REGLAS para la corrección:
- Inserta un mensaje assistant con SOLO: <tool_call>\\n{"name":"obtener_vehiculo","arguments":{"id":X}}\\n</tool_call>
- Seguido de un mensaje role:"tool" con: <tool_response>\\n{"name":"obtener_vehiculo","content":{...detalles...}}\\n</tool_response>
- El contenido de obtener_vehiculo debe expandir los datos del vehículo con: galería (URLs ficticias plausibles de autostrefa.mx), historial, seguro, liga_mariana
- Ajusta el mensaje del asistente que sigue para que naturalmente use la info detallada
- NO cambies el system prompt (__SYSTEM_PROMPT__)
- NO cambies los mensajes del usuario
- NO cambies las tool calls que ya existen
- Mantén el tono conversacional mexicano natural
- Los precios SIEMPRE como strings: "$359,900" (NO números)
- El kilometraje SIEMPRE como string: "28,000 km"
- Las URLs deben ser https://autostrefa.mx/inventario/{slug} y https://autostrefa.mx/bots/{slug}

FORMATO obtener_vehiculo response:
{
  "name": "obtener_vehiculo",
  "content": {
    "id": <number>,
    "titulo": "<marca> <modelo> <año>",
    "marca": "...",
    "modelo": "...",
    "año": <number>,
    "precio": "$XXX,XXX",
    "precio_numerico": <number>,
    "transmision": "Automática|Manual",
    "combustible": "Gasolina|Diesel|Híbrido",
    "carroceria": "SUV|Sedan|Hatchback|Pickup",
    "motor": "2.0L",
    "cilindros": 4,
    "ubicacion": "Monterrey|Guadalupe|Saltillo|Reynosa",
    "kilometraje": "XX,XXX km",
    "garantia": "12 meses|6 meses",
    "enganche_minimo": "$XX,XXX",
    "mensualidad_desde": "$X,XXX",
    "url": "https://autostrefa.mx/inventario/<slug>",
    "liga_mariana": "https://autostrefa.mx/bots/<slug>",
    "galeria": ["https://autostrefa.mx/fotos/<slug>/1.jpg", "..."],
    "historial": "Seminuevo certificado, único dueño",
    "seguro": "Incluido en paquete"
  }
}

CONVERSACIÓN A CORREGIR:
{conversation}

IDs de vehículos disponibles del buscar_vehiculos: {vehicle_ids}
Índice del mensaje donde el usuario muestra interés: {interest_index}

Responde SOLO con el JSON array de messages corregido. Sin texto adicional, sin markdown, sin explicaciones."""

PROMPT_VALIDATE = """Evalúa esta conversación de entrenamiento para Mariana (asistente IA de ventas de autos, Autos TREFA).

Califica 1-10 en cada criterio:

1. **naturalidad**: ¿Suena como un cliente real mexicano en WhatsApp? Tuteo, coloquialismos, flujo natural.
2. **coherencia**: ¿Las respuestas son coherentes? ¿Se mantiene el contexto? ¿No hay saltos lógicos?
3. **calidad_tc**: Si hay tool calls: ¿formato correcto (<tool_call>/<tool_response>)? ¿role:tool (no role:user)? ¿Precios como strings "$XXX,XXX"? ¿Kilometraje como string? ¿Un tool_call por mensaje sin texto extra? Si NO hay TC: puntaje 7.
4. **valor_finetuning**: ¿Enseña comportamientos útiles? ¿Cubre escenario real? ¿Evita repeticiones triviales?
5. **flujo_vehiculos**: ¿Flujo correcto? (buscar → interés → detalles → financiamiento). ¿NO presenta vehículos antes de que el usuario pregunte? Si no hay búsqueda de vehículos (es otra consulta), puntaje 7.

FALLAS AUTOMÁTICAS (puntaje_total = 0, veredicto = "descartar"):
- Vehículos presentados ANTES de que el usuario pregunte
- Tags XML visibles al usuario fuera de tool_call
- Tool calls truncadas o JSON inválido
- role:"user" para tool responses (debe ser role:"tool")
- Precios como números (359900) en vez de strings ("$359,900")
- Texto mezclado con <tool_call> en el mismo mensaje
- Menos de 3 mensajes útiles (system + user + assistant)
- Asistente rompe personaje o viola system prompt

CONVERSACIÓN:
{conversation}

Responde SOLO con JSON (sin markdown, sin texto extra):
{{
  "naturalidad": N,
  "coherencia": N,
  "calidad_tc": N,
  "valor_finetuning": N,
  "flujo_vehiculos": N,
  "puntaje_total": N.N,
  "veredicto": "conservar"|"descartar",
  "issues": ["issue1", ...]
}}"""


# ============================================================
# PHASE 3-4: GEMINI API
# ============================================================
def init_gemini(api_key: str, model_name: str):
    """Inicializa cliente Gemini."""
    try:
        import google.generativeai as genai
    except ImportError:
        log("ERROR: google-generativeai no instalado. Ejecuta: pip install google-generativeai")
        sys.exit(1)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name,
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 16384,
            "response_mime_type": "application/json",
        },
    )
    log(f"Gemini inicializado: {model_name}")
    return model


def call_gemini(model, prompt: str, retries: int = MAX_RETRIES) -> Optional[str]:
    """Llama a Gemini con reintentos."""
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response.text:
                return response.text.strip()
            log(f"  WARN: Gemini retornó respuesta vacía")
            return None
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait = RETRY_DELAY * (attempt + 1) * 2
                log(f"  Rate limit, esperando {wait}s...")
                time.sleep(wait)
            elif "500" in err_str or "503" in err_str:
                wait = RETRY_DELAY * (attempt + 1)
                log(f"  Error servidor ({err_str[:60]}), reintentando en {wait}s...")
                time.sleep(wait)
            else:
                log(f"  ERROR Gemini: {err_str[:120]}")
                if attempt < retries - 1:
                    time.sleep(RETRY_DELAY)
                else:
                    return None
    return None


def fix_p3_conversation(model, conv: dict) -> Optional[dict]:
    """Corrige una conversación P3 usando Gemini."""
    messages = get_messages(conv)
    p3_info = conv["metadata"].get("_p3_info", {})

    prompt = PROMPT_FIX_P3.format(
        conversation=json.dumps(messages, ensure_ascii=False, indent=2),
        vehicle_ids=json.dumps(p3_info.get("vehicle_ids", [])),
        interest_index=p3_info.get("interest_index", "desconocido"),
    )

    result = call_gemini(model, prompt)
    if not result:
        return None

    try:
        # Limpiar posible markdown wrapping
        result = result.strip()
        if result.startswith("```"):
            result = re.sub(r"^```(?:json)?\s*", "", result)
            result = re.sub(r"\s*```$", "", result)

        fixed_messages = json.loads(result)
        if not isinstance(fixed_messages, list) or len(fixed_messages) < len(messages):
            log(f"  WARN: Respuesta Gemini inválida (tipo={type(fixed_messages).__name__}, len={len(fixed_messages) if isinstance(fixed_messages, list) else 'N/A'})")
            return None

        # Verificar que se insertó obtener_vehiculo
        has_obtener = False
        for msg in fixed_messages:
            content = msg.get("content", "")
            if "obtener_vehiculo" in content and "<tool_call>" in content:
                has_obtener = True
                break

        if not has_obtener:
            log(f"  WARN: Gemini no insertó obtener_vehiculo")
            return None

        fixed_conv = dict(conv)
        fixed_conv["messages"] = fixed_messages
        fixed_conv["metadata"]["_fixed_p3"] = True
        fixed_conv["metadata"]["_fix_model"] = model.model_name if hasattr(model, 'model_name') else "gemini"
        fixed_conv["metadata"]["_fix_timestamp"] = datetime.now().isoformat()
        return fixed_conv

    except json.JSONDecodeError as e:
        log(f"  WARN: JSON inválido en respuesta Gemini: {e}")
        return None


def validate_conversation(model, conv: dict) -> Optional[dict]:
    """Valida una conversación usando Gemini. Retorna scores o None."""
    messages = get_messages(conv)

    prompt = PROMPT_VALIDATE.format(
        conversation=json.dumps(messages, ensure_ascii=False, indent=2),
    )

    result = call_gemini(model, prompt)
    if not result:
        return None

    try:
        result = result.strip()
        if result.startswith("```"):
            result = re.sub(r"^```(?:json)?\s*", "", result)
            result = re.sub(r"\s*```$", "", result)

        scores = json.loads(result)
        if not isinstance(scores, dict) or "puntaje_total" not in scores:
            log(f"  WARN: Formato de scores inválido")
            return None
        return scores

    except json.JSONDecodeError:
        return None


# ============================================================
# PHASE 3: FIX P3 CONVERSATIONS
# ============================================================
def fix_p3_batch(model, convs: list[dict]) -> list[dict]:
    """Corrige todas las conversaciones P3."""
    fixable = [c for c in convs if c["metadata"].get("_status") == "fixable_p3"]
    log(f"Fase 3: Corrigiendo {len(fixable)} conversaciones P3...")

    # Cargar checkpoint
    checkpoint = load_checkpoint("fix_p3")
    fixed_indices = set()
    if checkpoint:
        fixed_indices = set(checkpoint.get("fixed_indices", []))
        log(f"  Checkpoint: {len(fixed_indices)} ya corregidas")

    fixed_count = 0
    failed_count = 0

    for i, conv in enumerate(convs):
        if conv["metadata"].get("_status") != "fixable_p3":
            continue

        # Generar un ID estable para checkpoint
        conv_id = f"{conv['metadata'].get('_source_file', '')}_{i}"
        if conv_id in fixed_indices:
            fixed_count += 1
            continue

        fixed = fix_p3_conversation(model, conv)
        if fixed:
            convs[i] = fixed
            convs[i]["metadata"]["_status"] = "fixed_p3"
            fixed_count += 1
        else:
            convs[i]["metadata"]["_status"] = "discard"
            convs[i]["metadata"]["_issues"].append("fix_failed")
            failed_count += 1

        fixed_indices.add(conv_id)

        if (fixed_count + failed_count) % 10 == 0:
            log(f"  Progreso: {fixed_count} corregidas, {failed_count} fallidas de {len(fixable)}")
            save_checkpoint("fix_p3", {"fixed_indices": list(fixed_indices)})

        time.sleep(RATE_LIMIT_DELAY)

    log(f"  P3 Fix: {fixed_count} corregidas, {failed_count} fallidas")
    save_checkpoint("fix_p3", {"fixed_indices": list(fixed_indices), "done": True})
    return convs


# ============================================================
# PHASE 4: VALIDATE ALL CONVERSATIONS
# ============================================================
def validate_batch(model, convs: list[dict], threshold: float) -> list[dict]:
    """Valida todas las conversaciones candidatas con Gemini."""
    candidates = [
        c for c in convs
        if c["metadata"].get("_status") in ("clean", "fixed_p3")
    ]
    log(f"Fase 4: Validando {len(candidates)} conversaciones (threshold={threshold})...")

    # Cargar checkpoint
    checkpoint = load_checkpoint("validate")
    validated = {}
    if checkpoint:
        validated = checkpoint.get("validated", {})
        log(f"  Checkpoint: {len(validated)} ya validadas")

    passed = 0
    failed = 0

    for i, conv in enumerate(convs):
        status = conv["metadata"].get("_status")
        if status not in ("clean", "fixed_p3"):
            continue

        conv_id = f"{conv['metadata'].get('_source_file', '')}_{i}"

        if conv_id in validated:
            scores = validated[conv_id]
        else:
            scores = validate_conversation(model, conv)
            if scores:
                validated[conv_id] = scores
            else:
                scores = {"puntaje_total": 0, "veredicto": "descartar", "issues": ["api_error"]}
                validated[conv_id] = scores

            time.sleep(RATE_LIMIT_DELAY)

        # Aplicar scores
        conv["metadata"]["_eval_scores"] = scores
        puntaje = scores.get("puntaje_total", 0)

        if scores.get("veredicto") == "conservar" and puntaje >= threshold:
            conv["metadata"]["_status"] = "approved"
            passed += 1
        else:
            conv["metadata"]["_status"] = "rejected"
            conv["metadata"]["_reject_reason"] = scores.get("issues", [])
            failed += 1

        total_done = passed + failed
        if total_done % 20 == 0:
            log(f"  Progreso: {passed} aprobadas, {failed} rechazadas de {len(candidates)}")
            save_checkpoint("validate", {"validated": validated})

    log(f"  Validación: {passed} aprobadas, {failed} rechazadas (threshold={threshold})")
    save_checkpoint("validate", {"validated": validated, "done": True})
    return convs


# ============================================================
# PHASE 5: OUTPUT V6
# ============================================================
def output_v6(convs: list[dict], eval_split: float = EVAL_SPLIT):
    """Genera los archivos finales del dataset v6."""
    log("Fase 5: Generando dataset v6...")

    # Filtrar aprobadas
    approved = []
    for conv in convs:
        if conv["metadata"].get("_status") != "approved":
            continue

        # Limpiar campos internos de metadata
        clean_meta = {}
        for k, v in conv["metadata"].items():
            if not k.startswith("_"):
                clean_meta[k] = v
        # Agregar campos v6
        clean_meta["v6"] = True
        clean_meta["v6_timestamp"] = datetime.now().isoformat()
        if conv["metadata"].get("_fixed_p3"):
            clean_meta["fixed_p3"] = True
        if conv["metadata"].get("_eval_scores"):
            scores = conv["metadata"]["_eval_scores"]
            clean_meta["eval_score"] = scores.get("puntaje_total", 0)

        approved.append({
            "messages": conv["messages"],
            "metadata": clean_meta,
        })

    if not approved:
        log("  ERROR: No hay conversaciones aprobadas!")
        return

    # Shuffle y split
    random.seed(42)
    random.shuffle(approved)

    n_eval = max(1, int(len(approved) * eval_split))
    eval_set = approved[:n_eval]
    train_set = approved[n_eval:]

    # Guardar archivos
    save_jsonl(V6_DIR / "v6_train.jsonl", train_set)
    save_jsonl(V6_DIR / "v6_eval.jsonl", eval_set)
    save_jsonl(V6_DIR / "v6_completo.jsonl", approved)

    # Meta sidecars para FastAPI
    now = datetime.now().isoformat()
    for name, dataset, count in [
        ("v6_train", train_set, len(train_set)),
        ("v6_eval", eval_set, len(eval_set)),
        ("v6_completo", approved, len(approved)),
    ]:
        meta = {
            "name": name,
            "description": f"Dataset v6 TREFA/Mariana - {name}",
            "created": now,
            "conversations": count,
            "sources": ["v5_dataset", "gold_upgraded"],
            "pipeline": "create_v6_dataset.py",
            "eval_threshold": DEFAULT_THRESHOLD,
            "version": "v6",
        }
        meta_path = V6_DIR / f"{name}.meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    # Reporte detallado
    report = generate_report(convs, approved, train_set, eval_set)
    report_path = V6_DIR / "v6_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log(f"  Dataset v6 generado:")
    log(f"    Train: {len(train_set)} conversaciones")
    log(f"    Eval:  {len(eval_set)} conversaciones")
    log(f"    Total: {len(approved)} conversaciones")
    log(f"    Archivos en: {V6_DIR}")


def generate_report(all_convs, approved, train_set, eval_set) -> dict:
    """Genera reporte detallado del pipeline."""
    status_counts = {}
    issue_counts = {}
    source_counts = {}
    score_distribution = {"0-3": 0, "3-5": 0, "5-7": 0, "7-8": 0, "8-9": 0, "9-10": 0}

    for conv in all_convs:
        meta = conv.get("metadata", {})
        st = meta.get("_status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1

        src = meta.get("_source_dir", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1

        for iss in meta.get("_issues", []):
            issue_counts[iss] = issue_counts.get(iss, 0) + 1

        scores = meta.get("_eval_scores", {})
        puntaje = scores.get("puntaje_total", -1)
        if puntaje >= 0:
            if puntaje < 3:
                score_distribution["0-3"] += 1
            elif puntaje < 5:
                score_distribution["3-5"] += 1
            elif puntaje < 7:
                score_distribution["5-7"] += 1
            elif puntaje < 8:
                score_distribution["7-8"] += 1
            elif puntaje < 9:
                score_distribution["8-9"] += 1
            else:
                score_distribution["9-10"] += 1

    return {
        "timestamp": datetime.now().isoformat(),
        "total_input": len(all_convs),
        "total_approved": len(approved),
        "train_count": len(train_set),
        "eval_count": len(eval_set),
        "approval_rate": f"{len(approved)/len(all_convs)*100:.1f}%",
        "status_breakdown": status_counts,
        "issues_found": issue_counts,
        "by_source": source_counts,
        "score_distribution": score_distribution,
    }


# ============================================================
# DRY RUN (solo clasificación, sin API)
# ============================================================
def dry_run(convs: list[dict]):
    """Ejecuta solo la clasificación sin llamar a Gemini."""
    log("=== DRY RUN — solo clasificación ===")

    # Mostrar detalles
    clean = [c for c in convs if c["metadata"].get("_status") == "clean"]
    fixable = [c for c in convs if c["metadata"].get("_status") == "fixable_p3"]
    discard = [c for c in convs if c["metadata"].get("_status") == "discard"]

    log(f"\nResumen:")
    log(f"  Limpias (pasan directo a validación): {len(clean)}")
    log(f"  P3 corregibles (Gemini las arregla):  {len(fixable)}")
    log(f"  Descartadas (P1/P2/P4/múltiples):     {len(discard)}")
    log(f"  Candidatas totales para v6:             {len(clean) + len(fixable)}")

    # Desglose por fuente
    for source in ["v5_dataset", "gold_upgraded"]:
        src_convs = [c for c in convs if c["metadata"].get("_source_dir") == source]
        src_clean = len([c for c in src_convs if c["metadata"].get("_status") == "clean"])
        src_fix = len([c for c in src_convs if c["metadata"].get("_status") == "fixable_p3"])
        src_disc = len([c for c in src_convs if c["metadata"].get("_status") == "discard"])
        log(f"\n  {source}: {len(src_convs)} total")
        log(f"    Limpias: {src_clean}, P3 corregibles: {src_fix}, Descartadas: {src_disc}")

    # Guardar clasificación para referencia
    save_jsonl(V6_DIR / "_dry_run_clean.jsonl", clean)
    save_jsonl(V6_DIR / "_dry_run_fixable_p3.jsonl", fixable)
    save_jsonl(V6_DIR / "_dry_run_discard.jsonl", discard)
    log(f"\nArchivos de clasificación guardados en {V6_DIR}")


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Pipeline v6 dataset TREFA/Mariana")
    parser.add_argument("--dry-run", action="store_true", help="Solo clasificar, sin API Gemini")
    parser.add_argument("--skip-fix", action="store_true", help="No corregir P3, solo validar limpias")
    parser.add_argument("--skip-validate", action="store_true", help="No validar con Gemini, aprobar todas las limpias/fijas")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Modelo Gemini (default: {DEFAULT_MODEL})")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"Umbral mínimo (default: {DEFAULT_THRESHOLD})")
    parser.add_argument("--resume", action="store_true", help="Resumir desde checkpoints")
    args = parser.parse_args()

    log("=" * 60)
    log("TREFA v6 Dataset Pipeline")
    log("=" * 60)

    # Fase 1: Cargar
    log("Fase 1: Cargando conversaciones...")
    convs = load_all_conversations()
    if not convs:
        log("ERROR: No se encontraron conversaciones")
        sys.exit(1)

    # Fase 2: Clasificar
    convs = classify_conversations(convs)

    if args.dry_run:
        dry_run(convs)
        return

    # Verificar API key
    api_key = os.environ.get("TREFA_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log("ERROR: TREFA_GEMINI_API_KEY o GEMINI_API_KEY no definida")
        log("  export TREFA_GEMINI_API_KEY=tu_api_key")
        sys.exit(1)

    model = init_gemini(api_key, args.model)

    # Fase 3: Corregir P3
    if not args.skip_fix:
        convs = fix_p3_batch(model, convs)

    # Fase 4: Validar
    if not args.skip_validate:
        convs = validate_batch(model, convs, args.threshold)
    else:
        # Aprobar todas las clean y fixed sin validación Gemini
        for conv in convs:
            if conv["metadata"].get("_status") in ("clean", "fixed_p3"):
                conv["metadata"]["_status"] = "approved"

    # Fase 5: Generar v6
    output_v6(convs)

    log("=" * 60)
    log("Pipeline completado!")
    log("=" * 60)


if __name__ == "__main__":
    main()
