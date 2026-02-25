"""
analyzers.py — Análisis de tool patterns y actualización batch de system prompt.
Extraído de identificar_tools_sinteticos2.py y actualizar_system_prompt.py.
"""

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from typing import Optional

from app.job_manager import JobManager

# Herramientas conocidas
TOOLS_CONOCIDAS = {
    "buscar_vehiculos", "obtener_vehiculo", "buscar_alternativas",
    "comparar_vehiculos", "estadisticas_inventario", "calcular_financiamiento",
    "buscar_informacion", "obtener_info_negocio", "obtener_faqs",
    "solicitar_datos_contacto", "enviar_cotizacion_email",
}

TOOL_CALL_PATTERNS = [
    re.compile(r'<tool_call>\s*\{[^}]*"name"\s*:\s*"(\w+)"', re.DOTALL),
    re.compile(r'"function_call"\s*:\s*\{[^}]*"name"\s*:\s*"(\w+)"'),
    re.compile(r'Action:\s*(\w+)\s*\nAction Input:'),
    re.compile(r'"tool_calls"\s*:\s*\[\s*\{[^}]*"name"\s*:\s*"(\w+)"'),
    re.compile(
        r'\b(buscar_vehiculos|obtener_vehiculo|buscar_alternativas|comparar_vehiculos|'
        r'estadisticas_inventario|calcular_financiamiento|buscar_informacion|'
        r'obtener_info_negocio|obtener_faqs|solicitar_datos_contacto|enviar_cotizacion_email)\s*\('
    ),
]

ESCENARIO_PATTERNS = {
    "ESC-1_no_inventario": [r'no\s+tienen', r'no\s+hay', r'no\s+encuentr'],
    "ESC-2_precio_general": [r'cuánto\s+cuestan', r'qué\s+precios', r'rango\s+de\s+precios'],
    "ESC-3_financiamiento": [r'financ', r'crédit', r'mensualidad', r'enganche', r'plazos?'],
    "ESC-4_documentos": [r'document', r'papel', r'requisito', r'qué\s+necesito'],
    "ESC-5_garantia": [r'garant[íi]a', r'cubre', r'falla', r'descompon'],
    "ESC-6_ubicacion": [r'ubicaci[óo]n', r'sucursal', r'direcci[óo]n', r'horario', r'd[óo]nde\s+están'],
    "ESC-7_tradein": [r'intercambio', r'cambiar\s+mi\s+auto', r'tomar\s+a\s+cuenta', r'trade'],
    "ESC-8_devolucion": [r'devoluci[óo]n', r'devolver', r'regresar\s+el\s+auto'],
    "ESC-9_comparar": [r'compar', r'cu[áa]l\s+es\s+mejor', r'diferencia\s+entre'],
    "ESC-10_fuera_tema": [r'seguro\s+de\s+auto', r'refacci[óo]n', r'taller', r'rent', r'mecánic'],
    "ESC-11_frustrado": [r'quej', r'molest', r'frustr', r'enojad', r'pésim', r'terrible'],
    "ESC-12_saludo": [r'^hola\s*$', r'^buenas?\s*(tardes|noches|días|d[íi]as)?\.?\s*$', r'^hey\s*$', r'^qué\s+tal'],
    "ESC-13_ambiguo": [r'^quiero\s+uno', r'^cuánto\s+cuesta\??$', r'^me\s+interesa$', r'^información$'],
    "ESC-14_stats": [r'cuántos\s+autos', r'inventario', r'qué\s+marcas'],
    "ESC-15_visita": [r'prueba\s+de\s+manejo', r'verlo\s+en\s+persona', r'visitar'],
    "ESC-16_proceso": [r'c[óo]mo\s+compro', r'proceso\s+de\s+compra', r'c[óo]mo\s+funciona'],
    "ESC-17_cotizacion": [r'cotizaci[óo]n', r'correo', r'email', r'mand[ae]'],
    "LIM-1_legal": [r'impuesto', r'fiscal', r'deduci', r'legal', r'seguro\s+de'],
    "LIM-3_negociar": [r'descuento', r'rebaj', r'negoci', r'menos', r'más\s+barato', r'igualar'],
    "LIM-5_reservar": [r'reserv', r'apart', r'guard', r'depósito'],
    "LIM-6_bot": [r'robot', r'bot', r'real\s+o', r'humano', r'persona\s+real', r'inteligencia\s+artificial'],
    "LIM-7_mecanico": [r'diagnóstic', r'ruido', r'falla\s+mecánic', r'confiable', r'problem'],
}


def detectar_tools(mensaje: str) -> list[str]:
    tools = []
    for pattern in TOOL_CALL_PATTERNS:
        for m in pattern.findall(mensaje):
            if m in TOOLS_CONOCIDAS:
                tools.append(m)
    return tools


def detectar_escenario(mensaje_usuario: str) -> list[str]:
    msg_lower = mensaje_usuario.lower().strip()
    escenarios = []
    for esc, patterns in ESCENARIO_PATTERNS.items():
        for p in patterns:
            if re.search(p, msg_lower):
                escenarios.append(esc)
                break
    return escenarios


def hash_conversacion(messages: list) -> str:
    for m in messages:
        if m.get("role") == "user":
            return hashlib.md5(m.get("content", "")[:200].encode()).hexdigest()
    return hashlib.md5(str(messages).encode()).hexdigest()


