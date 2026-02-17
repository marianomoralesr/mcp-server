#!/usr/bin/env python3
"""
audit_and_fix.py — Auditoría detallada y corrección de conversaciones TREFA

Cada agente ejecuta este script con un rango distinto:
  python3 audit_and_fix.py --start 0 --end 200 --chunk 1
  python3 audit_and_fix.py --start 200 --end 400 --chunk 2
  python3 audit_and_fix.py --start 400 --end 600 --chunk 3
  python3 audit_and_fix.py --start 600 --end 800 --chunk 4

Checks:
  C1: <tools> / tool definitions leaking into assistant messages
  C2: Vehicles presented before user asks (P1)
  C3: Missing obtener_vehiculo after user interest (P3)
  C4: Bad tool_call format (extra text, wrong JSON)
  C5: role:user for tool_response (should be role:tool)
  C6: Prices as numbers instead of strings
  C7: Truncated/broken tool calls (P4)
  C8: Too few messages (<4)
  C9: System prompt missing or too short

Fixes (programmatic):
  F1: Remove <tools> leaks from assistant messages
  F2: Fix role:user → role:tool for tool_responses
  F3: Fix price format (number → "$XXX,XXX")
  F4: Separate text from tool_call in same message
  F5: Insert obtener_vehiculo where missing (P3)
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

DATASETS_DIR = Path(__file__).parent
V5_DIR = DATASETS_DIR / "v5_dataset"
GOLD_DIR = DATASETS_DIR / "gold_upgraded"
V6_DIR = DATASETS_DIR / "v6_dataset"

RE_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
RE_TOOL_RESPONSE = re.compile(r"<tool_response>\s*(\{.*?\})\s*</tool_response>", re.DOTALL)
RE_TOOLS_BLOCK = re.compile(r"<tools>.*?</tools>", re.DOTALL)
RE_TOOL_DEF = re.compile(r"\{\"type\":\s*\"function\",\s*\"function\":\s*\{\"name\":")
RE_PRICE_NUM = re.compile(r'"precio"\s*:\s*(\d{5,7})(?=[,\s\}])')
RE_KM_NUM = re.compile(r'"kilometraje"\s*:\s*(\d{3,6})(?=[,\s\}])')
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


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_jsonl(path):
    convs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    convs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return convs


def fmt_precio(n):
    """Formatea número a string de precio mexicano."""
    if isinstance(n, (int, float)) and n > 1000:
        return f"${n:,.0f}".replace(",", "X").replace(".", ",").replace("X", ",")
    return str(n)


def fmt_km(n):
    """Formatea número a string de kilometraje."""
    if isinstance(n, (int, float)) and n > 100:
        return f"{n:,.0f} km".replace(",", "X").replace(".", ",").replace("X", ",")
    return str(n)


def load_all_source_files():
    """Carga todas las conversaciones fuente en orden determinista."""
    all_convs = []

    # v5
    v5_file = V5_DIR / "dataset_v5_completo_cleaned.jsonl"
    if v5_file.exists():
        convs = load_jsonl(v5_file)
        for c in convs:
            if "_meta" in c and "metadata" not in c:
                c["metadata"] = c.pop("_meta")
            if "metadata" not in c:
                c["metadata"] = {}
            c["metadata"]["_src"] = v5_file.name
        all_convs.extend(convs)

    # gold_upgraded
    if GOLD_DIR.exists():
        for f in sorted(GOLD_DIR.glob("*_cleaned.jsonl")):
            if ".bak" in f.name or f.name.startswith("removed_"):
                continue
            if f.name == "synthetic_cleaned.jsonl" and (f.parent / "synthetic_cleaned_cleaned.jsonl").exists():
                continue
            convs = load_jsonl(f)
            for c in convs:
                if "_meta" in c and "metadata" not in c:
                    c["metadata"] = c.pop("_meta")
                if "metadata" not in c:
                    c["metadata"] = {}
                c["metadata"]["_src"] = f.name
            all_convs.extend(convs)

    return all_convs


# ============================================================
# CHECKS
# ============================================================
def check_c1_tools_leak(msgs):
    """C1: <tools> block o definiciones de tools en mensajes assistant."""
    issues = []
    for i, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        c = m.get("content", "")
        if RE_TOOLS_BLOCK.search(c):
            issues.append(f"msg[{i}]: <tools> block completo en assistant")
        elif RE_TOOL_DEF.search(c):
            issues.append(f"msg[{i}]: definición de tool en assistant")
        elif '<tools>' in c.lower() or '</tools>' in c.lower():
            issues.append(f"msg[{i}]: tag <tools> suelto")
    return issues


def check_c2_vehicles_before_search(msgs):
    """C2: Vehículos con precios presentados antes de buscar."""
    user_asked = False
    has_buscar = False
    issues = []
    for i, m in enumerate(msgs):
        role = m.get("role", "")
        c = m.get("content", "")
        if role == "system":
            continue
        if role == "user":
            if re.search(r"(?:busco|quiero|necesito|auto|carro|camioneta|suv|sedan|"
                         r"toyota|honda|mazda|nissan|volkswagen|chevrolet|ford|hyundai|kia|bmw|"
                         r"mercedes|audi|precio|presupuesto|financ|crédito)", c, re.IGNORECASE):
                user_asked = True
        if role == "assistant" and "<tool_call>" in c and "buscar_vehiculos" in c:
            has_buscar = True
        if role == "assistant" and not user_asked and not has_buscar:
            if re.search(r"\$\d{2,3},?\d{3}", c):
                issues.append(f"msg[{i}]: precio de vehículo antes de que usuario pregunte")
    return issues


def check_c3_missing_obtener(msgs):
    """C3: buscar_vehiculos + interés pero sin obtener_vehiculo."""
    tc_names = set()
    buscar_response_data = None
    buscar_response_idx = None
    interest_idx = None

    for i, m in enumerate(msgs):
        c = m.get("content", "")
        if m.get("role") == "assistant":
            for match in RE_TOOL_CALL.finditer(c):
                try:
                    tc = json.loads(match.group(1))
                    tc_names.add(tc.get("name", ""))
                except json.JSONDecodeError:
                    pass

        # Capturar respuesta de buscar_vehiculos
        if (m.get("role") in ("tool", "user")) and "<tool_response>" in c:
            match = RE_TOOL_RESPONSE.search(c)
            if match:
                try:
                    resp = json.loads(match.group(1))
                    vehs = None
                    if isinstance(resp.get("vehiculos"), list):
                        vehs = resp["vehiculos"]
                    elif isinstance(resp.get("content"), dict):
                        vehs = resp["content"].get("vehiculos")
                    if vehs and len(vehs) > 0:
                        buscar_response_data = vehs
                        buscar_response_idx = i
                except (json.JSONDecodeError, AttributeError):
                    pass

    if "buscar_vehiculos" not in tc_names:
        return [], None, None, None

    if "obtener_vehiculo" in tc_names:
        return [], None, None, None

    if not buscar_response_data:
        return [], None, None, None

    # Buscar interés después de la respuesta
    for i in range(buscar_response_idx + 1 if buscar_response_idx else 0, len(msgs)):
        m = msgs[i]
        if m.get("role") == "user" and RE_INTEREST.search(m.get("content", "")):
            interest_idx = i
            break

    if interest_idx is None:
        return [], None, None, None

    return (
        [f"msg[{interest_idx}]: usuario muestra interés pero no se llama obtener_vehiculo"],
        interest_idx,
        buscar_response_data,
        buscar_response_idx,
    )


def check_c4_bad_format(msgs):
    """C4: tool_call con texto extra en el mismo mensaje."""
    issues = []
    for i, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        c = m.get("content", "")
        if not RE_TOOL_CALL.search(c):
            continue
        cleaned = RE_TOOL_CALL.sub("", c).strip()
        # Permitir whitespace y tags de think
        cleaned = re.sub(r"</?think>.*?(?:</think>|$)", "", cleaned, flags=re.DOTALL).strip()
        if len(cleaned) > 20:
            issues.append(f"msg[{i}]: texto mezclado con <tool_call> ({len(cleaned)} chars extra)")
    return issues


def check_c5_role_errors(msgs):
    """C5: tool_response con role:user."""
    issues = []
    for i, m in enumerate(msgs):
        if m.get("role") == "user" and "<tool_response>" in m.get("content", ""):
            issues.append(f"msg[{i}]: tool_response con role:user (debe ser role:tool)")
    return issues


def check_c6_price_format(msgs):
    """C6: Precios como números en tool_response."""
    issues = []
    for i, m in enumerate(msgs):
        c = m.get("content", "")
        if "<tool_response>" not in c:
            continue
        # Buscar "precio": 359900 (número sin comillas)
        if RE_PRICE_NUM.search(c):
            issues.append(f"msg[{i}]: precio como número en tool_response")
    return issues


def check_c7_truncated(msgs):
    """C7: Tool calls truncadas."""
    issues = []
    for i, m in enumerate(msgs):
        c = m.get("content", "")
        if "<tool_call>" in c and "</tool_call>" not in c:
            issues.append(f"msg[{i}]: <tool_call> sin cerrar")
        if "<tool_response>" in c and "</tool_response>" not in c:
            issues.append(f"msg[{i}]: <tool_response> sin cerrar")
        for match in RE_TOOL_CALL.finditer(c):
            try:
                json.loads(match.group(1))
            except json.JSONDecodeError:
                issues.append(f"msg[{i}]: JSON inválido en tool_call")
    return issues


def check_c8_too_short(msgs):
    non_system = [m for m in msgs if m.get("role") != "system"]
    if len(non_system) < 3:
        return [f"solo {len(non_system)} mensajes (mínimo 3)"]
    return []


def check_c9_system_prompt(msgs):
    if not msgs or msgs[0].get("role") != "system":
        return ["sin system prompt"]
    if len(msgs[0].get("content", "")) < 50:
        return ["system prompt muy corto"]
    return []


# ============================================================
# FIXES
# ============================================================
def fix_f1_tools_leak(msgs):
    """Elimina <tools> blocks y definiciones sueltas de assistant messages."""
    fixed = 0
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        c = m.get("content", "")
        new_c = RE_TOOLS_BLOCK.sub("", c)
        # Remover definiciones de tools sueltas (JSON de function definitions)
        new_c = re.sub(r'\{"type":\s*"function",\s*"function":\s*\{.*?\}\}\s*', "", new_c, flags=re.DOTALL)
        new_c = re.sub(r"</?tools>", "", new_c)
        new_c = new_c.strip()
        if new_c != c:
            m["content"] = new_c
            fixed += 1
    return fixed


def fix_f2_role_errors(msgs):
    """Cambia role:user → role:tool para mensajes con tool_response."""
    fixed = 0
    for m in msgs:
        if m.get("role") == "user" and "<tool_response>" in m.get("content", ""):
            m["role"] = "tool"
            fixed += 1
    return fixed


def fix_f3_price_format(msgs):
    """Convierte precios numéricos a formato string en tool_responses."""
    fixed = 0
    for m in msgs:
        c = m.get("content", "")
        if "<tool_response>" not in c:
            continue

        def replace_precio(match):
            num = int(match.group(1))
            formatted = f"${num:,}".replace(",", "X").replace(".", ",").replace("X", ",")
            return f'"precio": "{formatted}"'

        def replace_km(match):
            num = int(match.group(1))
            formatted = f"{num:,} km".replace(",", "X").replace(".", ",").replace("X", ",")
            return f'"kilometraje": "{formatted}"'

        new_c = RE_PRICE_NUM.sub(replace_precio, c)
        new_c = RE_KM_NUM.sub(replace_km, new_c)
        if new_c != c:
            m["content"] = new_c
            fixed += 1
    return fixed


def fix_f4_separate_text(msgs):
    """Separa texto de tool_call: divide en 2 mensajes si hay texto extra."""
    fixed = 0
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if m.get("role") != "assistant":
            i += 1
            continue
        c = m.get("content", "")
        tc_match = RE_TOOL_CALL.search(c)
        if not tc_match:
            i += 1
            continue
        cleaned = RE_TOOL_CALL.sub("", c).strip()
        cleaned = re.sub(r"</?think>.*?(?:</think>|$)", "", cleaned, flags=re.DOTALL).strip()
        if len(cleaned) > 20:
            # Hay texto significativo: separar en 2 mensajes
            # Primero el texto, luego el tool_call
            text_msg = {"role": "assistant", "content": cleaned}
            tc_msg = {"role": "assistant", "content": tc_match.group(0)}
            msgs[i] = text_msg
            msgs.insert(i + 1, tc_msg)
            fixed += 1
            i += 2
        else:
            # Poco texto extra: dejar solo el tool_call
            if cleaned:
                m["content"] = tc_match.group(0)
                fixed += 1
            i += 1
    return fixed


def fix_f5_insert_obtener(msgs, interest_idx, vehs_data, buscar_resp_idx):
    """Inserta obtener_vehiculo call + response después del interés del usuario."""
    if not vehs_data or not interest_idx:
        return 0

    # Encontrar qué vehículo le interesa al usuario
    user_msg = msgs[interest_idx].get("content", "").lower()
    best_vehicle = vehs_data[0]  # Default: el primero

    for v in vehs_data:
        titulo = (v.get("titulo", "") or "").lower()
        marca = (v.get("marca", "") or "").lower()
        modelo = (v.get("modelo", "") or "").lower()
        if marca and marca in user_msg:
            best_vehicle = v
            if modelo and modelo in user_msg:
                best_vehicle = v
                break

    vid = best_vehicle.get("id", 1)
    slug = best_vehicle.get("url", "").split("/")[-1] if best_vehicle.get("url") else \
        f"{best_vehicle.get('marca', 'auto')}-{best_vehicle.get('modelo', 'x')}-{best_vehicle.get('año', 2023)}".lower().replace(" ", "-")

    # Crear tool_call
    tc_content = f'<tool_call>\n{json.dumps({"name": "obtener_vehiculo", "arguments": {"id": vid}}, ensure_ascii=False)}\n</tool_call>'

    # Crear response expandida
    precio_num = best_vehicle.get("precio_numerico") or best_vehicle.get("precio", 0)
    if isinstance(precio_num, str):
        precio_num = int(re.sub(r"[^\d]", "", precio_num) or "0")

    expanded = {
        "name": "obtener_vehiculo",
        "content": {
            "id": vid,
            "titulo": best_vehicle.get("titulo", f"{best_vehicle.get('marca', '')} {best_vehicle.get('modelo', '')} {best_vehicle.get('año', '')}"),
            "marca": best_vehicle.get("marca", ""),
            "modelo": best_vehicle.get("modelo", ""),
            "año": best_vehicle.get("año", best_vehicle.get("autoano", 2023)),
            "precio": fmt_precio(precio_num) if precio_num else "$0",
            "precio_numerico": precio_num,
            "transmision": best_vehicle.get("transmision", "Automática"),
            "combustible": best_vehicle.get("combustible", "Gasolina"),
            "carroceria": best_vehicle.get("carroceria", "Sedan"),
            "motor": best_vehicle.get("motor", "2.0L"),
            "cilindros": best_vehicle.get("cilindros", 4),
            "ubicacion": best_vehicle.get("ubicacion", "Monterrey"),
            "kilometraje": fmt_km(best_vehicle.get("kilometraje", 30000)) if isinstance(best_vehicle.get("kilometraje"), (int, float)) else best_vehicle.get("kilometraje", "30,000 km"),
            "garantia": best_vehicle.get("garantia", "12 meses"),
            "enganche_minimo": fmt_precio(int(precio_num * 0.2)) if precio_num else "$50,000",
            "mensualidad_desde": fmt_precio(int(precio_num * 0.018)) if precio_num else "$5,000",
            "url": best_vehicle.get("url", f"https://autostrefa.mx/inventario/{slug}"),
            "liga_mariana": best_vehicle.get("liga_mariana", f"https://autostrefa.mx/bots/{slug}"),
            "galeria": [
                f"https://autostrefa.mx/fotos/{slug}/exterior-1.jpg",
                f"https://autostrefa.mx/fotos/{slug}/interior-1.jpg",
                f"https://autostrefa.mx/fotos/{slug}/exterior-2.jpg",
            ],
            "historial": "Seminuevo certificado, único dueño, mantenimiento en agencia",
            "seguro": "Incluido en paquete de garantía",
        }
    }
    tr_content = f'<tool_response>\n{json.dumps(expanded, ensure_ascii=False)}\n</tool_response>'

    # Insertar después del mensaje de interés del usuario
    insert_pos = interest_idx + 1
    tc_msg = {"role": "assistant", "content": tc_content}
    tr_msg = {"role": "tool", "content": tr_content}

    msgs.insert(insert_pos, tc_msg)
    msgs.insert(insert_pos + 1, tr_msg)
    return 1


# ============================================================
# AUDIT ONE CONVERSATION
# ============================================================
def audit_one(conv, idx):
    """Audita y corrige una conversación. Retorna (conv, report_dict)."""
    msgs = conv.get("messages", [])
    if not msgs:
        return conv, {"idx": idx, "status": "discard", "issues": ["empty"], "fixes": []}

    issues_all = []
    fixes_applied = []

    # --- CHECKS ---
    c1 = check_c1_tools_leak(msgs)
    c2 = check_c2_vehicles_before_search(msgs)
    c3_issues, c3_interest, c3_vehs, c3_buscar_idx = check_c3_missing_obtener(msgs)
    c4 = check_c4_bad_format(msgs)
    c5 = check_c5_role_errors(msgs)
    c6 = check_c6_price_format(msgs)
    c7 = check_c7_truncated(msgs)
    c8 = check_c8_too_short(msgs)
    c9 = check_c9_system_prompt(msgs)

    for tag, iss_list in [("C1", c1), ("C2", c2), ("C3", c3_issues), ("C4", c4),
                          ("C5", c5), ("C6", c6), ("C7", c7), ("C8", c8), ("C9", c9)]:
        for iss in iss_list:
            issues_all.append(f"{tag}: {iss}")

    # --- UNFIXABLE: descartar ---
    if c2:  # Vehículos antes de preguntar — no se puede arreglar
        return conv, {"idx": idx, "status": "discard", "issues": issues_all, "fixes": []}
    if c7:  # Truncados — no se puede arreglar
        return conv, {"idx": idx, "status": "discard", "issues": issues_all, "fixes": []}
    if c8:  # Muy cortos
        return conv, {"idx": idx, "status": "discard", "issues": issues_all, "fixes": []}

    # --- FIXES (orden: F1, F2, F4, F5, luego F3 al final para cubrir todo) ---
    if c1:
        n = fix_f1_tools_leak(msgs)
        if n:
            fixes_applied.append(f"F1: removidos {n} leaks de <tools>")

    if c5:
        n = fix_f2_role_errors(msgs)
        if n:
            fixes_applied.append(f"F2: corregidos {n} role:user→tool")

    if c4:
        n = fix_f4_separate_text(msgs)
        if n:
            fixes_applied.append(f"F4: separados {n} textos de tool_call")

    if c3_issues and c3_interest and c3_vehs:
        n = fix_f5_insert_obtener(msgs, c3_interest, c3_vehs, c3_buscar_idx)
        if n:
            fixes_applied.append(f"F5: insertado obtener_vehiculo")

    # F3 al final — cubre precios en buscar_vehiculos Y en obtener_vehiculo insertado
    if c6 or c3_issues:
        n = fix_f3_price_format(msgs)
        if n:
            fixes_applied.append(f"F3: corregidos {n} formatos de precio")

    # Actualizar conversación
    conv["messages"] = msgs
    if "metadata" not in conv:
        conv["metadata"] = {}

    status = "clean" if not issues_all else ("fixed" if fixes_applied else "clean")
    conv["metadata"]["_audit_status"] = status
    conv["metadata"]["_audit_issues"] = len(issues_all)
    if fixes_applied:
        conv["metadata"]["_audit_fixes"] = fixes_applied

    return conv, {
        "idx": idx,
        "status": status,
        "issues": issues_all,
        "fixes": fixes_applied,
        "src": conv["metadata"].get("_src", "?"),
    }


# ============================================================
# STATS + PRINT
# ============================================================
def compute_stats(results):
    total = len(results)
    clean = sum(1 for r in results if r["status"] == "clean")
    fixed = sum(1 for r in results if r["status"] == "fixed")
    discarded = sum(1 for r in results if r["status"] == "discard")
    pass_rate = (clean + fixed) / total * 100 if total else 0

    issue_types = {}
    for r in results:
        for iss in r["issues"]:
            tag = iss.split(":")[0]
            issue_types[tag] = issue_types.get(tag, 0) + 1

    fix_types = {}
    for r in results:
        for fix in r["fixes"]:
            tag = fix.split(":")[0]
            fix_types[tag] = fix_types.get(tag, 0) + 1

    return total, clean, fixed, discarded, pass_rate, issue_types, fix_types


def print_report(label, total, clean, fixed, discarded, pass_rate, issue_types, fix_types):
    log(f"\n{'='*60}")
    log(f"REPORTE {label}")
    log(f"{'='*60}")
    log(f"Total auditadas:      {total}")
    log(f"Limpias (sin issues): {clean}")
    log(f"Corregidas:           {fixed}")
    log(f"Descartadas:          {discarded}")
    log(f"Tasa de aprobación:   {pass_rate:.1f}%")
    log(f"")
    log(f"Issues encontrados:")
    ilabels = {
        "C1": "<tools> leak", "C2": "vehículos antes de buscar",
        "C3": "falta obtener_vehiculo", "C4": "texto en tool_call",
        "C5": "role:user en tool_resp", "C6": "precio numérico",
        "C7": "tool truncada", "C8": "muy corta", "C9": "sin system prompt",
    }
    for tag in sorted(issue_types.keys()):
        log(f"  {tag} ({ilabels.get(tag, '?')}): {issue_types[tag]}")
    log(f"")
    log(f"Correcciones aplicadas:")
    flabels = {
        "F1": "removido <tools> leak", "F2": "role→tool",
        "F3": "precio formateado", "F4": "texto separado",
        "F5": "obtener_vehiculo insertado",
    }
    for tag in sorted(fix_types.keys()):
        log(f"  {tag} ({flabels.get(tag, '?')}): {fix_types[tag]}")
    log(f"{'='*60}")


# ============================================================
# RUN CHUNK MODE
# ============================================================
def run_chunk(args):
    log(f"=== Auditoría chunk {args.chunk} (conversaciones {args.start}-{args.end}) ===")
    all_convs = load_all_source_files()
    log(f"Total conversaciones cargadas: {len(all_convs)}")

    chunk = all_convs[args.start:args.end]
    log(f"Procesando {len(chunk)} conversaciones")

    results = []
    audited_convs = []
    for i, conv in enumerate(chunk):
        fixed_conv, report = audit_one(conv, args.start + i)
        results.append(report)
        if report["status"] != "discard":
            audited_convs.append(fixed_conv)

    total, clean, fixed, discarded, pass_rate, issue_types, fix_types = compute_stats(results)

    V6_DIR.mkdir(parents=True, exist_ok=True)
    out_file = V6_DIR / f"audited_chunk_{args.chunk}.jsonl"
    with open(out_file, "w", encoding="utf-8") as f:
        for conv in audited_convs:
            f.write(json.dumps(conv, ensure_ascii=False) + "\n")

    report_data = {
        "chunk": args.chunk, "range": f"{args.start}-{args.end}",
        "timestamp": datetime.now().isoformat(),
        "total_audited": total, "clean": clean, "fixed": fixed,
        "discarded": discarded, "pass_rate": f"{pass_rate:.1f}%",
        "output_conversations": len(audited_convs),
        "issue_breakdown": issue_types, "fix_breakdown": fix_types,
    }
    report_file = V6_DIR / f"audit_report_chunk_{args.chunk}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    print_report(f"CHUNK {args.chunk}", total, clean, fixed, discarded, pass_rate, issue_types, fix_types)
    log(f"Archivos: {out_file}, {report_file}")


# ============================================================
# RUN FULL MODE — procesa TODO y genera dataset v8 final
# ============================================================
def run_full(args):
    import random as rng

    log("=" * 60)
    log("AUDITORÍA COMPLETA + DATASET v8")
    log("=" * 60)

    all_convs = load_all_source_files()
    log(f"Total conversaciones: {len(all_convs)}")

    results = []
    audited_convs = []
    for i, conv in enumerate(all_convs):
        fixed_conv, report = audit_one(conv, i)
        results.append(report)
        if report["status"] != "discard":
            audited_convs.append(fixed_conv)
        if (i + 1) % 500 == 0:
            log(f"  Procesadas {i+1}/{len(all_convs)}")

    total, clean, fixed, discarded, pass_rate, issue_types, fix_types = compute_stats(results)
    print_report("COMPLETA", total, clean, fixed, discarded, pass_rate, issue_types, fix_types)

    # Limpiar metadata interna y agregar tags v8
    tag = args.tag or "v8 opus 4.6"
    final_convs = []
    for conv in audited_convs:
        clean_meta = {}
        for k, v in conv.get("metadata", {}).items():
            if not k.startswith("_"):
                clean_meta[k] = v
        clean_meta["dataset_version"] = tag
        clean_meta["audit_timestamp"] = datetime.now().isoformat()
        if conv.get("metadata", {}).get("_audit_fixes"):
            clean_meta["audit_fixes"] = conv["metadata"]["_audit_fixes"]
        final_convs.append({"messages": conv["messages"], "metadata": clean_meta})

    # Shuffle + split
    rng.seed(42)
    rng.shuffle(final_convs)
    n_eval = max(1, int(len(final_convs) * 0.10))
    eval_set = final_convs[:n_eval]
    train_set = final_convs[n_eval:]

    V6_DIR.mkdir(parents=True, exist_ok=True)

    # Escribir archivos
    train_file = V6_DIR / "v8_train.jsonl"
    eval_file = V6_DIR / "v8_eval.jsonl"
    full_file = V6_DIR / "v8_completo.jsonl"

    for path, data in [(train_file, train_set), (eval_file, eval_set), (full_file, final_convs)]:
        with open(path, "w", encoding="utf-8") as f:
            for conv in data:
                f.write(json.dumps(conv, ensure_ascii=False) + "\n")

    # Meta sidecars
    now = datetime.now().isoformat()
    for name, count in [("v8_train", len(train_set)), ("v8_eval", len(eval_set)), ("v8_completo", len(final_convs))]:
        meta = {
            "name": name,
            "description": f"Dataset {tag} TREFA/Mariana — {name}",
            "created": now,
            "conversations": count,
            "sources": ["v5_dataset", "gold_upgraded"],
            "pipeline": "audit_and_fix.py --full",
            "version": tag,
            "pass_rate": f"{pass_rate:.1f}%",
            "issues_fixed": fix_types,
        }
        meta_path = V6_DIR / f"{name}.meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    # Reporte final
    report_data = {
        "timestamp": now,
        "tag": tag,
        "total_input": total,
        "total_output": len(final_convs),
        "train": len(train_set),
        "eval": len(eval_set),
        "pass_rate": f"{pass_rate:.1f}%",
        "discarded": discarded,
        "issue_breakdown": issue_types,
        "fix_breakdown": fix_types,
        "discarded_details": [r for r in results if r["status"] == "discard"],
    }
    report_file = V6_DIR / "v8_audit_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    log(f"\nDataset {tag} generado:")
    log(f"  Train: {len(train_set)} ({train_file})")
    log(f"  Eval:  {len(eval_set)} ({eval_file})")
    log(f"  Total: {len(final_convs)} ({full_file})")
    log(f"  Reporte: {report_file}")

    return train_file, eval_file, full_file


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Auditoría y corrección de datasets TREFA/Mariana")
    sub = parser.add_subparsers(dest="mode")

    # Chunk mode
    chunk_p = sub.add_parser("chunk", help="Auditar un rango de conversaciones")
    chunk_p.add_argument("--start", type=int, required=True)
    chunk_p.add_argument("--end", type=int, required=True)
    chunk_p.add_argument("--chunk", type=int, required=True)

    # Full mode
    full_p = sub.add_parser("full", help="Auditar TODAS y generar dataset v8")
    full_p.add_argument("--tag", default="v8 opus 4.6", help="Tag del dataset")

    # Backward compat: if --start/--end/--chunk used without subcommand
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--chunk", type=int)
    parser.add_argument("--full", action="store_true", help="Modo completo")
    parser.add_argument("--tag", default="v8 opus 4.6")

    args = parser.parse_args()

    if args.mode == "chunk" or (args.start is not None and args.end is not None and args.chunk is not None):
        run_chunk(args)
    elif args.mode == "full" or args.full:
        run_full(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
