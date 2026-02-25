#!/usr/bin/env python3
"""
===============================================================
 fix_merged_quality.py — Correcciones de calidad post-auditoría
===============================================================

Fixes aplicados (en orden):
  F1. role="user" con <tool_response> → role="tool"
  F2. Typo <tool_reponse> → <tool_response>
  F3. Tool responses sin <tool_response> wrapper
  F4. TREFABOT / TrefaBot → Mariana
  F5. Inyectar identidad "Mariana" en primer assistant text si falta
  F6. Eliminar doble "Hola" en assistant messages consecutivos
  F7. Asegurar que Mariana (assistant) cierra la conversación
  F8. Drop conversaciones con comparar_vehiculos < 2 IDs
  F9. solicitar_datos_contacto: telefono NO es required (es WhatsApp)

Incluye verificación post-fix.

Uso:
    python3 fix_merged_quality.py
    python3 fix_merged_quality.py --dry-run
===============================================================
"""

import json
import re
import os
import sys
import argparse
from copy import deepcopy
from collections import Counter

BASE = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets"

stats = Counter()


# ─── Utilidades ───────────────────────────────────────────────

def extract_text_without_tc(content):
    """Remueve <tool_call> blocks y devuelve el texto restante."""
    return re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', str(content)).strip()


def split_by_tool_calls(content):
    """Divide content en partes: texto y tool_call blocks."""
    return re.split(r'(<tool_call>[\s\S]*?</tool_call>)', str(content))


def has_hola(text):
    """Detecta si el texto contiene una variante de 'Hola'."""
    return bool(re.search(r'[¡!]?\s*hola\b', text, re.IGNORECASE))


# ─── F1: Fix role para tool responses ─────────────────────────

def fix_roles(messages):
    """Cambia role='user' a role='tool' para mensajes con <tool_response>."""
    for m in messages:
        if m.get("role") == "user":
            c = str(m.get("content", ""))
            if "<tool_response>" in c or "<tool_reponse>" in c:
                m["role"] = "tool"
                stats["F1_role_fixed"] += 1
    return messages


# ─── F2: Fix typo tool_reponse ────────────────────────────────

def fix_typos(messages):
    """Corrige <tool_reponse> → <tool_response>."""
    for m in messages:
        c = str(m.get("content", ""))
        if "<tool_reponse>" in c:
            m["content"] = c.replace(
                "<tool_reponse>", "<tool_response>"
            ).replace(
                "</tool_reponse>", "</tool_response>"
            )
            stats["F2_typo_fixed"] += 1
    return messages


# ─── F3: Wrap bare JSON en tool messages ──────────────────────

def fix_bare_tool_responses(messages):
    """Envuelve JSON crudo en <tool_response> tags."""
    for m in messages:
        if m.get("role") == "tool":
            c = str(m.get("content", "")).strip()
            if not c.startswith("<tool_response>") and (
                c.startswith("{") or c.startswith("[")
            ):
                m["content"] = f"<tool_response>\n{c}\n</tool_response>"
                stats["F3_bare_wrapped"] += 1
    return messages


# ─── F4: TREFABOT → Mariana ──────────────────────────────────

def fix_trefabot(messages):
    """Reemplaza cualquier referencia a TREFABOT con Mariana."""
    for m in messages:
        c = str(m.get("content", ""))
        if re.search(r'trefabot', c, re.IGNORECASE):
            new_c = re.sub(r'TREFABOT[-\s]?v\d+', 'Mariana', c, flags=re.IGNORECASE)
            new_c = re.sub(r'TREFABOT', 'Mariana', new_c, flags=re.IGNORECASE)
            if new_c != c:
                m["content"] = new_c
                stats["F4_trefabot_replaced"] += 1
    return messages


# ─── F5: Inyectar identidad Mariana ──────────────────────────

def inject_mariana_in_text(text):
    """
    Inserta 'Soy Mariana de Autos TREFA' en el texto.
    Retorna (nuevo_texto, was_modified).
    """
    if "mariana" in text.lower():
        return text, False

    # Buscar greeting al inicio: ¡Hola, Carlos! / ¡Hola! / Hola,
    m = re.match(r'(^[¡!]?\s*[Hh]ola\b.*?[!.]\s*)', text)
    if m and len(m.group(1)) < 60:
        # Insertar identidad después del greeting
        pos = m.end()
        return text[:pos] + "Soy Mariana de Autos TREFA. " + text[pos:], True

    # Greeting muy largo o sin greeting: limpiar "Hola" plano y prefijar
    stripped = re.sub(r'^[¡!]?\s*[Hh]ola[,.\s]*', '', text).lstrip()
    if stripped and stripped[0].islower():
        stripped = stripped[0].upper() + stripped[1:]
    if not stripped:
        stripped = text
    return "¡Hola! Soy Mariana de Autos TREFA. " + stripped, True


