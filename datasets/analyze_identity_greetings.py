#!/usr/bin/env python3
"""
Análisis de identidad y saludos en el dataset de entrenamiento merged_v10_together_train.jsonl

Checks:
1. Identity — ¿El bot se presenta como "Mariana" en el primer mensaje de asistente?
2. Enthusiastic greeting — ¿El primer mensaje de asistente tiene saludo entusiasta?
3. Repeated "Hola" in consecutive assistant messages
4. System prompt identity — ¿El system prompt usa "Mariana" o "TREFABOT"?
"""

import json
import re
import sys
from collections import defaultdict

FILE = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets/merged_v10_together_train.jsonl"

# ─── Load all conversations ───────────────────────────────────────────────────
conversations = []
with open(FILE, "r", encoding="utf-8") as f:
    for i, line in enumerate(f):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            conversations.append((i, obj))
        except json.JSONDecodeError as e:
            print(f"  [WARN] Línea {i} no es JSON válido: {e}")

total = len(conversations)
print(f"{'='*80}")
print(f" ANÁLISIS DE IDENTIDAD Y SALUDOS — merged_v10_together_train.jsonl")
print(f"{'='*80}")
print(f"\nTotal de conversaciones analizadas: {total}\n")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_assistant_messages(msgs):
    """Return list of (index_in_msgs, content) for assistant messages."""
    result = []
    for idx, m in enumerate(msgs):
        if m.get("role") == "assistant":
            content = m.get("content", "")
            result.append((idx, content))
    return result

def get_first_text_assistant_message(msgs):
    """Return the first assistant message that is NOT purely a tool_call."""
    for m in msgs:
        if m.get("role") == "assistant":
            content = m.get("content", "")
            # Skip if it's purely a tool call (no human-readable text)
            stripped = content.strip()
            if stripped.startswith("<tool_call>") and stripped.endswith("</tool_call>"):
                continue
            if stripped.startswith("{") and "name" in stripped and "arguments" in stripped:
                continue
            return content
    return None

def get_system_content(msgs):
    for m in msgs:
        if m.get("role") == "system":
            return m.get("content", "")
    return None

def excerpt(text, maxlen=120):
    if not text:
        return "(vacío)"
    text = text.replace("\n", " ").strip()
    if len(text) > maxlen:
        return text[:maxlen] + "..."
    return text

# ─── CHECK 1: Identity — First assistant message mentions "Mariana" ───────────
print(f"{'─'*80}")
print(f" CHECK 1: IDENTIDAD — ¿El primer mensaje de texto del asistente menciona 'Mariana'?")
print(f"{'─'*80}\n")

check1_fail = []  # (line_idx, first_assistant_content)
check1_other_names = []  # (line_idx, name_found, content)

other_name_patterns = [
    (re.compile(r'\bTREFABOT\b', re.IGNORECASE), "TREFABOT"),
    (re.compile(r'\bTrefaBot\b'), "TrefaBot"),
    (re.compile(r'\bSoy\s+(?!Mariana\b)(\w+)', re.IGNORECASE), None),  # "Soy X" where X != Mariana
    (re.compile(r'\bme llamo\s+(?!Mariana\b)(\w+)', re.IGNORECASE), None),  # "me llamo X"
]

for line_idx, obj in conversations:
    msgs = obj.get("messages", [])
    first_text = get_first_text_assistant_message(msgs)

    if first_text is None:
        check1_fail.append((line_idx, "(sin mensaje de asistente de texto)"))
        continue

    # Check if Mariana is mentioned
    if not re.search(r'mariana', first_text, re.IGNORECASE):
        check1_fail.append((line_idx, first_text))

    # Check for other names in ALL assistant messages
    all_asst = get_assistant_messages(msgs)
    for msg_idx, content in all_asst:
        for pattern, label in other_name_patterns:
            match = pattern.search(content)
            if match:
                if label:
                    name = label
                else:
                    name = match.group(1) if match.lastindex else match.group(0)
                    # Skip common false positives
                    if name.lower() in ("mariana", "trefa", "de", "la", "el", "tu", "un", "una"):
                        continue
                check1_other_names.append((line_idx, name, content))

print(f"  Conversaciones donde el 1er mensaje de texto NO menciona 'Mariana': {len(check1_fail)} / {total} ({len(check1_fail)/total*100:.1f}%)")
print()
if check1_fail:
    print(f"  Primeros 40 problemas:")
    for line_idx, content in check1_fail[:40]:
        print(f"    Línea {line_idx:>5}: {excerpt(content)}")
    if len(check1_fail) > 40:
        print(f"    ... y {len(check1_fail)-40} más")
print()

