"""
Framework de Evaluación Automatizada para Dataset de Fine-Tuning v10
====================================================================
Evalúa cada conversación del dataset en 15 dimensiones y produce un reporte
cuantitativo con scores objetivos.

Uso:
    python evaluar_conversacion.py                          # Evalúa todo el dataset
    python evaluar_conversacion.py --conv 0                 # Evalúa solo conversación 0
    python evaluar_conversacion.py --sample 100             # Muestra aleatoria de 100
    python evaluar_conversacion.py --output reporte.jsonl   # Guarda resultados
    python evaluar_conversacion.py --summary                # Solo muestra resumen
"""

import json
import re
import sys
import argparse
import random
from collections import Counter, defaultdict
from pathlib import Path

FILE = Path(__file__).parent / "merged_v10_together_train.jsonl"

# ─── Herramientas válidas (schema unificado v2) ───────────────────────────────

VALID_TOOLS = {
    "buscar_vehiculos": {
        "marca", "modelo", "anio_min", "anio_max", "precio_min", "precio_max",
        "tipo", "transmision", "ubicacion", "limit", "combustible",
        "kilometraje_max", "garantia"
    },
    "obtener_vehiculo": {"id", "slug"},
    "buscar_alternativas": {
        "marca_original", "modelo_original", "presupuesto", "tipo_uso",
        "carroceria", "ubicacion", "vehiculo_id", "precio_max", "limit"
    },
    "comparar_vehiculos": {"vehiculo_ids"},
    "calcular_financiamiento": {
        "vehiculo_id", "precio_vehiculo", "enganche_porcentaje", "plazo_meses",
        "tasa_anual", "enganche", "plazo"
    },
    "solicitar_datos_contacto": {"nombre", "telefono", "email", "vehiculo_interes"},
    "enviar_cotizacion_email": {"email", "vehiculo_id", "incluir_financiamiento"},
    "obtener_info_negocio": {"tema"},
    "estadisticas_inventario": {"agrupacion"},
    "buscar_informacion": {"consulta"},
    "obtener_faqs": {"categoria"},
}

FALSE_POSITIVE_NAMES = {
    "col", "acabo", "mex", "mxn", "encontré", "perfecto", "excelente",
    "claro", "listo", "genial", "super", "hola", "buenas", "buenos",
    "entiendo", "ahora", "vamos", "gracias", "así", "bien", "oye",
    "dale", "sale", "vale", "pues", "mira", "soy", "mariana", "autos",
    "trefa", "whatsapp", "monterrey", "guadalupe", "saltillo", "reynosa",
    "gonzalitos", "morelos", "centro", "petrolera", "para", "con",
    "aquí", "aqui", "tenemos", "mitras",
}

CAR_BRANDS = {
    "volkswagen", "toyota", "honda", "nissan", "kia", "mazda", "ford",
    "chevrolet", "hyundai", "suzuki", "seat", "renault", "jeep", "dodge",
    "bmw", "mercedes", "audi", "subaru", "mitsubishi", "peugeot", "fiat",
    "buick", "cadillac", "lincoln", "chrysler", "mg",
}


def extract_tool_calls_from_markup(content: str):
    """Extrae tool calls del formato <tool_call> markup."""
    results = []
    matches = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.DOTALL)
    for raw in matches:
        raw = raw.strip()
        try:
            data = json.loads(raw)
            results.append(data)
        except json.JSONDecodeError:
            results.append({"_parse_error": True, "_raw": raw[:200]})
    return results


def extract_tool_response(content: str):
    """Extrae JSON de <tool_response> wrapper."""
    match = re.search(r"<tool_response>\s*(.*?)\s*</tool_response>", content, re.DOTALL)
    if match:
        raw = match.group(1).strip()
        try:
            return json.loads(raw), True  # parsed, has_wrapper
        except json.JSONDecodeError:
            return None, True
    # Sin wrapper
    try:
        return json.loads(content), False
    except (json.JSONDecodeError, TypeError):
        return None, False