def fix_identity(messages):
    """Asegura que el primer assistant text menciona 'Mariana'."""
    for m in messages:
        if m.get("role") != "assistant":
            continue

        content = str(m.get("content", ""))
        text = extract_text_without_tc(content)
        if not text:
            continue  # Solo tool_call, saltar

        if "mariana" in content.lower():
            return messages  # Ya tiene identidad

        # Encontrar la primera parte de texto en el content
        parts = split_by_tool_calls(content)
        for p_idx, part in enumerate(parts):
            if part.strip().startswith("<tool_call>"):
                continue
            stripped = part.strip()
            if not stripped:
                continue

            # Inyectar identidad en esta parte
            new_text, modified = inject_mariana_in_text(stripped)
            if modified:
                leading_ws = part[:len(part) - len(part.lstrip())]
                parts[p_idx] = leading_ws + new_text
                stats["F5_identity_injected"] += 1

            break  # Solo la primera parte de texto

        m["content"] = "".join(parts)
        return messages

    return messages


# ─── F6: Eliminar doble Hola ─────────────────────────────────

def fix_double_hola(messages):
    """
    Elimina saludos duplicados en assistant messages consecutivos
    (del mismo turno, sin user messages entre ellos).
    """
    # Identificar assistant messages con texto visible
    asst_text_indices = []
    for i, m in enumerate(messages):
        if m.get("role") == "assistant":
            text = extract_text_without_tc(str(m.get("content", "")))
            if text:
                asst_text_indices.append(i)

    to_remove = set()
    to_strip = set()
    handled = set()

    for k in range(len(asst_text_indices) - 1):
        i = asst_text_indices[k]
        j = asst_text_indices[k + 1]

        if i in handled or j in handled:
            continue

        # Solo si no hay user messages entre ellos (mismo turno)
        has_user_between = any(
            messages[x].get("role") == "user"
            for x in range(i + 1, j)
        )
        if has_user_between:
            continue

        curr_content = str(messages[i].get("content", ""))
        next_content = str(messages[j].get("content", ""))

        curr_text = extract_text_without_tc(curr_content)
        next_text = extract_text_without_tc(next_content)

        if has_hola(curr_text) and has_hola(next_text):
            has_tc_curr = "<tool_call>" in curr_content

            # Si el primero es un greeting corto sin tool_call → remover
            if len(curr_text) < 120 and not has_tc_curr:
                to_remove.add(i)
                handled.add(i)
            else:
                # Mantener primero, strip Hola del segundo
                to_strip.add(j)
                handled.add(j)

    # Aplicar strips primero (no cambia índices)
    for j in to_strip:
        content = str(messages[j].get("content", ""))
        parts = split_by_tool_calls(content)
        for p_idx, part in enumerate(parts):
            if part.strip().startswith("<tool_call>"):
                continue
            stripped = part.strip()
            if not stripped:
                continue

            # Remover patrón Hola del inicio
            new_stripped = re.sub(
                r'^[¡!]?\s*[Hh]ola\b[!,.:;\s]*'
                r'(?:[Ss]oy\s+Mariana\s+de\s+Autos\s+TREFA[.,!]?\s*)?',
                '', stripped
            ).lstrip()

            if new_stripped and new_stripped[0].islower():
                new_stripped = new_stripped[0].upper() + new_stripped[1:]

            if new_stripped and new_stripped != stripped:
                leading_ws = part[:len(part) - len(part.lstrip())]
                parts[p_idx] = leading_ws + new_stripped
                stats["F6_hola_stripped"] += 1
            break

        messages[j]["content"] = "".join(parts)

    # Aplicar removals (orden reverso)
    for i in sorted(to_remove, reverse=True):
        messages.pop(i)
        stats["F6_greeting_removed"] += 1

    return messages


# ─── F7: Fix endings ─────────────────────────────────────────