if check1_other_names:
    # Deduplicate by line_idx + name
    seen = set()
    unique_other = []
    for line_idx, name, content in check1_other_names:
        key = (line_idx, name)
        if key not in seen:
            seen.add(key)
            unique_other.append((line_idx, name, content))

    print(f"  Conversaciones con nombres alternativos (no 'Mariana'): {len(unique_other)}")
    for line_idx, name, content in unique_other[:30]:
        print(f"    Línea {line_idx:>5}: nombre='{name}' -> {excerpt(content, 100)}")
    if len(unique_other) > 30:
        print(f"    ... y {len(unique_other)-30} más")
else:
    print(f"  Nombres alternativos encontrados: 0 (OK)")

# ─── CHECK 2: Enthusiastic greeting ──────────────────────────────────────────
print(f"\n{'─'*80}")
print(f" CHECK 2: SALUDO ENTUSIASTA en el primer mensaje de texto del asistente")
print(f"{'─'*80}\n")

# Patterns for enthusiastic greetings
enthusiastic_patterns = [
    re.compile(r'¡\s*Hola\s*!', re.IGNORECASE),
    re.compile(r'Hola\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Qué\s+tal\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Buen(?:os|as)\s+(?:días|tardes|noches)\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Holi\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Hey\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Perfecto\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Claro\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Excelente\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Genial\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Super\s*!', re.IGNORECASE),
    re.compile(r'¡\s*Bienvenid[oa]\s*!', re.IGNORECASE),
]

# Pattern for flat "Hola" (no exclamation)
flat_hola = re.compile(r'^Hola\b(?!\s*!)', re.IGNORECASE)

check2_no_greeting = []
check2_flat_greeting = []
check2_ok = 0

for line_idx, obj in conversations:
    msgs = obj.get("messages", [])
    first_text = get_first_text_assistant_message(msgs)

    if first_text is None:
        check2_no_greeting.append((line_idx, "(sin mensaje de asistente)"))
        continue

    has_enthusiastic = any(p.search(first_text) for p in enthusiastic_patterns)

    if has_enthusiastic:
        check2_ok += 1
    elif flat_hola.search(first_text):
        check2_flat_greeting.append((line_idx, first_text))
    else:
        check2_no_greeting.append((line_idx, first_text))

print(f"  Saludos entusiastas (OK):     {check2_ok} / {total} ({check2_ok/total*100:.1f}%)")
print(f"  Saludos planos ('Hola' sin !): {len(check2_flat_greeting)} / {total} ({len(check2_flat_greeting)/total*100:.1f}%)")
print(f"  Sin saludo:                    {len(check2_no_greeting)} / {total} ({len(check2_no_greeting)/total*100:.1f}%)")
print()

if check2_flat_greeting:
    print(f"  Saludos planos (primeros 30):")
    for line_idx, content in check2_flat_greeting[:30]:
        print(f"    Línea {line_idx:>5}: {excerpt(content)}")
    if len(check2_flat_greeting) > 30:
        print(f"    ... y {len(check2_flat_greeting)-30} más")
    print()

if check2_no_greeting:
    print(f"  Sin saludo (primeros 30):")
    for line_idx, content in check2_no_greeting[:30]:
        print(f"    Línea {line_idx:>5}: {excerpt(content)}")
    if len(check2_no_greeting) > 30:
        print(f"    ... y {len(check2_no_greeting)-30} más")

# ─── CHECK 3: Repeated "Hola" in consecutive assistant messages ──────────────
print(f"\n{'─'*80}")
print(f" CHECK 3: 'Hola' REPETIDO en mensajes de asistente consecutivos")
print(f"{'─'*80}\n")

hola_pattern = re.compile(r'(?:¡\s*)?Hola\b', re.IGNORECASE)

check3_issues = []  # (line_idx, [(msg_i, content_i), (msg_j, content_j)])

for line_idx, obj in conversations:
    msgs = obj.get("messages", [])
    asst_msgs = get_assistant_messages(msgs)

    # Filter out pure tool_call messages
    text_asst = []
    for msg_idx, content in asst_msgs:
        stripped = content.strip()
        if stripped.startswith("<tool_call>") and stripped.endswith("</tool_call>"):
            continue
        if stripped.startswith("{") and "name" in stripped and "arguments" in stripped:
            continue
        text_asst.append((msg_idx, content))

    # Check consecutive pairs
    for i in range(len(text_asst) - 1):
        idx_a, content_a = text_asst[i]
        idx_b, content_b = text_asst[i + 1]

        has_hola_a = hola_pattern.search(content_a)
        has_hola_b = hola_pattern.search(content_b)

        if has_hola_a and has_hola_b:
            check3_issues.append((line_idx, [
                (idx_a, content_a),
                (idx_b, content_b)
            ]))

print(f"  Pares consecutivos con 'Hola' repetido: {len(check3_issues)}")

# Count unique conversations
check3_convs = set(item[0] for item in check3_issues)
print(f"  Conversaciones afectadas: {len(check3_convs)} / {total} ({len(check3_convs)/total*100:.1f}%)")
print()

