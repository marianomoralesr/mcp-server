#!/usr/bin/env python3
"""
Auditoría de leakage de campos de herramientas en texto natural del asistente.
Detecta cuando el asistente vuelca nombres de campos técnicos (field_name: value)
en lugar de presentar la información de forma natural en conversación WhatsApp.

Dataset: merged_v10_together_train.jsonl (4,320 conversaciones)
"""

import json
import re
import sys
from collections import defaultdict, Counter
from pathlib import Path

# ─── Configuración ──────────────────────────────────────────────────────────

DATASET = Path(__file__).parent / "merged_v10_together_train.jsonl"

# Campos que NUNCA deben aparecer como "campo: valor" en texto del asistente
FORBIDDEN_FIELDS = [
    "url_imagen",
    "liga_web",
    "precio_lista",
    "precio_oferta",
    "traccion",
    "transmision",
    "combustible",
    "kilometraje",
    "record_id",
    "slug",
    "vehiculo_id",
    "año_modelo",
    "ano_modelo",       # variante sin ñ
    "tipo_carroceria",
    "equipamiento",
    "color_exterior",
    "color_interior",
    "numero_puertas",
    "capacidad_pasajeros",
]

# Campos que son "duros" — nunca deberían aparecer en texto natural bajo ningún contexto
HARD_FORBIDDEN = {
    "url_imagen", "liga_web", "record_id", "slug", "vehiculo_id",
    "ano_modelo", "tipo_carroceria", "numero_puertas", "capacidad_pasajeros",
}

# Campos "blandos" — el nombre puede aparecer de forma natural (ej. "Kilometraje: 45,000 km")
# pero se marca como violación si aparece como parte de un patrón de volcado (field: value)
SOFT_FIELDS = {
    "precio_lista", "precio_oferta", "traccion", "transmision",
    "combustible", "kilometraje", "año_modelo", "equipamiento",
    "color_exterior", "color_interior",
}

SNIPPET_LEN = 150


def extract_assistant_text(content: str) -> str:
    """Elimina bloques <tool_call>...</tool_call> y retorna solo texto natural."""
    return re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL).strip()


def check_field_colon_pattern(text: str, field: str) -> list[dict]:
    """
    Detecta patrón: field_name: value  ó  field_name : value
    Para campos 'duros', cualquier aparición es violación.
    Para campos 'blandos', se detecta patrón field_name: seguido de valor.
    """
    violations = []
    # Buscar con underscore (campo técnico puro: precio_lista: ...)
    pattern_underscore = re.compile(
        rf"\b{re.escape(field)}\s*:\s*\S", re.IGNORECASE
    )
    for match in pattern_underscore.finditer(text):
        start = max(0, match.start() - 30)
        end = min(len(text), match.start() + SNIPPET_LEN - 30)
        snippet = text[start:end].replace("\n", " ↵ ")
        violations.append({
            "field": field,
            "type": "field_colon_value",
            "snippet": snippet,
            "pos": match.start(),
        })

    # Para campos blandos, también buscar la versión "humanizada" con mayúscula
    # Ej. "Kilometraje: 45000" o "Transmisión: Automático"
    # Esto es válido en prosa pero es un indicador de volcado tipo ficha técnica
    if field in SOFT_FIELDS:
        # Mapeo de campo técnico a variantes humanizadas que siguen siendo dump
        humanized_map = {
            "kilometraje": ["Kilometraje"],
            "transmision": ["Transmisión", "Transmision"],
            "combustible": ["Combustible"],
            "traccion": ["Tracción", "Traccion"],
            "precio_lista": ["Precio lista", "Precio de lista"],
            "precio_oferta": ["Precio oferta", "Precio de oferta"],
            "año_modelo": ["Año modelo", "Año"],
            "equipamiento": [],  # demasiado natural, skip humanized
            "color_exterior": ["Color exterior", "Color Exterior"],
            "color_interior": ["Color interior", "Color Interior"],
        }
        for variant in humanized_map.get(field, []):
            pattern_human = re.compile(
                rf"(?:^|[\n•\-–—|])\s*(?:📍|✓|✅|🔹|▸|►)?\s*{re.escape(variant)}\s*:\s*\S",
                re.MULTILINE
            )
            for match in pattern_human.finditer(text):
                # Avoid double-counting if underscore version already caught it
                already = any(
                    abs(v["pos"] - match.start()) < 20 for v in violations
                )
                if not already:
                    start = max(0, match.start() - 10)
                    end = min(len(text), match.start() + SNIPPET_LEN - 10)
                    snippet = text[start:end].replace("\n", " ↵ ")
                    violations.append({
                        "field": field,
                        "type": "humanized_field_dump",
                        "snippet": snippet,
                        "pos": match.start(),
                    })

    return violations