def get_user_names_up_to(msgs, up_to_idx):
    """Extrae nombres que el usuario ha proporcionado hasta cierto índice."""
    names = set()
    for msg in msgs[:up_to_idx]:
        if msg.get("role") != "user":
            continue
        content = str(msg.get("content", "") or "").lower()
        patterns = [
            r"(?:me llamo|soy|mi nombre es)\s+([a-záéíóúñ]{3,})",
            r"^([a-záéíóúñ]{3,})[\s.,!]*$",
        ]
        for pat in patterns:
            for m in re.finditer(pat, content):
                name = m.group(1)
                if name not in FALSE_POSITIVE_NAMES and name not in CAR_BRANDS:
                    names.add(name)
    return names


def evaluate_conversation(ci: int, conv: dict) -> dict:
    """Evalúa una conversación en las 15 dimensiones."""
    msgs = conv.get("messages", [])
    tools_field = conv.get("tools", [])
    issues = []
    scores = {}

    # ─── D01: Formato tool_call ────────────────────────────────────────
    has_proper_tool_calls = False
    has_markup_tool_calls = False
    markup_count = 0

    for msg in msgs:
        if msg.get("tool_calls"):
            has_proper_tool_calls = True
        content = str(msg.get("content", "") or "")
        if "<tool_call>" in content:
            has_markup_tool_calls = True
            markup_count += content.count("<tool_call>")

    if has_markup_tool_calls and not has_proper_tool_calls:
        scores["D01"] = 0
        issues.append(f"D01: FAIL — {markup_count} tool calls usan markup <tool_call> en vez de campo tool_calls")
    elif has_proper_tool_calls:
        # Verificar estructura de cada tool_call
        tc_issues = 0
        tc_total = 0
        for msg in msgs:
            for tc in msg.get("tool_calls", []):
                tc_total += 1
                if not tc.get("id"):
                    tc_issues += 1
                if tc.get("type") != "function":
                    tc_issues += 1
                func = tc.get("function", {})
                if not func.get("name"):
                    tc_issues += 1
                args = func.get("arguments", "")
                if isinstance(args, str):
                    try:
                        json.loads(args)
                    except json.JSONDecodeError:
                        tc_issues += 1
        scores["D01"] = 15 if tc_issues == 0 else max(0, 15 - tc_issues * 3)
        if tc_issues:
            issues.append(f"D01: PARTIAL — {tc_issues} problemas en {tc_total} tool_calls")
    else:
        scores["D01"] = 15  # No tool calls = N/A = pass
        if not any("<tool_call>" in str(m.get("content", "")) for m in msgs):
            pass  # Genuinamente sin tools

    # ─── D02: Campo tools ──────────────────────────────────────────────
    if tools_field and len(tools_field) > 0:
        valid_tools = all(
            isinstance(t, dict) and t.get("type") == "function" and "function" in t
            for t in tools_field
        )
        scores["D02"] = 10 if valid_tools else 5
        if not valid_tools:
            issues.append("D02: PARTIAL — Campo tools presente pero con formato incorrecto")
    else:
        # Verificar si hay tools en system prompt
        sp = msgs[0].get("content", "") if msgs else ""
        if "<tools>" in sp:
            scores["D02"] = 0
            issues.append("D02: FAIL — Tools definidas en system prompt XML, no en campo tools")
        else:
            scores["D02"] = 10  # No tools needed

    # ─── D03: Cadena tool_call_id ──────────────────────────────────────
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    if tool_msgs:
        missing_ids = sum(1 for m in tool_msgs if not m.get("tool_call_id"))
        if missing_ids == len(tool_msgs):
            scores["D03"] = 0
            issues.append(f"D03: FAIL — {missing_ids}/{len(tool_msgs)} tool messages sin tool_call_id")
        elif missing_ids > 0:
            scores["D03"] = max(0, 10 - missing_ids * 2)
            issues.append(f"D03: PARTIAL — {missing_ids}/{len(tool_msgs)} sin tool_call_id")
        else:
            scores["D03"] = 10
    else:
        scores["D03"] = 10  # N/A

    # ─── D04: Formato tool response ────────────────────────────────────
    if tool_msgs:
        wrapper_count = 0
        parse_errors = 0
        for m in tool_msgs:
            content = str(m.get("content", "") or "")
            if "<tool_response>" in content:
                wrapper_count += 1
            _, has_wrapper = extract_tool_response(content)
            if has_wrapper:
                wrapper_count = max(wrapper_count, 1)  # count once
        if wrapper_count > 0:
            scores["D04"] = 0
            issues.append(f"D04: FAIL — {wrapper_count} tool messages con <tool_response> wrapper")
        else:
            scores["D04"] = 5
    else:
        scores["D04"] = 5

    # ─── D05: Saludo inicial ───────────────────────────────────────────
    first_asst = None
    for msg in msgs:
        if msg.get("role") == "assistant":
            first_asst = msg
            break

    if first_asst:
        content = str(first_asst.get("content", "") or "")
        tc_in_content = "<tool_call>" in content

        has_greeting = any(g in content.lower() for g in ["hola", "¡hola", "buen"])
        has_mariana = any(m in content.lower() for m in ["soy mariana", "me llamo mariana", "mariana de autos"])
        has_emoji = bool(re.search(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF]", content))
        has_name_ask = any(n in content.lower() for n in ["tu nombre", "cómo te llamas", "me compartes"])

        if tc_in_content and not has_greeting:
            scores["D05"] = 0
            issues.append("D05: FAIL — Primer mensaje es tool_call sin saludo")
        else:
            s = 0
            if has_greeting:
                s += 3
            else:
                issues.append("D05: Sin saludo (hola/buenos)")
            if has_mariana:
                s += 3
            else:
                issues.append("D05: Sin 'Soy Mariana'")
            if has_emoji:
                s += 2
            if has_name_ask:
                s += 2
            scores["D05"] = s

        # Check for multiple greetings
        greeting_count = sum(
            1 for m in msgs
            if m.get("role") == "assistant"
            and any(g in str(m.get("content", "")).lower() for g in ["soy mariana", "me llamo mariana"])
        )
        if greeting_count > 1:
            scores["D05"] = max(0, scores["D05"] - 3)
            issues.append(f"D05: Saludo repetido — 'Soy Mariana' aparece {greeting_count} veces")
    else:
        scores["D05"] = 0
        issues.append("D05: FAIL — No hay mensaje assistant")

    # ─── D06: Discovery antes de tool ──────────────────────────────────
    first_user_intent = False
    first_tool_call_idx = None

    for mi, msg in enumerate(msgs):
        role = msg.get("role", "")
        content = str(msg.get("content", "") or "").lower()

        if role == "user":
            intent_words = [
                "busco", "quiero", "necesito", "me interesa", "pickup",
                "camioneta", "sedan", "suv", "auto", "carro", "presupuesto",
                "financiamiento", "garantía", "garantia", "horario",
                "ubicación", "ubicacion", "devolución", "devolucion",
            ]
            if any(w in content for w in intent_words):
                first_user_intent = True

        if role == "assistant" and "<tool_call>" in content and first_tool_call_idx is None:
            first_tool_call_idx = mi
        if role == "assistant" and msg.get("tool_calls") and first_tool_call_idx is None:
            first_tool_call_idx = mi

    if first_tool_call_idx is not None:
        if first_user_intent:
            scores["D06"] = 8
        else:
            # Check if there was a greeting/discovery message before
            pre_asst = [m for m in msgs[:first_tool_call_idx] if m.get("role") == "assistant"]
            if pre_asst:
                scores["D06"] = 6
            else:
                scores["D06"] = 0
                issues.append("D06: FAIL — Tool call sin discovery ni saludo previo")
    else:
        scores["D06"] = 8  # N/A

    # ─── D07: Argumentos tool correctos ────────────────────────────────
    all_tool_calls = []
    for msg in msgs:
        content = str(msg.get("content", "") or "")
        all_tool_calls.extend(extract_tool_calls_from_markup(content))
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
                all_tool_calls.append({"name": func.get("name", ""), "arguments": parsed})
            except json.JSONDecodeError:
                pass

    if all_tool_calls:
        total_args = 0
        invalid_args = 0
        arg_issues_detail = []

        for tc in all_tool_calls:
            if tc.get("_parse_error"):
                invalid_args += 1
                continue
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            if not isinstance(args, dict):
                continue

            valid_keys = VALID_TOOLS.get(name, set())
            for k in args.keys():
                total_args += 1
                if valid_keys and k not in valid_keys:
                    invalid_args += 1
                    arg_issues_detail.append(f"{name}.{k}")

        if total_args == 0:
            scores["D07"] = 10
        else:
            pct_valid = (total_args - invalid_args) / total_args
            scores["D07"] = round(10 * pct_valid)
            if invalid_args:
                issues.append(f"D07: {invalid_args}/{total_args} args inválidos: {', '.join(arg_issues_detail[:5])}")
    else:
        scores["D07"] = 10  # N/A

    # ─── D08: Datos no inventados ──────────────────────────────────────
    financing_without_tool = 0
    for mi, msg in enumerate(msgs):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content", "") or "").lower()

        # Check for financing data
        has_financing = (
            re.search(r"mensualidad.*\$[\d,]+", content)
            or re.search(r"enganche.*\$[\d,]+", content)
            or re.search(r"\$[\d,]+.*mensual", content)
        )
        if has_financing:
            preceded_by_tool = False
            for pmi in range(mi - 1, max(0, mi - 5), -1):
                pm = msgs[pmi]
                pm_content = str(pm.get("content", "") or "")
                if "calcular_financiamiento" in pm_content:
                    preceded_by_tool = True
                    break
                if pm.get("role") == "tool" and any(
                    k in pm_content for k in ["mensualidad", "financiamiento", "pago"]
                ):
                    preceded_by_tool = True
                    break
            if not preceded_by_tool:
                financing_without_tool += 1

    if financing_without_tool:
        scores["D08"] = max(0, 10 - financing_without_tool * 3)
        issues.append(f"D08: {financing_without_tool} menciones de financiamiento sin tool call previo")
    else:
        scores["D08"] = 10

    # ─── D09: URLs completas ───────────────────────────────────────────
    missing_urls = 0
    for msg in msgs:
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content", "") or "")
        promises_url = any(
            p in content.lower()
            for p in ["puedes ver", "aquí puedes", "fotos aquí", "detalles aquí", "todas sus fotos"]
        )
        if promises_url:
            has_url = bool(re.search(r"https?://[^\s\"')+]+", content))
            if not has_url:
                missing_urls += 1

    if missing_urls:
        scores["D09"] = 0
        issues.append(f"D09: FAIL — {missing_urls} URLs prometidas pero faltantes")
    else:
        scores["D09"] = 5

    # ─── D10: Sin alucinación de nombres ───────────────────────────────
    name_hallucinated = False
    for mi, msg in enumerate(msgs):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content", "") or "")
        user_names = get_user_names_up_to(msgs, mi)

        name_patterns = [
            r"(?:Hola|Perfecto|Claro|Mucho gusto)[,!]?\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})",
            r",\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})\s*[!?.:]",
        ]
        for pat in name_patterns:
            for m in re.finditer(pat, content):
                name = m.group(1)
                if (
                    name.lower() not in FALSE_POSITIVE_NAMES
                    and name.lower() not in CAR_BRANDS
                    and name.lower() not in user_names
                ):
                    name_hallucinated = True
                    issues.append(f"D10: Usa nombre '{name}' en msg {mi} sin que el usuario lo diera")
                    break
            if name_hallucinated:
                break
        if name_hallucinated:
            break

    scores["D10"] = 0 if name_hallucinated else 5

    # ─── D11: Tono y naturalidad ───────────────────────────────────────
    robotic = False
    robotic_patterns = [
        r"se encontraron los siguientes",
        r"basado en tu solicitud",
        r"los resultados de la búsqueda",
        r"la búsqueda arrojó",
        r"como asistente",
        r"como inteligencia artificial",
        r"como modelo de lenguaje",
        r"no tengo la capacidad",
    ]
    wrong_name = False

    for msg in msgs:
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content", "") or "").lower()
        for pat in robotic_patterns:
            if re.search(pat, content):
                robotic = True
                issues.append(f"D11: Respuesta robótica — '{pat}'")
                break
        if re.search(r"\b(lote|tienda|concesionario|dealer)\b", content):
            wrong_name = True
            issues.append("D11: Usa nombre incorrecto para TREFA (lote/tienda)")

    score_11 = 5
    if robotic:
        score_11 -= 3
    if wrong_name:
        score_11 -= 2
    scores["D11"] = max(0, score_11)

    # ─── D12: CTA y cierre ────────────────────────────────────────────
    last_asst = None
    for msg in reversed(msgs):
        if msg.get("role") == "assistant":
            last_asst = msg
            break

    if last_asst:
        content = str(last_asst.get("content", "") or "")
        has_question = "?" in content
        has_cta = any(
            c in content.lower()
            for c in ["agenda", "avísame", "avisame", "dime", "cuéntame", "cuentame", "escríbe", "escribeme"]
        )
        scores["D12"] = 3 if (has_question or has_cta) else 0
        if not has_question and not has_cta:
            issues.append("D12: Último mensaje sin pregunta ni CTA")
    else:
        scores["D12"] = 0

    # ─── D13: System prompt limpio ─────────────────────────────────────
    sp = msgs[0].get("content", "") if msgs else ""
    has_tools_xml = "<tools>" in sp
    has_template = "<function-name>" in sp or "<args-json-object>" in sp

    if has_tools_xml or has_template:
        scores["D13"] = 0
        if has_tools_xml:
            issues.append("D13: System prompt contiene <tools> XML")
        if has_template:
            issues.append("D13: System prompt contiene template placeholder")
    else:
        scores["D13"] = 5

    # ─── D14: Secuencia de roles ───────────────────────────────────────
    violations = 0
    prev_role = None
    for mi, msg in enumerate(msgs):
        role = msg.get("role")
        if role == prev_role and role not in ("tool",):
            violations += 1
            issues.append(f"D14: Roles consecutivos {role} en msgs {mi-1},{mi}")
        prev_role = role

    scores["D14"] = max(0, 4 - violations * 2)

    # ─── D15: Sin leaks de agente ──────────────────────────────────────
    leaks = 0
    for msg in msgs:
        content = str(msg.get("content", "") or "")
        leak_patterns = [
            "Agent stopped",
            "max iterations",
            "texto_original",
            "Let me think",
            "I need to",
            "I should",
        ]
        for lp in leak_patterns:
            if lp in content:
                leaks += 1
                issues.append(f"D15: Leak encontrado — '{lp}'")
                break

    scores["D15"] = max(0, 5 - leaks * 2)

    # ─── Calcular total ───────────────────────────────────────────────
    total = sum(scores.values())
    max_score = 100

    # Determinar si aprueba
    bloqueantes_ok = scores.get("D01", 0) >= 12 and scores.get("D02", 0) >= 8 and scores.get("D03", 0) >= 8
    aprobada = total >= 75 and bloqueantes_ok

    return {
        "conv_id": ci,
        "total_mensajes": len(msgs),
        "scores": scores,
        "total": total,
        "max_score": max_score,
        "aprobada": aprobada,
        "issues": issues,
        "recomendacion": "aprobar" if aprobada else ("corregir" if total >= 40 else "descartar"),
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluador de dataset de fine-tuning v10")
    parser.add_argument("--conv", type=int, help="Evaluar solo conversación N")
    parser.add_argument("--sample", type=int, help="Muestra aleatoria de N conversaciones")
    parser.add_argument("--output", type=str, help="Archivo de salida JSONL")
    parser.add_argument("--summary", action="store_true", help="Solo mostrar resumen")
    parser.add_argument("--file", type=str, default=str(FILE), help="Archivo del dataset")
    args = parser.parse_args()

    with open(args.file) as f:
        conversations = [json.loads(line) for line in f if line.strip()]

    print(f"Dataset cargado: {len(conversations)} conversaciones")

    # Seleccionar conversaciones a evaluar
    if args.conv is not None:
        indices = [args.conv]
    elif args.sample:
        indices = sorted(random.sample(range(len(conversations)), min(args.sample, len(conversations))))
    else:
        indices = range(len(conversations))

    # Evaluar
    results = []
    score_dist = Counter()
    dimension_scores = defaultdict(list)
    issue_counts = Counter()

    for ci in indices:
        if ci >= len(conversations):
            continue
        result = evaluate_conversation(ci, conversations[ci])
        results.append(result)

        score_dist[result["recomendacion"]] += 1
        for dim, score in result["scores"].items():
            dimension_scores[dim].append(score)
        for issue in result["issues"]:
            dim = issue.split(":")[0].strip()
            issue_counts[dim] += 1

    # Guardar resultados
    if args.output:
        with open(args.output, "w") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"Resultados guardados en {args.output}")

    # Mostrar resumen
    print(f"\n{'='*70}")
    print(f"RESUMEN DE EVALUACIÓN — {len(results)} conversaciones evaluadas")
    print(f"{'='*70}")

    print(f"\n## Recomendación")
    for rec, count in score_dist.most_common():
        pct = 100 * count / len(results)
        print(f"  {rec}: {count} ({pct:.1f}%)")

    print(f"\n## Score por Dimensión (promedio)")
    max_scores = {
        "D01": 15, "D02": 10, "D03": 10, "D04": 5, "D05": 10,
        "D06": 8, "D07": 10, "D08": 10, "D09": 5, "D10": 5,
        "D11": 5, "D12": 3, "D13": 5, "D14": 4, "D15": 5,
    }
    dim_names = {
        "D01": "Formato tool_call",
        "D02": "Campo tools",
        "D03": "Cadena tool_call_id",
        "D04": "Format tool response",
        "D05": "Saludo inicial",
        "D06": "Discovery antes de tool",
        "D07": "Args tool correctos",
        "D08": "Datos no inventados",
        "D09": "URLs completas",
        "D10": "Sin alucinación nombres",
        "D11": "Tono natural",
        "D12": "CTA y cierre",
        "D13": "System prompt limpio",
        "D14": "Secuencia roles",
        "D15": "Sin leaks agente",
    }

    for dim in sorted(dimension_scores.keys()):
        values = dimension_scores[dim]
        avg = sum(values) / len(values) if values else 0
        mx = max_scores.get(dim, 10)
        pct = 100 * avg / mx if mx > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        status = "✓" if pct >= 80 else ("!" if pct >= 50 else "✗")
        name = dim_names.get(dim, dim)
        print(f"  {status} {dim} {name:30s} {avg:5.1f}/{mx:2d} ({pct:5.1f}%) {bar}")

    print(f"\n## Problemas Más Frecuentes")
    for dim, count in issue_counts.most_common(15):
        pct = 100 * count / len(results)
        print(f"  {dim}: {count} conversaciones ({pct:.1f}%)")

    total_avg = sum(r["total"] for r in results) / len(results) if results else 0
    print(f"\n## Score Total Promedio: {total_avg:.1f}/100")

    aprobadas = sum(1 for r in results if r["aprobada"])
    print(f"## Aprobadas: {aprobadas}/{len(results)} ({100*aprobadas/len(results) if results else 0:.1f}%)")

    # Mostrar detalle si es una sola conversación
    if args.conv is not None and results:
        r = results[0]
        print(f"\n{'='*70}")
        print(f"DETALLE — Conversación {r['conv_id']} ({r['total_mensajes']} mensajes)")
        print(f"{'='*70}")
        print(f"Score: {r['total']}/{r['max_score']}")
        print(f"Recomendación: {r['recomendacion']}")
        print(f"\nScores por dimensión:")
        for dim, score in sorted(r["scores"].items()):
            mx = max_scores.get(dim, 10)
            name = dim_names.get(dim, dim)
            status = "✓" if score >= mx * 0.8 else ("!" if score >= mx * 0.5 else "✗")
            print(f"  {status} {dim} {name}: {score}/{mx}")
        print(f"\nProblemas encontrados:")
        for issue in r["issues"]:
            print(f"  - {issue}")


if __name__ == "__main__":
    main()