if check3_issues:
    print(f"  Detalle (primeros 50 pares):")
    for line_idx, pair in check3_issues[:50]:
        idx_a, content_a = pair[0]
        idx_b, content_b = pair[1]
        print(f"    Línea {line_idx:>5}:")
        print(f"      msg[{idx_a}]: {excerpt(content_a, 100)}")
        print(f"      msg[{idx_b}]: {excerpt(content_b, 100)}")
        print()
    if len(check3_issues) > 50:
        print(f"    ... y {len(check3_issues)-50} pares más")

# ─── CHECK 4: System prompt identity ─────────────────────────────────────────
print(f"\n{'─'*80}")
print(f" CHECK 4: IDENTIDAD EN EL SYSTEM PROMPT")
print(f"{'─'*80}\n")

system_types = defaultdict(list)

for line_idx, obj in conversations:
    msgs = obj.get("messages", [])
    sys_content = get_system_content(msgs)

    if sys_content is None:
        system_types["sin_system"].append(line_idx)
    elif sys_content == "__SYSTEM_PROMPT__":
        system_types["placeholder_(__SYSTEM_PROMPT__)"].append(line_idx)
    else:
        has_mariana = bool(re.search(r'mariana', sys_content, re.IGNORECASE))
        has_trefabot = bool(re.search(r'trefabot', sys_content, re.IGNORECASE))

        if has_trefabot and not has_mariana:
            system_types["TREFABOT_only"].append(line_idx)
        elif has_trefabot and has_mariana:
            system_types["both_TREFABOT_and_Mariana"].append(line_idx)
        elif has_mariana:
            system_types["Mariana_only"].append(line_idx)
        else:
            system_types["other_no_name"].append(line_idx)

print(f"  Distribución de system prompts:")
for key, indices in sorted(system_types.items(), key=lambda x: -len(x[1])):
    print(f"    {key}: {len(indices)} ({len(indices)/total*100:.1f}%)")
    if key in ("TREFABOT_only", "both_TREFABOT_and_Mariana", "other_no_name") and indices:
        # Show sample system content
        sample_idx = indices[0]
        for li, obj in conversations:
            if li == sample_idx:
                sys_c = get_system_content(obj.get("messages", []))
                print(f"      Ejemplo (línea {sample_idx}): {excerpt(sys_c, 200)}")
                break
        if len(indices) > 1:
            print(f"      Líneas: {indices[:20]}{'...' if len(indices)>20 else ''}")

# Also check for TREFABOT anywhere in assistant messages
print()
print(f"  Búsqueda de 'TREFABOT' en TODOS los mensajes de asistente:")
trefabot_in_asst = []
for line_idx, obj in conversations:
    msgs = obj.get("messages", [])
    for m in msgs:
        if m.get("role") == "assistant":
            if re.search(r'trefabot', m.get("content", ""), re.IGNORECASE):
                trefabot_in_asst.append((line_idx, m["content"]))
                break

print(f"    Conversaciones con 'TREFABOT' en respuesta de asistente: {len(trefabot_in_asst)}")
if trefabot_in_asst:
    for line_idx, content in trefabot_in_asst[:20]:
        print(f"      Línea {line_idx:>5}: {excerpt(content, 120)}")

# ─── RESUMEN ──────────────────────────────────────────────────────────────────
print(f"\n{'='*80}")
print(f" RESUMEN")
print(f"{'='*80}")
print(f"""
  Total conversaciones: {total}

  CHECK 1 — Identidad 'Mariana' en 1er mensaje:
    OK: {total - len(check1_fail)} ({(total-len(check1_fail))/total*100:.1f}%)
    Falta: {len(check1_fail)} ({len(check1_fail)/total*100:.1f}%)
    Nombres alternativos: {len(set(x[0] for x in check1_other_names))} conversaciones

  CHECK 2 — Saludo entusiasta:
    OK (entusiasta): {check2_ok} ({check2_ok/total*100:.1f}%)
    Plano (sin !): {len(check2_flat_greeting)} ({len(check2_flat_greeting)/total*100:.1f}%)
    Sin saludo: {len(check2_no_greeting)} ({len(check2_no_greeting)/total*100:.1f}%)

  CHECK 3 — 'Hola' repetido en mensajes consecutivos:
    Pares afectados: {len(check3_issues)}
    Conversaciones afectadas: {len(check3_convs)} ({len(check3_convs)/total*100:.1f}%)

  CHECK 4 — System prompt:
    Placeholder (__SYSTEM_PROMPT__): {len(system_types.get('placeholder_(__SYSTEM_PROMPT__)', []))}
    Con 'Mariana': {len(system_types.get('Mariana_only', []))}
    Con 'TREFABOT': {len(system_types.get('TREFABOT_only', []))}
    TREFABOT en respuestas asistente: {len(trefabot_in_asst)}
""")