def check_json_like_patterns(text: str) -> list[dict]:
    """Detecta patrones JSON crudos en texto del asistente."""
    violations = []

    # Patrón 1: {"key": "value"} o {"key": value}
    json_obj_pattern = re.compile(r'\{["\'][a-z_]+["\']:\s*["\']?[^}]{1,100}["\']?\}')
    for match in json_obj_pattern.finditer(text):
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        snippet = text[start:end].replace("\n", " ↵ ")
        violations.append({
            "field": "_json_object",
            "type": "json_in_text",
            "snippet": snippet,
            "pos": match.start(),
        })

    # Patrón 2: "key": "value" (comillas de JSON)
    json_kv_pattern = re.compile(r'"[a-z_]+":\s*"[^"]*"')
    for match in json_kv_pattern.finditer(text):
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        snippet = text[start:end].replace("\n", " ↵ ")
        violations.append({
            "field": "_json_kv",
            "type": "json_kv_in_text",
            "snippet": snippet,
            "pos": match.start(),
        })

    return violations


def check_consecutive_kv_dump(text: str) -> list[dict]:
    """
    Detecta 3+ pares clave: valor consecutivos, indicando volcado de ficha técnica.
    Patrones como:
      - Precio: $350,000
      - Kilometraje: 45,000 km
      - Transmisión: Automático
      - Ubicación: Monterrey
    """
    violations = []
    # Buscar bloques con 3+ líneas tipo "- Label: value" o "• Label: value"
    kv_line = r"(?:[\-•▸►🔹✅📍✓]\s*)?[A-ZÁÉÍÓÚa-záéíóú][A-Za-záéíóúñÁÉÍÓÚÑ\s_]{2,30}:\s*\S[^\n]{0,120}"
    block_pattern = re.compile(
        rf"(?:{kv_line}\n){{3,}}", re.MULTILINE
    )
    for match in block_pattern.finditer(text):
        # Contar cuántas líneas key:value hay en el bloque
        lines = match.group().strip().split("\n")
        kv_count = sum(1 for l in lines if re.match(r"\s*(?:[\-•▸►🔹✅📍✓]\s*)?[A-ZÁÉÍÓÚa-z].+:\s*\S", l))
        if kv_count >= 3:
            snippet = match.group()[:SNIPPET_LEN].replace("\n", " ↵ ")
            violations.append({
                "field": "_consecutive_kv",
                "type": f"kv_dump_{kv_count}_fields",
                "snippet": snippet,
                "pos": match.start(),
                "kv_count": kv_count,
            })

    # También detectar formato pipe: "Precio: $350,000 | Kilometraje: 45,000 | ..."
    pipe_pattern = re.compile(
        r"(?:[A-ZÁÉÍÓÚa-záéíóú][A-Za-záéíóúñÁÉÍÓÚÑ\s_]{2,25}:\s*\S[^|]{0,60}\|){2,}[A-ZÁÉÍÓÚa-z][^|\n]{0,80}"
    )
    for match in pipe_pattern.finditer(text):
        parts = match.group().split("|")
        kv_parts = [p for p in parts if re.search(r"[A-Za-záéíóú]+\s*:", p.strip())]
        if len(kv_parts) >= 3:
            snippet = match.group()[:SNIPPET_LEN].replace("\n", " ↵ ")
            already = any(
                v["type"].startswith("kv_dump") and abs(v["pos"] - match.start()) < 50
                for v in violations
            )
            if not already:
                violations.append({
                    "field": "_pipe_kv",
                    "type": f"pipe_kv_dump_{len(kv_parts)}_fields",
                    "snippet": snippet,
                    "pos": match.start(),
                    "kv_count": len(kv_parts),
                })

    return violations