def fix_ending(messages):
    """Asegura que Mariana (assistant) cierra la conversación con texto real."""
    changed = False

    while True:
        if not messages:
            break

        last = messages[-1]
        role = last.get("role", "")

        if role == "assistant":
            content = str(last.get("content", "")).strip()
            text = extract_text_without_tc(content)
            # Remover si vacío, "...", o muy corto
            if not text or text in ("...", ".. .", "..", ".", "") or len(text) < 5:
                messages.pop()
                stats["F7_removed_empty_asst"] += 1
                changed = True
                continue
            break  # Buen ending
        else:
            messages.pop()
            stats[f"F7_removed_trailing_{role}"] += 1
            changed = True

    if changed:
        stats["F7_endings_fixed"] += 1

    return messages


# ─── F8: Drop comparar_vehiculos con < 2 IDs ────────────────

def has_bad_comparar(messages):
    """Detecta comparar_vehiculos con menos de 2 vehiculo_ids."""
    for m in messages:
        if m.get("role") != "assistant":
            continue
        content = str(m.get("content", ""))
        if "comparar_vehiculos" not in content:
            continue

        for match in re.finditer(
            r'<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>', content
        ):
            try:
                tc = json.loads(match.group(1))
                if tc.get("name") == "comparar_vehiculos":
                    ids = tc.get("arguments", {}).get("vehiculo_ids", [])
                    if not isinstance(ids, list) or len(ids) < 2:
                        return True
            except json.JSONDecodeError:
                pass
    return False


# ─── F9: solicitar_datos_contacto — telefono no es required ──

def fix_solicitar_telefono(messages):
    """
    En WhatsApp ya tenemos el teléfono. Si solicitar_datos_contacto
    se llama sin nombre (args vacío o solo comentarios), eso sí es
    un problema — pero telefono faltante no lo es.
    Aquí no hacemos nada destructivo; esta función es un no-op
    porque decidimos que telefono no es required.
    Solo registra stats para el reporte.
    """
    for m in messages:
        if m.get("role") != "assistant":
            continue
        content = str(m.get("content", ""))
        if "solicitar_datos_contacto" not in content:
            continue

        for match in re.finditer(
            r'<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>', content
        ):
            try:
                tc = json.loads(match.group(1))
                if tc.get("name") == "solicitar_datos_contacto":
                    args = tc.get("arguments", {})
                    if "telefono" not in args and "nombre" in args:
                        stats["F9_telefono_missing_ok"] += 1
                    elif not args or ("nombre" not in args and "telefono" not in args):
                        stats["F9_contacto_empty_args"] += 1
            except json.JSONDecodeError:
                pass
    return messages


# ─── Verificación post-fix ────────────────────────────────────

def verify_conversation(idx, messages):
    """Verifica una conversación post-fix. Retorna lista de issues."""
    issues = []

    if not messages:
        issues.append("empty")
        return issues

    # Check 1: Last message is assistant
    if messages[-1].get("role") != "assistant":
        issues.append(f"ends_with_{messages[-1].get('role')}")

    # Check 2: First assistant text mentions Mariana
    for m in messages:
        if m.get("role") != "assistant":
            continue
        text = extract_text_without_tc(str(m.get("content", "")))
        if text:
            if "mariana" not in text.lower():
                issues.append("no_mariana_identity")
            break

    # Check 3: Double Hola in consecutive assistant messages
    asst_texts = []
    for i, m in enumerate(messages):
        if m.get("role") == "assistant":
            text = extract_text_without_tc(str(m.get("content", "")))
            if text:
                asst_texts.append((i, text))

    for k in range(len(asst_texts) - 1):
        i, curr_text = asst_texts[k]
        j, next_text = asst_texts[k + 1]

        has_user_between = any(
            messages[x].get("role") == "user"
            for x in range(i + 1, j)
        )
        if not has_user_between and has_hola(curr_text) and has_hola(next_text):
            issues.append(f"double_hola_at_{i}_{j}")

    # Check 4: tool_response with wrong role
    for m in messages:
        if m.get("role") == "user" and "<tool_response>" in str(m.get("content", "")):
            issues.append("tool_response_in_user_role")
            break

    return issues


# ─── Procesamiento principal ──────────────────────────────────

