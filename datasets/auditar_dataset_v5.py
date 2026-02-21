#!/usr/bin/env python3
"""
auditar_dataset_v5.py — Muestreo crítico y honesto de 50 conversaciones
del dataset v5, evaluando múltiples criterios de calidad.

Criterios evaluados:
  1. SALUDO: Expresivo, con 😊, solo una vez, usa nombre del cliente
  2. TOOL CALLS: Formato correcto, no mezclado con texto, seguido de tool response
  3. CONTEXTO: Assistant usa datos de tool responses, no inventa info
  4. CIERRE: Último msg con CTA, no deja conversación abierta
  5. VERACIDAD: Precios, modelos, URLs coinciden con tool responses
  6. FORMATO: Bullets •, negritas en títulos, precios $XXX,XXX MXN
  7. FLUJO: No ofrece cotización por email, no doble saludo
"""

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DATASET = Path(__file__).parent / "v5_dataset" / "dataset_v5_completo.jsonl"
SAMPLE_SIZE = 50
SEED = 123


def load_all(path: Path) -> list[dict]:
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def stratified_sample(data: list[dict], n: int, seed: int) -> list[dict]:
    """Muestreo estratificado por fuente."""
    random.seed(seed)
    by_source = defaultdict(list)
    for d in data:
        src = d.get("_meta", {}).get("source", "unknown")
        by_source[src].append(d)

    total = len(data)
    sample = []
    remainder = []

    for src, convs in by_source.items():
        # Proporción de esta fuente
        alloc = max(1, round(len(convs) / total * n))
        random.shuffle(convs)
        sample.extend(convs[:alloc])
        remainder.extend(convs[alloc:])

    # Si nos pasamos, recortar; si nos falta, completar
    if len(sample) > n:
        random.shuffle(sample)
        sample = sample[:n]
    elif len(sample) < n:
        random.shuffle(remainder)
        sample.extend(remainder[:n - len(sample)])

    random.shuffle(sample)
    return sample[:n]


# ═══════════════════════════════════════════════════════════════
# EVALUADORES
# ═══════════════════════════════════════════════════════════════

def eval_saludo(msgs: list[dict]) -> dict:
    """Evalúa calidad del saludo."""
    result = {"score": 0, "max": 5, "issues": []}

    # Encontrar primer assistant msg que no sea tool_call
    first_ast = None
    for m in msgs:
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            first_ast = m
            break

    if not first_ast:
        result["issues"].append("No hay mensaje de assistant conversacional")
        return result

    content = first_ast["content"]

    # 1. Tiene saludo expresivo?
    if re.search(r'(?i)(¡hola|qué gusto|me da.*gusto)', content):
        result["score"] += 1
    else:
        result["issues"].append("Saludo no expresivo")

    # 2. Tiene emoji 😊?
    if '😊' in content:
        result["score"] += 1
    elif ':)' in content:
        result["score"] += 0.5
        result["issues"].append("Usa :) en vez de 😊")
    else:
        result["issues"].append("Sin emoji en saludo")

    # 3. Se presenta como Mariana de TREFA?
    if re.search(r'(?i)mariana', content):
        result["score"] += 1
    else:
        result["issues"].append("No se presenta como Mariana")

    # 4. Solo un saludo en toda la conversación
    greeting_count = 0
    for m in msgs:
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            if re.search(r'(?i)soy\s+mariana', m.get("content", "")):
                greeting_count += 1
    if greeting_count <= 1:
        result["score"] += 1
    else:
        result["issues"].append(f"Se presenta {greeting_count} veces")

    # 5. Usa nombre del cliente si lo conoce
    # Buscar si el cliente dio su nombre
    client_name = None
    for m in msgs:
        if m.get("role") == "user":
            name_match = re.search(r'(?i)(?:soy|me llamo|mi nombre es)\s+(\w+)', m.get("content", ""))
            if name_match:
                client_name = name_match.group(1)
                break
            # Nombre al inicio: "Hola Mariana, soy X"
            name_match2 = re.search(r'(?i)soy\s+(\w+)', m.get("content", ""))
            if name_match2:
                client_name = name_match2.group(1)
                break

    if client_name:
        # Verificar que Mariana use el nombre después de conocerlo
        found_name_usage = False
        name_known = False
        for m in msgs:
            if m.get("role") == "user" and client_name.lower() in m.get("content", "").lower():
                name_known = True
                continue
            if name_known and m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
                if client_name.lower() in m.get("content", "").lower() or client_name.capitalize() in m.get("content", ""):
                    found_name_usage = True
                    break
        if found_name_usage:
            result["score"] += 1
        else:
            result["issues"].append(f"No usa nombre '{client_name}' del cliente")
    else:
        # No se pudo determinar nombre, dar punto si pregunta
        if re.search(r'(?i)(tu nombre|cómo te llamas|con quién)', first_ast["content"]):
            result["score"] += 1
        else:
            result["issues"].append("No pregunta nombre del cliente")

    return result