def audit_dataset(path: Path) -> dict:
    """Ejecuta auditoría completa del dataset."""
    results = {
        "total_conversations": 0,
        "total_assistant_messages": 0,
        "conversations_with_violations": set(),
        "violations": [],
        "violation_counts_by_type": Counter(),
        "violation_counts_by_field": Counter(),
    }

    with open(path, "r", encoding="utf-8") as f:
        for conv_idx, line in enumerate(f):
            conv = json.loads(line)
            results["total_conversations"] += 1

            for msg_idx, msg in enumerate(conv["messages"]):
                if msg["role"] != "assistant":
                    continue

                results["total_assistant_messages"] += 1
                text = extract_assistant_text(msg["content"])
                if not text:
                    continue

                conv_violations = []

                # Check 1: Field name: value patterns
                for field in FORBIDDEN_FIELDS:
                    field_violations = check_field_colon_pattern(text, field)
                    conv_violations.extend(field_violations)

                # Check 2: JSON-like patterns
                json_violations = check_json_like_patterns(text)
                conv_violations.extend(json_violations)

                # Check 3: Consecutive key-value dumps
                kv_violations = check_consecutive_kv_dump(text)
                conv_violations.extend(kv_violations)

                for v in conv_violations:
                    v["conv_idx"] = conv_idx
                    v["msg_idx"] = msg_idx
                    results["violations"].append(v)
                    results["conversations_with_violations"].add(conv_idx)
                    results["violation_counts_by_type"][v["type"]] += 1
                    results["violation_counts_by_field"][v["field"]] += 1

    return results


def print_report(results: dict):
    """Imprime reporte detallado de la auditoría."""
    print("=" * 100)
    print("AUDITORÍA DE LEAKAGE DE CAMPOS DE HERRAMIENTAS EN TEXTO DEL ASISTENTE")
    print(f"Dataset: {DATASET}")
    print("=" * 100)

    print(f"\n📊 RESUMEN GENERAL")
    print(f"   Conversaciones totales:           {results['total_conversations']:,}")
    print(f"   Mensajes de asistente analizados: {results['total_assistant_messages']:,}")
    print(f"   Violaciones totales detectadas:   {len(results['violations']):,}")
    print(f"   Conversaciones afectadas:         {len(results['conversations_with_violations']):,}")
    pct = len(results['conversations_with_violations']) / results['total_conversations'] * 100
    print(f"   Porcentaje afectado:              {pct:.1f}%")

    print(f"\n{'─' * 100}")
    print(f"📋 VIOLACIONES POR TIPO")
    print(f"{'─' * 100}")
    for vtype, count in results["violation_counts_by_type"].most_common():
        print(f"   {vtype:40s} → {count:,} ocurrencias")

    print(f"\n{'─' * 100}")
    print(f"📋 VIOLACIONES POR CAMPO")
    print(f"{'─' * 100}")
    for field, count in results["violation_counts_by_field"].most_common():
        severity = "HARD" if field in HARD_FORBIDDEN else "soft" if field in SOFT_FIELDS else "struct"
        print(f"   [{severity:5s}] {field:30s} → {count:,} ocurrencias")

    # === HARD violations detail ===
    hard_violations = [v for v in results["violations"] if v["field"] in HARD_FORBIDDEN]
    if hard_violations:
        print(f"\n{'=' * 100}")
        print(f"🔴 VIOLACIONES DURAS (campos que NUNCA deben aparecer) — {len(hard_violations)} total")
        print(f"{'=' * 100}")
        for v in hard_violations:
            print(f"\n  Conv {v['conv_idx']:>4d} | msg {v['msg_idx']} | {v['type']:30s} | campo: {v['field']}")
            print(f"  Snippet: {v['snippet'][:SNIPPET_LEN]}")

    # === field_colon_value (underscore fields in text) ===
    underscore_violations = [
        v for v in results["violations"]
        if v["type"] == "field_colon_value" and v["field"] not in HARD_FORBIDDEN
    ]
    if underscore_violations:
        print(f"\n{'=' * 100}")
        print(f"🟠 CAMPOS TÉCNICOS CON UNDERSCORE EN TEXTO (field_name: value) — {len(underscore_violations)} total")
        print(f"{'=' * 100}")
        for v in underscore_violations:
            print(f"\n  Conv {v['conv_idx']:>4d} | msg {v['msg_idx']} | campo: {v['field']}")
            print(f"  Snippet: {v['snippet'][:SNIPPET_LEN]}")

    # === Humanized field dumps ===
    humanized = [v for v in results["violations"] if v["type"] == "humanized_field_dump"]
    if humanized:
        print(f"\n{'=' * 100}")
        print(f"🟡 VOLCADO HUMANIZADO (Label: valor en formato ficha) — {len(humanized)} total")
        print(f"  (Primeras 50 muestras)")
        print(f"{'=' * 100}")
        for v in humanized[:50]:
            print(f"\n  Conv {v['conv_idx']:>4d} | msg {v['msg_idx']} | campo: {v['field']}")
            print(f"  Snippet: {v['snippet'][:SNIPPET_LEN]}")
        if len(humanized) > 50:
            print(f"\n  ... y {len(humanized) - 50} más")

    # === KV dumps ===
    kv_dumps = [v for v in results["violations"] if "kv_dump" in v["type"] or "pipe_kv" in v["type"]]
    if kv_dumps:
        print(f"\n{'=' * 100}")
        print(f"🟡 VOLCADO DE FICHAS TÉCNICAS (3+ campos consecutivos) — {len(kv_dumps)} total")
        print(f"  (Primeras 50 muestras)")
        print(f"{'=' * 100}")
        for v in kv_dumps[:50]:
            kv_n = v.get("kv_count", "?")
            print(f"\n  Conv {v['conv_idx']:>4d} | msg {v['msg_idx']} | {v['type']} ({kv_n} campos)")
            print(f"  Snippet: {v['snippet'][:SNIPPET_LEN]}")
        if len(kv_dumps) > 50:
            print(f"\n  ... y {len(kv_dumps) - 50} más")

    # === JSON in text ===
    json_violations = [v for v in results["violations"] if "json" in v["type"]]
    if json_violations:
        print(f"\n{'=' * 100}")
        print(f"🔴 JSON CRUDO EN TEXTO DEL ASISTENTE — {len(json_violations)} total")
        print(f"{'=' * 100}")
        for v in json_violations[:30]:
            print(f"\n  Conv {v['conv_idx']:>4d} | msg {v['msg_idx']} | {v['type']}")
            print(f"  Snippet: {v['snippet'][:SNIPPET_LEN]}")
        if len(json_violations) > 30:
            print(f"\n  ... y {len(json_violations) - 30} más")

    # === Lista completa de conversaciones afectadas ===
    affected = sorted(results["conversations_with_violations"])
    print(f"\n{'=' * 100}")
    print(f"LISTA COMPLETA DE CONVERSACIONES AFECTADAS ({len(affected)} conversaciones)")
    print(f"{'=' * 100}")

    # Agrupar por severidad
    hard_convs = set()
    for v in results["violations"]:
        if v["field"] in HARD_FORBIDDEN or "json" in v["type"]:
            hard_convs.add(v["conv_idx"])

    underscore_convs = set()
    for v in results["violations"]:
        if v["type"] == "field_colon_value" and v["field"] not in HARD_FORBIDDEN:
            underscore_convs.add(v["conv_idx"])

    kv_convs = set()
    for v in results["violations"]:
        if "kv_dump" in v["type"] or "pipe_kv" in v["type"]:
            kv_convs.add(v["conv_idx"])

    humanized_convs = set()
    for v in results["violations"]:
        if v["type"] == "humanized_field_dump":
            humanized_convs.add(v["conv_idx"])

    print(f"\n  HARD (campos técnicos crudos / JSON):  {len(hard_convs)} conversaciones")
    if hard_convs:
        print(f"    {sorted(hard_convs)}")

    print(f"\n  UNDERSCORE fields en texto (field_name: val): {len(underscore_convs)} conversaciones")
    if underscore_convs:
        _print_conv_list(sorted(underscore_convs))

    print(f"\n  KV DUMP (ficha técnica 3+ campos):     {len(kv_convs)} conversaciones")
    if kv_convs:
        _print_conv_list(sorted(kv_convs))

    print(f"\n  HUMANIZED field dump (Label: valor):    {len(humanized_convs)} conversaciones")
    if humanized_convs:
        _print_conv_list(sorted(humanized_convs))