def process_file(filepath, dry_run=False):
    """Procesa un archivo JSONL aplicando todos los fixes."""
    print(f"\n{'═' * 70}")
    print(f"  Procesando: {os.path.basename(filepath)}")
    print(f"{'═' * 70}")

    convos = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    convos.append(json.loads(line))
                except json.JSONDecodeError:
                    stats["load_error"] += 1

    total = len(convos)
    print(f"  Cargadas: {total}")

    results = []
    dropped = 0
    post_issues = Counter()

    for idx, conv in enumerate(convos):
        msgs = conv.get("messages", [])
        if not msgs:
            dropped += 1
            stats["dropped_empty"] += 1
            continue

        msgs = deepcopy(msgs)

        # F8: Drop bad comparar_vehiculos
        if has_bad_comparar(msgs):
            stats["F8_dropped_comparar"] += 1
            dropped += 1
            continue

        # Aplicar fixes en orden
        msgs = fix_roles(msgs)              # F1
        msgs = fix_typos(msgs)              # F2
        msgs = fix_bare_tool_responses(msgs)  # F3
        msgs = fix_trefabot(msgs)           # F4
        msgs = fix_identity(msgs)           # F5 (primer paso)
        msgs = fix_double_hola(msgs)        # F6
        msgs = fix_identity(msgs)           # F5 (segundo paso — por si F6 removió el msg con identidad)
        msgs = fix_ending(msgs)             # F7
        msgs = fix_solicitar_telefono(msgs)  # F9 (solo stats)

        # Validar post-fix
        if len(msgs) < 3:
            stats["dropped_too_short"] += 1
            dropped += 1
            continue

        roles = {m.get("role") for m in msgs}
        if "user" not in roles or "assistant" not in roles:
            stats["dropped_missing_roles"] += 1
            dropped += 1
            continue

        if msgs[-1].get("role") != "assistant":
            stats["dropped_bad_ending_still"] += 1
            dropped += 1
            continue

        # Verificación
        issues = verify_conversation(idx, msgs)
        for issue in issues:
            post_issues[issue] += 1

        conv["messages"] = msgs
        results.append(conv)

    # Escribir
    if dry_run:
        print(f"\n  DRY-RUN: {len(results)} conversaciones OK, {dropped} eliminadas")
    else:
        backup = filepath + ".pre_quality_fix"
        if not os.path.exists(backup):
            os.rename(filepath, backup)
            print(f"  Backup: {os.path.basename(backup)}")
        else:
            print(f"  Backup ya existe: {os.path.basename(backup)}")

        with open(filepath, "w", encoding="utf-8") as f:
            for item in results:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"  Resultado: {len(results)} conversaciones ({dropped} eliminadas)")

    # Reporte de verificación post-fix
    if post_issues:
        print(f"\n  {'─' * 50}")
        print(f"  ISSUES RESIDUALES POST-FIX:")
        print(f"  {'─' * 50}")
        for issue, count in post_issues.most_common():
            print(f"    {issue:<40} {count:>5}")
    else:
        print(f"\n  ✓ Verificación post-fix: 0 issues")

    return total, len(results), dropped


# ─── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Correcciones de calidad al dataset merged v10"
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo reportar")
    args = parser.parse_args()

    print("=" * 70)
    print("  fix_merged_quality.py — Correcciones de calidad post-auditoría")
    print("=" * 70)
    print("  Nota: telefono NO es required en solicitar_datos_contacto (WhatsApp)")

    files = [
        os.path.join(BASE, "merged_v10_together_train.jsonl"),
        os.path.join(BASE, "merged_v10_together_eval.jsonl"),
    ]

    grand_total = 0
    grand_kept = 0
    grand_dropped = 0

    for f in files:
        if os.path.exists(f):
            t, k, d = process_file(f, dry_run=args.dry_run)
            grand_total += t
            grand_kept += k
            grand_dropped += d
        else:
            print(f"\n  ⚠ No encontrado: {os.path.basename(f)}")

    print(f"\n{'═' * 70}")
    print("  ESTADÍSTICAS DE FIXES")
    print(f"{'═' * 70}")
    for key, val in sorted(stats.items()):
        print(f"  {key:<45} {val:>6}")

    print(f"\n{'═' * 70}")
    print("  RESUMEN FINAL")
    print(f"{'═' * 70}")
    print(f"  Total procesadas:      {grand_total}")
    print(f"  Conservadas:           {grand_kept}")
    print(f"  Eliminadas:            {grand_dropped}")
    print()


if __name__ == "__main__":
    main()
