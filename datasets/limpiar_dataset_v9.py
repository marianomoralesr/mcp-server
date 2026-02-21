#!/usr/bin/env python3
"""
Limpieza del dataset v8 → v9 para reentrenamiento.

Criterios de calidad basados en la guía TREFA:
  C1. Saludo: primer turno assistant debe presentarse como Mariana + pregunta
  C2. No tools antes de criterio: buscar_vehiculos solo después de criterio claro
  C3. Máximo 3 opciones de vehículos por turno
  C4. Negritas solo en nombre del auto
  C5. Sin tool_call leaks en respuestas al usuario
  C6. Tuteo obligatorio (tú, NUNCA usted)
  C7. Sin "Encantada de conocerte" ni formalidades vacías
  C8. Cierre con pregunta o siguiente paso (cada turno assistant visible)
  C9. Tool calls en respuestas visibles al usuario

Uso:
  python3 limpiar_dataset_v9.py
  python3 limpiar_dataset_v9.py --input v8_train.jsonl --output v9_train.jsonl
  python3 limpiar_dataset_v9.py --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import Counter

# ─── Patrones ──────────────────────────────────────────────────

GREETING_PATTERN = re.compile(
    r"^\s*(hola|hey|buenos?\s*d[ií]as?|buenas?\s*tardes?|buenas?\s*noches?|"
    r"qu[eé]\s*tal|qué\s*onda|hi|hello|saludos|buen\s*d[ií]a)\s*[.!,?😊🙋‍♂️🙋‍♀️👋]*\s*$",
    re.IGNORECASE,
)

NAME_ONLY_PATTERN = re.compile(
    r"^\s*(?:me\s+llamo\s+|soy\s+|mi\s+nombre\s+es\s+)?\w+(?:\s+\w+)?\s*[.!]*\s*$",
    re.IGNORECASE,
)

VEHICLE_KEYWORDS = re.compile(
    r"(busc|quiero|necesito|interes|auto[s]?\b|carro|coche|camioneta|suv|sedan|sedán|"
    r"pick\s*up|presupuesto|precio|financ|crédito|credito|enganche|mensualidad|"
    r"toyota|honda|nissan|mazda|kia|hyundai|chevrolet|chevy|ford|volkswagen|vw|bmw|"
    r"mercedes|audi|seat|renault|peugeot|suzuki|mitsubishi|jeep|dodge|ram|tesla|"
    r"volvo|mini|fiat|porsche|jaguar|land\s*rover|buick|gmc|cadillac|lincoln|"
    r"acura|infiniti|lexus|genesis|subaru|"
    r"corolla|civic|sentra|versa|march|cx-?[3579]|rav4|tucson|sportage|"
    r"rio|accent|aveo|spark|onix|jetta|golf|tiguan|polo|model\s*[3ysx]|vento|"
    r"ranger|hilux|frontier|tacoma|wrangler|mustang|camaro|"
    r"horario|sucursal|abierto|abren|cerrado|dirección|ubicación|"
    r"garantía|devoluc|arrepient|trámite|proceso|placa|documento)",
    re.IGNORECASE,
)

VEHICLE_BULLET = re.compile(r"[•\-\*]\s*\*\*.*?\*\*")

BOLD_WITH_DETAILS = re.compile(
    r"\*\*([^*]+(?:(?:\$|MXN|Automático|Manual|Gasolina|Diésel|sucursal)[^*]*))\*\*",
    re.IGNORECASE,
)

TOOL_CALL_RE = re.compile(r"<tool_call>\s*\{.*?\}\s*</tool_call>", re.DOTALL)
TOOL_CALL_TAG = re.compile(r"</?tool_call>")

# Frases formales prohibidas
FORMAL_PHRASES = re.compile(
    r"(encantad[ao]\s+de\s+conocer|es\s+un\s+placer|un\s+gusto\s+conocer|"
    r"mucho\s+gusto\s+en\s+conocer|es\s+un\s+honor|me\s+honra)",
    re.IGNORECASE,
)

# Ustedeo — formas que deben ser tú
USTED_PATTERNS = [
    # Pronombres y conjugaciones comunes de usted
    (re.compile(r"\busted\b", re.IGNORECASE), "tú"),
    (re.compile(r"\bsu nombre\b", re.IGNORECASE), "tu nombre"),
    (re.compile(r"\bsu presupuesto\b", re.IGNORECASE), "tu presupuesto"),
    (re.compile(r"\bsu auto\b", re.IGNORECASE), "tu auto"),
    (re.compile(r"\bsu vehículo\b", re.IGNORECASE), "tu vehículo"),
    (re.compile(r"\ble interesa\b", re.IGNORECASE), "te interesa"),
    (re.compile(r"\ble gustaría\b", re.IGNORECASE), "te gustaría"),
    (re.compile(r"\ble parece\b", re.IGNORECASE), "te parece"),
    (re.compile(r"\ble puedo\b", re.IGNORECASE), "te puedo"),
    (re.compile(r"\ble ayudo\b", re.IGNORECASE), "te ayudo"),
    (re.compile(r"\ble recomiendo\b", re.IGNORECASE), "te recomiendo"),
    (re.compile(r"\ble ofrezco\b", re.IGNORECASE), "te ofrezco"),
    (re.compile(r"\ble muestro\b", re.IGNORECASE), "te muestro"),
    (re.compile(r"\ble comparto\b", re.IGNORECASE), "te comparto"),
    (re.compile(r"\ble comento\b", re.IGNORECASE), "te comento"),
    (re.compile(r"\ble funciona\b", re.IGNORECASE), "te funciona"),
    (re.compile(r"\ble conviene\b", re.IGNORECASE), "te conviene"),
    (re.compile(r"\ble queda\b", re.IGNORECASE), "te queda"),
    (re.compile(r"\bme comparte\b", re.IGNORECASE), "me compartes"),
    (re.compile(r"\bme diga\b", re.IGNORECASE), "me digas"),
    (re.compile(r"\bme permite\b", re.IGNORECASE), "me permites"),
    (re.compile(r"\bcoménteme\b", re.IGNORECASE), "cuéntame"),
    (re.compile(r"\bdígame\b", re.IGNORECASE), "dime"),
    (re.compile(r"\bpermítame\b", re.IGNORECASE), "déjame"),
    (re.compile(r"\bdisculpe\b", re.IGNORECASE), "disculpa"),
]

# Tools de búsqueda que NO deben llamarse sin criterio
SEARCH_TOOLS = {"buscar_vehiculos", "buscar_alternativas", "comparar_vehiculos",
                "estadisticas_inventario"}


# ─── Funciones auxiliares ──────────────────────────────────────

def is_tool_call_only(content: str) -> bool:
    """Retorna True si el mensaje es SOLO tool_call(s), sin texto visible."""
    stripped = TOOL_CALL_RE.sub("", content).strip()
    stripped = re.sub(r"</?think>", "", stripped).strip()
    stripped = re.sub(r"<think>.*?</think>", "", stripped, flags=re.DOTALL).strip()
    return len(stripped) == 0


def get_visible_assistant_turns(messages: list) -> list:
    """Retorna índices de turnos assistant que tienen texto visible al usuario."""
    visible = []
    for i, msg in enumerate(messages):
        if msg["role"] == "assistant" and not is_tool_call_only(msg.get("content", "")):
            visible.append(i)
    return visible


# ─── Fixes ─────────────────────────────────────────────────────

def fix_C1_greeting(messages: list) -> tuple:
    """C1: Primer turno assistant visible debe contener 'Mariana' y terminar con '?'."""
    visible = get_visible_assistant_turns(messages)
    if not visible:
        return messages, False, False

    first_visible = visible[0]
    content = messages[first_visible].get("content", "")
    has_mariana = "mariana" in content.lower()
    has_question = "?" in content

    if has_mariana and has_question:
        return messages, False, False

    # No se puede arreglar automáticamente → marcar como remoción
    return messages, True, True


def fix_C2_tools_before_criteria(messages: list) -> tuple:
    """C2: No buscar_vehiculos antes de que el usuario dé criterio."""
    user_has_criteria = False

    for msg in messages:
        if msg["role"] == "user":
            content = msg.get("content", "")
            if not content.strip().startswith("<tool_response>"):
                if VEHICLE_KEYWORDS.search(content):
                    user_has_criteria = True

        if msg["role"] == "assistant" and not user_has_criteria:
            content = msg.get("content", "")
            for match in TOOL_CALL_RE.finditer(content):
                try:
                    call_json = json.loads(
                        re.search(r"\{.*\}", match.group(0), re.DOTALL).group(0)
                    )
                    if call_json.get("name", "") in SEARCH_TOOLS:
                        return messages, True, True  # Remover
                except (json.JSONDecodeError, AttributeError):
                    pass

    return messages, False, False


def fix_C3_max_three(messages: list) -> tuple:
    """C3: Máximo 3 opciones de vehículos por turno assistant."""
    fixed = False
    new_messages = []

    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", "")
            bullets = VEHICLE_BULLET.findall(content)
            if len(bullets) > 3:
                lines = content.split("\n")
                new_lines = []
                bullet_count = 0
                for line in lines:
                    if VEHICLE_BULLET.search(line):
                        bullet_count += 1
                        if bullet_count > 3:
                            continue
                    new_lines.append(line)
                msg = {**msg, "content": "\n".join(new_lines)}
                fixed = True
        new_messages.append(msg)

    return new_messages, fixed, False


def fix_C4_bold_name_only(messages: list) -> tuple:
    """C4: Negritas solo en nombre del auto."""
    fixed = False
    new_messages = []

    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", "")

            def fix_bold(match):
                nonlocal fixed
                text = match.group(1)
                if re.search(r"(\$|MXN|Automático|Manual|Gasolina|sucursal|—|,\s*\$)", text, re.IGNORECASE):
                    parts = re.split(r"\s*[—\-]\s*", text, maxsplit=1)
                    name_part = parts[0].strip()
                    rest = parts[1].strip() if len(parts) > 1 else ""
                    name_clean = re.split(r"\s*[\$,]", name_part)[0].strip()
                    if rest:
                        fixed = True
                        return f"**{name_clean}** — {rest}"
                    elif name_clean != text:
                        fixed = True
                        return f"**{name_clean}**"
                return match.group(0)

            new_content = BOLD_WITH_DETAILS.sub(fix_bold, content)
            msg = {**msg, "content": new_content}
        new_messages.append(msg)

    return new_messages, fixed, False


def fix_C5_tool_leaks(messages: list) -> tuple:
    """C5: Limpiar tool_call tags de respuestas visibles al usuario."""
    fixed = False
    new_messages = []

    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", "")
            clean = TOOL_CALL_RE.sub("", content).strip()
            clean = TOOL_CALL_TAG.sub("", clean).strip()
            if clean and clean != content.strip() and not is_tool_call_only(content):
                msg = {**msg, "content": clean}
                fixed = True
        new_messages.append(msg)

    return new_messages, fixed, False


def fix_C6_tuteo(messages: list) -> tuple:
    """C6: Forzar tuteo, eliminar ustedeo."""
    fixed = False
    new_messages = []

    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", "")
            new_content = content
            for pattern, replacement in USTED_PATTERNS:
                new_content, count = pattern.subn(replacement, new_content)
                if count > 0:
                    fixed = True
            if new_content != content:
                msg = {**msg, "content": new_content}
        new_messages.append(msg)

    return new_messages, fixed, False


def fix_C7_no_formal(messages: list) -> tuple:
    """C7: Eliminar 'Encantada de conocerte' y formalidades vacías."""
    fixed = False
    new_messages = []

    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", "")
            new_content = FORMAL_PHRASES.sub("", content)
            # Limpiar espacios dobles resultantes
            new_content = re.sub(r"  +", " ", new_content)
            new_content = re.sub(r"\n\s*\n\s*\n", "\n\n", new_content)
            if new_content.strip() != content.strip():
                msg = {**msg, "content": new_content.strip()}
                fixed = True
        new_messages.append(msg)

    return new_messages, fixed, False


def fix_C8_close_with_question(messages: list) -> tuple:
    """C8: Cada turno assistant visible debe cerrar con ? o CTA.
    Si no cierra con pregunta, intentar agregar una."""
    fixed = False
    new_messages = []

    # Frases de cierre aceptables sin ?
    cta_patterns = re.compile(
        r"(aquí\s+est[oa]y|no\s+dudes|con\s+gusto|estoy\s+para|"
        r"te\s+espero|te\s+esperamos|quedo\s+a\s+tus|a\s+tus\s+órdenes)",
        re.IGNORECASE,
    )

    for msg in messages:
        if msg["role"] == "assistant" and not is_tool_call_only(msg.get("content", "")):
            content = msg.get("content", "").strip()
            # Revisar si cierra con ? o CTA
            last_line = content.split("\n")[-1].strip()
            has_question = "?" in last_line
            has_cta = bool(cta_patterns.search(last_line))

            if not has_question and not has_cta:
                # No se puede arreglar automáticamente de forma fiable
                # Solo registrar el fix, no modificar
                pass

        new_messages.append(msg)

    return new_messages, fixed, False


def fix_C9_greeting_then_tools(messages: list) -> tuple:
    """C9: Si primer user es saludo/nombre y primer assistant responde con tool_call → remover."""
    user_msgs = [(i, m) for i, m in enumerate(messages) if m["role"] == "user"
                 and not m.get("content", "").startswith("<tool_response>")]

    if not user_msgs:
        return messages, False, False

    first_user_idx, first_user = user_msgs[0]
    first_text = first_user.get("content", "").strip()

    is_simple = (
        GREETING_PATTERN.match(first_text)
        or (NAME_ONLY_PATTERN.match(first_text)
            and not VEHICLE_KEYWORDS.search(first_text)
            and len(first_text.split()) <= 4)
    )

    if not is_simple:
        return messages, False, False

    for i in range(first_user_idx + 1, len(messages)):
        if messages[i]["role"] == "assistant":
            content = messages[i].get("content", "")
            if "<tool_call>" in content:
                for match in TOOL_CALL_RE.finditer(content):
                    try:
                        call_json = json.loads(
                            re.search(r"\{.*\}", match.group(0), re.DOTALL).group(0)
                        )
                        if call_json.get("name", "") in SEARCH_TOOLS:
                            return messages, True, True
                    except (json.JSONDecodeError, AttributeError):
                        pass
            break

    # Check second user msg too (just name)
    if len(user_msgs) >= 2:
        second_idx, second_user = user_msgs[1]
        second_text = second_user.get("content", "").strip()
        is_name = (
            NAME_ONLY_PATTERN.match(second_text)
            and not VEHICLE_KEYWORDS.search(second_text)
            and len(second_text.split()) <= 4
        )
        if is_name:
            for i in range(second_idx + 1, len(messages)):
                if messages[i]["role"] == "assistant":
                    content = messages[i].get("content", "")
                    if "<tool_call>" in content:
                        for match in TOOL_CALL_RE.finditer(content):
                            try:
                                call_json = json.loads(
                                    re.search(r"\{.*\}", match.group(0), re.DOTALL).group(0)
                                )
                                if call_json.get("name", "") in SEARCH_TOOLS:
                                    return messages, True, True
                            except (json.JSONDecodeError, AttributeError):
                                pass
                    break

    return messages, False, False


# ─── Pipeline ──────────────────────────────────────────────────

ALL_FIXES = [
    ("C9_greeting_tools", fix_C9_greeting_then_tools),
    ("C2_tools_no_criteria", fix_C2_tools_before_criteria),
    ("C1_missing_greeting", fix_C1_greeting),
    ("C3_max_3_options", fix_C3_max_three),
    ("C4_bold_name_only", fix_C4_bold_name_only),
    ("C5_tool_leak", fix_C5_tool_leaks),
    ("C6_tuteo", fix_C6_tuteo),
    ("C7_no_formal", fix_C7_no_formal),
    ("C8_close_question", fix_C8_close_with_question),
]


def process_conversation(conv: dict) -> tuple:
    """Aplica todas las reglas de limpieza.
    Returns: (cleaned_conv, fixes_applied, should_remove)
    """
    messages = conv["messages"]
    fixes = []
    should_remove = False

    for fix_name, fix_fn in ALL_FIXES:
        messages, fixed, remove = fix_fn(messages)
        if remove:
            fixes.append(fix_name)
            should_remove = True
            break  # No seguir procesando si se va a remover
        if fixed:
            fixes.append(fix_name)

    cleaned = {**conv, "messages": messages}
    if fixes:
        meta = cleaned.get("metadata", {})
        meta["v9_fixes"] = fixes
        cleaned["metadata"] = meta

    return cleaned, fixes, should_remove


# ─── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Limpieza dataset v8 → v9")
    parser.add_argument("--input", default="v8_train.jsonl")
    parser.add_argument("--eval-input", default="v8_eval.jsonl")
    parser.add_argument("--output", default="v9_train.jsonl")
    parser.add_argument("--eval-output", default="v9_eval.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for input_file, output_file in [(args.input, args.output), (args.eval_input, args.eval_output)]:
        if not Path(input_file).exists():
            print(f"Archivo no encontrado: {input_file}, saltando...")
            continue

        print(f"\n{'='*60}")
        print(f"  Procesando: {input_file} → {output_file}")
        print(f"{'='*60}")

        conversations = []
        with open(input_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    conversations.append(json.loads(line))

        print(f"  Conversaciones cargadas: {len(conversations)}")

        cleaned = []
        removed = []
        fix_counts = Counter()

        for conv in conversations:
            result, fixes, should_remove = process_conversation(conv)
            for f in fixes:
                fix_counts[f] += 1
            if should_remove:
                removed.append((conv, fixes))
            else:
                cleaned.append(result)

        # Reporte
        total_fixes = sum(fix_counts.values()) - sum(
            fix_counts[k] for k in fix_counts if any(
                conv_fixes == [k] for _, conv_fixes in removed
            )
        )
        print(f"\n  Resultados:")
        print(f"    Conservadas:  {len(cleaned)}")
        print(f"    Removidas:    {len(removed)}")
        print(f"\n  Detalle de fixes:")
        for fix, count in fix_counts.most_common():
            label = "REMOVE" if fix in ("C9_greeting_tools", "C2_tools_no_criteria", "C1_missing_greeting") and count > 0 else "FIX"
            print(f"    {fix}: {count}")

        if removed:
            print(f"\n  Ejemplos de conversaciones removidas:")
            for conv, fixes in removed[:8]:
                msgs = conv["messages"]
                user_msgs = [m for m in msgs if m["role"] == "user"
                             and not m.get("content", "").startswith("<tool_response>")]
                first_user = user_msgs[0]["content"][:60] if user_msgs else "?"
                print(f"    [{', '.join(fixes)}] \"{first_user}\"")

        if not args.dry_run:
            with open(output_file, "w", encoding="utf-8") as f:
                for conv in cleaned:
                    f.write(json.dumps(conv, ensure_ascii=False) + "\n")
            print(f"\n  Guardado: {output_file} ({len(cleaned)} conversaciones)")

            removed_file = output_file.replace(".jsonl", "_removed.jsonl")
            with open(removed_file, "w", encoding="utf-8") as f:
                for conv, fixes in removed:
                    entry = {**conv, "_removal_reason": fixes}
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"  Removidas: {removed_file} ({len(removed)} conversaciones)")
        else:
            print(f"\n  [DRY RUN] No se escribieron archivos")

    print(f"\n{'='*60}")
    print(f"  Limpieza completada")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