def _print_conv_list(convs: list, per_line=20):
    """Imprime lista de índices de conversación en filas compactas."""
    for i in range(0, len(convs), per_line):
        chunk = convs[i : i + per_line]
        print(f"    {', '.join(str(c) for c in chunk)}")


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DATASET.exists():
        print(f"ERROR: No se encontró el dataset: {DATASET}")
        sys.exit(1)

    print(f"Analizando {DATASET}...")
    results = audit_dataset(DATASET)
    print_report(results)

    # Guardar resultados JSON para uso programático
    output_json = DATASET.parent / "AUDITORIA_LEAKAGE_FIELDS.json"
    export = {
        "total_conversations": results["total_conversations"],
        "total_assistant_messages": results["total_assistant_messages"],
        "total_violations": len(results["violations"]),
        "conversations_affected": len(results["conversations_with_violations"]),
        "affected_indices": sorted(results["conversations_with_violations"]),
        "violations_by_type": dict(results["violation_counts_by_type"].most_common()),
        "violations_by_field": dict(results["violation_counts_by_field"].most_common()),
        "violations": [
            {
                "conv_idx": v["conv_idx"],
                "msg_idx": v["msg_idx"],
                "field": v["field"],
                "type": v["type"],
                "snippet": v["snippet"][:SNIPPET_LEN],
            }
            for v in results["violations"]
        ],
    }
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"\nResultados JSON guardados en: {output_json}")