def analyze_tool_patterns(filepath: str) -> dict:
    """Analiza un archivo JSONL y retorna estadísticas de tool patterns."""
    resultado = {
        "archivo": os.path.basename(filepath),
        "total": 0,
        "con_tools": 0,
        "sin_tools": 0,
        "porcentaje": 0.0,
        "tools_por_herramienta": {},
        "combos": {},
        "escenarios_detectados": {},
        "herramientas_no_encontradas": [],
        "formatos": {},
        "errores": 0,
    }

    tools_counter = Counter()
    combos_counter = Counter()
    escenarios_counter = Counter()
    formatos_counter = Counter()
    hashes_vistos = set()

    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                resultado["errores"] += 1
                continue

            messages = data.get("messages", [])
            if not messages:
                continue

            resultado["total"] += 1
            h = hash_conversacion(messages)
            if h in hashes_vistos:
                continue
            hashes_vistos.add(h)

            tools_en_conv = []
            primer_user_msg = ""

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user" and not primer_user_msg:
                    primer_user_msg = content
                if role == "assistant":
                    found = detectar_tools(content)
                    tools_en_conv.extend(found)
                    if "<tool_call>" in content:
                        formatos_counter["qwen_chatml"] += 1
                    elif "function_call" in content:
                        formatos_counter["function_call"] += 1

            if tools_en_conv:
                resultado["con_tools"] += 1
                for t in tools_en_conv:
                    tools_counter[t] += 1
                combo = tuple(sorted(set(tools_en_conv)))
                combos_counter[combo] += 1
            else:
                resultado["sin_tools"] += 1

            for esc in detectar_escenario(primer_user_msg):
                escenarios_counter[esc] += 1

    resultado["porcentaje"] = round(
        resultado["con_tools"] / max(resultado["total"], 1) * 100, 1
    )
    resultado["tools_por_herramienta"] = dict(tools_counter.most_common())
    resultado["combos"] = {" + ".join(k): v for k, v in combos_counter.most_common(30)}
    resultado["escenarios_detectados"] = dict(escenarios_counter.most_common())
    resultado["herramientas_no_encontradas"] = list(
        TOOLS_CONOCIDAS - set(tools_counter.keys())
    )
    resultado["formatos"] = dict(formatos_counter)
    return resultado


async def analyze_tool_patterns_batch(
    job_manager: JobManager,
    job_id: str,
    filepaths: list[str],
) -> dict:
    """Analiza múltiples archivos y retorna reporte consolidado."""
    resultados = []
    for i, fp in enumerate(filepaths):
        if job_manager.is_cancelled(job_id):
            break
        r = analyze_tool_patterns(fp)
        resultados.append(r)
        pct = int((i + 1) / len(filepaths) * 100)
        job_manager.update_progress(job_id, pct, f"{i+1}/{len(filepaths)} archivos")

    total = sum(r["total"] for r in resultados)
    con_tools = sum(r["con_tools"] for r in resultados)
    tools_global = Counter()
    escenarios_global = Counter()
    for r in resultados:
        for t, n in r["tools_por_herramienta"].items():
            tools_global[t] += n
        for e, n in r["escenarios_detectados"].items():
            escenarios_global[e] += n

    return {
        "total_conversaciones": total,
        "con_tool_calling": con_tools,
        "sin_tool_calling": total - con_tools,
        "porcentaje_tools": round(con_tools / max(total, 1) * 100, 1),
        "tools_por_herramienta": dict(tools_global.most_common()),
        "herramientas_no_encontradas": list(TOOLS_CONOCIDAS - set(tools_global.keys())),
        "escenarios_detectados": dict(escenarios_global.most_common()),
        "archivos": resultados,
    }


def actualizar_jsonl(ruta: str, prompt: str) -> dict:
    """Reemplaza/inyecta system prompt en cada conversación del JSONL."""
    if not os.path.exists(ruta):
        return {"archivo": ruta, "status": "NO ENCONTRADO", "total": 0, "actualizadas": 0}

    lineas = []
    actualizadas = 0
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                conv = json.loads(linea)
            except json.JSONDecodeError:
                lineas.append(linea)
                continue

            msgs = conv.get("messages", [])
            if not msgs:
                lineas.append(json.dumps(conv, ensure_ascii=False))
                continue

            if msgs[0].get("role") == "system":
                msgs[0]["content"] = prompt
            else:
                msgs.insert(0, {"role": "system", "content": prompt})
            actualizadas += 1
            conv["messages"] = msgs
            lineas.append(json.dumps(conv, ensure_ascii=False))

    with open(ruta, "w", encoding="utf-8") as f:
        for linea in lineas:
            f.write(linea + "\n")

    return {
        "archivo": os.path.basename(ruta),
        "status": "OK",
        "total": len(lineas),
        "actualizadas": actualizadas,
    }


async def update_system_prompt_batch(
    job_manager: JobManager,
    job_id: str,
    target_files: list[str],
    new_system_prompt: str,
) -> dict:
    """Actualiza system prompt en múltiples archivos JSONL."""
    resultados = []
    for i, fp in enumerate(target_files):
        if job_manager.is_cancelled(job_id):
            break
        r = actualizar_jsonl(fp, new_system_prompt)
        resultados.append(r)
        pct = int((i + 1) / len(target_files) * 100)
        job_manager.update_progress(job_id, pct, f"{i+1}/{len(target_files)} archivos")

    return {"files_updated": resultados}