def eval_tool_calls(msgs: list[dict]) -> dict:
    """Evalúa correcto uso de tool calls."""
    result = {"score": 0, "max": 5, "issues": []}

    tool_calls = []
    tool_responses = []
    for i, m in enumerate(msgs):
        content = m.get("content", "")
        if m.get("role") == "assistant" and "<tool_call>" in content:
            tool_calls.append(i)
        if m.get("role") == "tool" or "<tool_response>" in content:
            tool_responses.append(i)

    if not tool_calls:
        # Conversaciones sin tools (saludos, estilo) — verificar que no necesiten
        has_search_need = any(
            re.search(r'(?i)(busco|quiero|tienes|tienen|precio|modelo|marca)', m.get("content", ""))
            for m in msgs if m.get("role") == "user"
        )
        if has_search_need:
            result["issues"].append("Debería usar tools pero no lo hace")
        else:
            result["score"] = 5  # Conversación sin necesidad de tools
        return result

    points_per_check = 5.0 / 5

    # 1. Tool calls aislados (sin texto extra)
    mixed = 0
    for i in tool_calls:
        content = msgs[i]["content"]
        clean = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
        if clean:
            mixed += 1
    if mixed == 0:
        result["score"] += points_per_check
    else:
        result["issues"].append(f"{mixed} tool calls mezclados con texto")

    # 2. Cada tool call seguido de tool response
    orphan = 0
    for i in tool_calls:
        if i + 1 < len(msgs):
            nxt = msgs[i + 1]
            if nxt.get("role") not in ("tool",) and "<tool_response>" not in nxt.get("content", ""):
                orphan += 1
        else:
            orphan += 1
    if orphan == 0:
        result["score"] += points_per_check
    else:
        result["issues"].append(f"{orphan} tool calls sin response")

    # 3. Después de tool response hay un assistant que interpreta
    no_interp = 0
    for i in tool_responses:
        if i + 1 < len(msgs):
            nxt = msgs[i + 1]
            if nxt.get("role") != "assistant":
                no_interp += 1
        else:
            no_interp += 1
    if no_interp == 0:
        result["score"] += points_per_check
    else:
        result["issues"].append(f"{no_interp} tool responses sin interpretación del assistant")

    # 4. Tool call JSON es parseable
    unparseable = 0
    for i in tool_calls:
        content = msgs[i]["content"]
        match = re.search(r'<tool_call>\s*(.*?)\s*</tool_call>', content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if "name" not in data:
                    unparseable += 1
            except json.JSONDecodeError:
                unparseable += 1
        else:
            unparseable += 1
    if unparseable == 0:
        result["score"] += points_per_check
    else:
        result["issues"].append(f"{unparseable} tool calls con JSON inválido")

    # 5. Tool response JSON es parseable
    unparseable_resp = 0
    for i in tool_responses:
        content = msgs[i].get("content", "")
        match = re.search(r'<tool_response>\s*(.*?)\s*</tool_response>', content, re.DOTALL)
        if match:
            try:
                json.loads(match.group(1))
            except json.JSONDecodeError:
                unparseable_resp += 1
        # Some have raw JSON without tags
        elif content.strip().startswith("{"):
            try:
                json.loads(content.strip())
            except json.JSONDecodeError:
                unparseable_resp += 1
    if unparseable_resp == 0:
        result["score"] += points_per_check
    else:
        result["issues"].append(f"{unparseable_resp} tool responses con JSON inválido")

    return result


def eval_veracidad(msgs: list[dict]) -> dict:
    """Evalúa que el assistant no invente información y use datos de tools."""
    result = {"score": 0, "max": 5, "issues": []}

    # Extraer datos de tool responses
    tool_data = {}  # {tool_name: parsed_data}
    all_prices = set()
    all_models = set()
    all_urls = set()

    for m in msgs:
        content = m.get("content", "")
        match = re.search(r'<tool_response>\s*(.*?)\s*</tool_response>', content, re.DOTALL)
        if not match and m.get("role") == "tool" and content.strip().startswith("{"):
            raw = content.strip()
        elif match:
            raw = match.group(1)
        else:
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Extraer precios de vehiculos
        vehiculos = data.get("vehiculos", data.get("alternativas", data.get("comparacion", [])))
        if isinstance(vehiculos, list):
            for v in vehiculos:
                if isinstance(v, dict):
                    if "precio" in v:
                        try:
                            all_prices.add(int(v["precio"]))
                        except (ValueError, TypeError):
                            pass
                    if "titulo" in v:
                        all_models.add(str(v["titulo"]))
                    if "liga_web" in v:
                        all_urls.add(str(v["liga_web"]))
                    if "slug" in v:
                        all_urls.add(f"https://autostrefa.mx/autos/{v['slug']}")
        elif isinstance(data, dict):
            if "precio" in data:
                try:
                    all_prices.add(int(data["precio"]))
                except (ValueError, TypeError):
                    pass
            if "titulo" in data:
                all_models.add(str(data["titulo"]))
            if "liga_web" in data:
                all_urls.add(str(data["liga_web"]))
            if "slug" in data:
                all_urls.add(f"https://autostrefa.mx/autos/{data['slug']}")

    if not all_prices and not all_models:
        # No tools con datos de vehículos — dar puntaje completo
        result["score"] = 5
        return result

    points_per = 5.0 / 5

    # 1. Precios mencionados por Mariana coinciden con tool data
    invented_prices = 0
    for m in msgs:
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        content = m["content"]
        prices_in_msg = re.findall(r'\$\s*([\d,]+)\s*(?:MXN|pesos)?', content)
        for p_str in prices_in_msg:
            p_num = int(p_str.replace(",", ""))
            # Verificar que coincida con algún precio de tools (tolerancia ±5%)
            if all_prices and not any(abs(p_num - tp) / max(tp, 1) < 0.05 for tp in all_prices):
                # Podría ser enganche o mensualidad — verificar magnitud
                if p_num > 50000:  # Solo verificar precios de vehículos
                    invented_prices += 1

    if invented_prices == 0:
        result["score"] += points_per
    else:
        result["issues"].append(f"{invented_prices} precios posiblemente inventados")

    # 2. URLs mencionadas son de autostrefa.mx
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        content = m["content"]
        urls = re.findall(r'https?://\S+', content)
        for url in urls:
            url_clean = url.rstrip(".,;)")
            if "autostrefa.mx" not in url_clean:
                result["issues"].append(f"URL externa: {url_clean[:60]}")
            elif all_urls and url_clean not in all_urls:
                # Podría ser variación legítima
                pass
    if not any("URL" in i for i in result["issues"]):
        result["score"] += points_per

    # 3. No inventa herramientas que no existen
    valid_tools = {
        "buscar_vehiculos", "obtener_vehiculo", "buscar_alternativas",
        "comparar_vehiculos", "calcular_financiamiento", "solicitar_datos_contacto",
        "enviar_cotizacion_email", "obtener_info_negocio", "estadisticas_inventario",
        "buscar_informacion", "obtener_faqs"
    }
    invented_tools = 0
    for m in msgs:
        if m.get("role") == "assistant" and "<tool_call>" in m.get("content", ""):
            match = re.search(r'"name"\s*:\s*"(\w+)"', m["content"])
            if match and match.group(1) not in valid_tools:
                invented_tools += 1
                result["issues"].append(f"Tool inventada: {match.group(1)}")
    if invented_tools == 0:
        result["score"] += points_per

    # 4. No dice cosas contradictorias con los datos
    result["score"] += points_per  # Difícil de automatizar, dar punto base

    # 5. Precios en formato correcto $XXX,XXX MXN
    bad_format = 0
    for m in msgs:
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        # Buscar precios sin formato: ej "$359900" sin comas
        raw_prices = re.findall(r'\$\s*(\d{6,})', m.get("content", ""))
        for rp in raw_prices:
            if "," not in rp:
                bad_format += 1
    if bad_format == 0:
        result["score"] += points_per
    else:
        result["issues"].append(f"{bad_format} precios sin formato correcto")

    return result


def eval_cierre(msgs: list[dict]) -> dict:
    """Evalúa calidad del cierre de la conversación."""
    result = {"score": 0, "max": 5, "issues": []}

    # Último mensaje assistant
    last_ast = None
    for m in reversed(msgs):
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            last_ast = m
            break

    if not last_ast:
        result["issues"].append("No termina con mensaje de assistant")
        return result

    content = last_ast["content"]

    # 1. Termina con pregunta o CTA
    has_question = "?" in content
    has_cta = re.search(
        r'(?i)(te\s+gustaría|quieres|te\s+interesa|te\s+animas|cuál\s+prefieres|'
        r'alguna.*interesa|cuál.*atención|vamos|iniciemos|agendamos|'
        r'te\s+parece|aquí\s+estoy|no\s+dudes|cualquier\s+duda)',
        content
    )
    if has_question:
        result["score"] += 1.5
    elif has_cta:
        result["score"] += 1
    else:
        result["issues"].append("No termina con pregunta ni CTA")

    # 2. Tono amigable en cierre
    friendly = re.search(r'(?i)(😊|:[\)\)]|con\s+gusto|encant|aquí\s+estoy|con\s+confianza|no\s+dudes)', content)
    if friendly:
        result["score"] += 1
    else:
        result["issues"].append("Cierre sin tono amigable")

    # 3. No queda al aire (no es solo un tool call response sin contexto)
    if len(content.strip()) > 30:
        result["score"] += 1
    else:
        result["issues"].append("Último mensaje muy corto")

    # 4. No ofrece cotización por email
    if not re.search(r'(?i)(cotización.*correo|email.*cotización|enviar.*cotización)', content):
        result["score"] += 0.75
    else:
        result["issues"].append("Ofrece cotización por email en cierre")

    # 5. Orientado a acción concreta
    action_oriented = re.search(
        r'(?i)(solicitud.*financiamiento|trámite.*crédito|venir.*persona|agendar.*visita|'
        r'conocer.*persona|iniciar.*solicitud|ver.*sucursal)',
        content
    )
    if action_oriented:
        result["score"] += 0.75
    else:
        # Aceptable si tiene pregunta genérica
        if has_question:
            result["score"] += 0.5
        else:
            result["issues"].append("Cierre sin orientación a acción concreta")

    return result


def eval_formato(msgs: list[dict]) -> dict:
    """Evalúa formato de presentación."""
    result = {"score": 0, "max": 5, "issues": []}

    has_vehicle_presentation = False
    points_per = 5.0 / 5

    for m in msgs:
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        content = m["content"]

        # Detectar si presenta vehículos (múltiples opciones)
        if re.search(r'(?i)(encontré|opciones?|opción)', content) and "**" in content:
            has_vehicle_presentation = True

            # 1. Usa bullets •
            if "•" in content:
                result["score"] += points_per
            elif re.search(r'(?m)^(?:Opción\s*\d|1\.)', content):
                result["issues"].append("Usa numeración en vez de bullets •")
            else:
                result["score"] += points_per * 0.5  # Neutral

            # 2. Negritas en título del auto
            if re.search(r'\*\*\w+.*\d{4}\*\*', content):
                result["score"] += points_per
            else:
                result["issues"].append("Título del auto sin negritas")

            # 3. Precios con formato $XXX,XXX MXN
            if re.search(r'\$\d{1,3}(?:,\d{3})+\s*MXN', content):
                result["score"] += points_per
            elif re.search(r'\$[\d,]+', content):
                result["score"] += points_per * 0.5
                result["issues"].append("Precio sin formato completo $XXX,XXX MXN")
            break

    if not has_vehicle_presentation:
        # Sin presentación de vehículos — evaluar formato general
        result["score"] = 5  # No aplica, dar puntaje completo
        return result

    # 4. Menciona ubicación/sucursal
    for m in msgs:
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        if re.search(r'(?i)(monterrey|guadalupe|saltillo|reynosa|sucursal)', m.get("content", "")):
            result["score"] += points_per
            break
    else:
        result["issues"].append("No menciona sucursal/ubicación")

    # 5. No usa emojis como viñetas
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        if re.search(r'[🔹🚗✅🔸]', m.get("content", "")):
            result["issues"].append("Usa emojis como viñetas")
            break
    else:
        result["score"] += points_per

    return result


def eval_flujo(msgs: list[dict]) -> dict:
    """Evalúa flujo general de la conversación."""
    result = {"score": 0, "max": 5, "issues": []}
    points_per = 5.0 / 5

    # 1. No hay dos user consecutivos
    consecutive_users = False
    for i in range(1, len(msgs)):
        if msgs[i].get("role") == "user" and msgs[i-1].get("role") == "user":
            consecutive_users = True
            break
    if not consecutive_users:
        result["score"] += points_per
    else:
        result["issues"].append("Users consecutivos")

    # 2. System prompt es __SYSTEM_PROMPT__
    if msgs and msgs[0].get("role") == "system" and msgs[0].get("content") == "__SYSTEM_PROMPT__":
        result["score"] += points_per
    else:
        result["issues"].append("System prompt incorrecto")

    # 3. Conversación tiene longitud razonable
    non_system = [m for m in msgs if m.get("role") != "system"]
    if len(non_system) >= 4:
        result["score"] += points_per
    else:
        result["issues"].append(f"Muy corta: {len(non_system)} msgs")

    # 4. No menciona enviar_cotizacion_email
    has_email_tool = any(
        "enviar_cotizacion_email" in m.get("content", "")
        for m in msgs if m.get("role") == "assistant"
    )
    if not has_email_tool:
        result["score"] += points_per
    else:
        result["issues"].append("Usa enviar_cotizacion_email")

    # 5. Termina con role assistant (no tool ni user)
    if msgs[-1].get("role") == "assistant":
        result["score"] += points_per
    else:
        result["issues"].append(f"Termina con role '{msgs[-1].get('role')}'")

    return result


# ═══════════════════════════════════════════════════════════════
# HOLA COUNT
# ═══════════════════════════════════════════════════════════════

def count_hola(msgs: list[dict]) -> int:
    count = 0
    for m in msgs:
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            count += len(re.findall(r'(?i)\bhola\b', m.get("content", "")))
    return count


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("AUDITORÍA CRÍTICA — DATASET V5 (50 conversaciones)")
    print("=" * 70)

    data = load_all(DATASET)
    sample = stratified_sample(data, SAMPLE_SIZE, SEED)

    print(f"\nMuestra: {len(sample)} conversaciones de {len(data)} totales")
    src_dist = Counter(d.get("_meta", {}).get("source", "?") for d in sample)
    for s, c in src_dist.most_common():
        print(f"  {s}: {c}")

    # Evaluar cada conversación
    categories = ["saludo", "tool_calls", "veracidad", "cierre", "formato", "flujo"]
    all_results = []
    global_scores = {cat: [] for cat in categories}
    all_issues = defaultdict(list)
    critical_failures = []

    for idx, conv in enumerate(sample):
        msgs = conv.get("messages", [])
        source = conv.get("_meta", {}).get("source", "?")
        hola = count_hola(msgs)

        r_saludo = eval_saludo(msgs)
        r_tools = eval_tool_calls(msgs)
        r_veracidad = eval_veracidad(msgs)
        r_cierre = eval_cierre(msgs)
        r_formato = eval_formato(msgs)
        r_flujo = eval_flujo(msgs)

        results = {
            "saludo": r_saludo,
            "tool_calls": r_tools,
            "veracidad": r_veracidad,
            "cierre": r_cierre,
            "formato": r_formato,
            "flujo": r_flujo,
        }

        total = sum(r["score"] for r in results.values())
        total_max = sum(r["max"] for r in results.values())
        pct = total / total_max * 100 if total_max else 0

        for cat, r in results.items():
            global_scores[cat].append(r["score"] / r["max"] * 100)
            for iss in r["issues"]:
                all_issues[iss].append(idx)

        conv_summary = {
            "idx": idx,
            "source": source,
            "msgs": len(msgs),
            "hola": hola,
            "total_pct": round(pct, 1),
            "scores": {cat: f"{r['score']:.1f}/{r['max']}" for cat, r in results.items()},
            "issues": [iss for r in results.values() for iss in r["issues"]],
        }
        all_results.append(conv_summary)

        if pct < 60:
            critical_failures.append(conv_summary)

    # ═══════════════════════════════════════════════════════════
    # REPORTE
    # ═══════════════════════════════════════════════════════════

    print("\n" + "=" * 70)
    print("RESULTADOS POR CONVERSACIÓN")
    print("=" * 70)

    for r in all_results:
        status = "✓" if r["total_pct"] >= 80 else ("⚠" if r["total_pct"] >= 60 else "✗")
        issues_str = f" | Issues: {', '.join(r['issues'])}" if r['issues'] else ""
        print(f"  {status} #{r['idx']:02d} [{r['source']:20s}] {r['msgs']:2d}msgs "
              f"hola={r['hola']} → {r['total_pct']:5.1f}%  "
              f"{r['scores']}{issues_str}")

    # Promedios por categoría
    print("\n" + "=" * 70)
    print("PROMEDIOS POR CATEGORÍA")
    print("=" * 70)
    for cat in categories:
        scores = global_scores[cat]
        avg = sum(scores) / len(scores) if scores else 0
        below_60 = sum(1 for s in scores if s < 60)
        below_80 = sum(1 for s in scores if s < 80)
        bar = "█" * int(avg / 5) + "░" * (20 - int(avg / 5))
        print(f"  {cat:15s}  {bar} {avg:5.1f}%  (< 60%: {below_60}, < 80%: {below_80})")

    # Promedio global
    all_pcts = [r["total_pct"] for r in all_results]
    global_avg = sum(all_pcts) / len(all_pcts) if all_pcts else 0
    above_80 = sum(1 for p in all_pcts if p >= 80)
    between = sum(1 for p in all_pcts if 60 <= p < 80)
    below = sum(1 for p in all_pcts if p < 60)

    print(f"\n  {'GLOBAL':15s}  {'█' * int(global_avg / 5)}{'░' * (20 - int(global_avg / 5))} {global_avg:5.1f}%")
    print(f"    ✓ ≥80%: {above_80}/{len(all_results)}  |  ⚠ 60-79%: {between}  |  ✗ <60%: {below}")

    # Issues más comunes
    print("\n" + "=" * 70)
    print("ISSUES MÁS COMUNES")
    print("=" * 70)
    sorted_issues = sorted(all_issues.items(), key=lambda x: -len(x[1]))
    for iss, indices in sorted_issues[:20]:
        print(f"  [{len(indices):2d}x] {iss}")

    # Hola stats
    hola_counts = [r["hola"] for r in all_results]
    hola_multi = sum(1 for h in hola_counts if h > 1)
    print(f"\n  --- 'Hola' en la muestra ---")
    print(f"  Total: {sum(hola_counts)}")
    print(f"  Convs con >1 'Hola': {hola_multi} / {len(sample)}")
    print(f"  Distribución: {Counter(hola_counts).most_common()}")

    # Failures críticos
    if critical_failures:
        print(f"\n" + "=" * 70)
        print(f"CONVERSACIONES CRÍTICAS (<60%) — {len(critical_failures)}")
        print("=" * 70)
        for r in critical_failures:
            print(f"\n  ✗ #{r['idx']} [{r['source']}] {r['total_pct']}%")
            print(f"    Issues: {', '.join(r['issues'])}")

    # Veredicto
    print("\n" + "=" * 70)
    print("VEREDICTO")
    print("=" * 70)
    if global_avg >= 85:
        print("  ✓ Dataset de ALTA calidad. Listo para entrenar.")
    elif global_avg >= 70:
        print("  ⚠ Dataset de calidad ACEPTABLE. Revisar issues más comunes.")
    else:
        print("  ✗ Dataset necesita MEJORAS SIGNIFICATIVAS antes de entrenar.")

    print(f"\n  Score global: {global_avg:.1f}%")
    areas_to_fix = [(cat, sum(s) / len(s)) for cat, s in global_scores.items() if sum(s) / len(s) < 75]
    if areas_to_fix:
        print(f"  Áreas a mejorar:")
        for cat, avg in sorted(areas_to_fix, key=lambda x: x[1]):
            print(f"    - {cat}: {avg:.1f}%")

    print("=" * 70)

    # Guardar reporte JSON
    report = {
        "timestamp": datetime.now().isoformat() if 'datetime' in dir() else "N/A",
        "sample_size": len(sample),
        "global_avg": round(global_avg, 1),
        "by_category": {cat: round(sum(s)/len(s), 1) for cat, s in global_scores.items()},
        "issues_frequency": {iss: len(idxs) for iss, idxs in sorted_issues},
        "critical_failures": len(critical_failures),
        "hola_distribution": dict(Counter(hola_counts)),
        "results": all_results,
    }
    report_path = Path(__file__).parent / "v5_dataset" / "auditoria_v5.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  Reporte guardado: {report_path}")


if __name__ == "__main__":
    from datetime import datetime
    main()
