#!/usr/bin/env python3
"""
fix_final_issues.py — Corrige URLs, saludos faltantes y conversaciones incompletas
===================================================================================

1. URLs: Reemplaza dominios incorrectos (autostrefa.mx, trefa.mx, trefa.com,
   autostrefa.com.mx) → autostrefa.com
2. Saludos: Inyecta saludo antes de <tool_call> cuando el primer mensaje del
   asistente no tiene texto humano
3. User-last: Elimina conversaciones donde el último mensaje es del usuario
4. Empty URLs: Elimina conversaciones con liga_web/liga_bot vacíos en tool responses

Uso:
    python3 fix_final_issues.py              # dry-run
    python3 fix_final_issues.py --apply      # aplica cambios
    python3 fix_final_issues.py --apply --merge  # aplica + regenera merge
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
from collections import Counter

BASE = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets"

SOURCE_FILES = [
    "v10_real_train.jsonl",
    "v10_real_eval.jsonl",
    "v8_train.jsonl",
    "v8_eval.jsonl",
    "trefa_train.jsonl",
    "trefa_eval.jsonl",
    "mariana_training_v1.jsonl",
    "golden_qwen_mariana.jsonl",
    "golden_system_prompts.jsonl",
    "semillas_mariana.jsonl",
    "semillas_obtener_vehiculo.jsonl",
    "semillas_solicitud_financiamiento.jsonl",
    "saludos_mariana.jsonl",
    "estilo_conversacional_mariana.jsonl",
    "etapa1_40_calidad_gold.jsonl",
    "synthetic_cleaned.jsonl",
]

# ─── URL Fix ────────────────────────────────────────────────────

# Dominios incorrectos → correcto
DOMAIN_REPLACEMENTS = [
    ("autostrefa.com.mx", "autostrefa.com"),
    ("autostrefa.mx", "autostrefa.com"),
    ("www.trefa.mx", "autostrefa.com"),
    ("trefa.mx", "autostrefa.com"),
    ("trefa.com.mx", "autostrefa.com"),
    # trefa.com solo cuando es dominio (no parte de autostrefa.com)
]

def fix_urls_in_text(text):
    """Reemplaza todos los dominios incorrectos en un string."""
    fixed = text
    for wrong, correct in DOMAIN_REPLACEMENTS:
        fixed = fixed.replace(wrong, correct)
    # Fix trefa.com pero no autostrefa.com
    fixed = re.sub(r'(?<!autos)trefa\.com', 'autostrefa.com', fixed)
    return fixed


def fix_urls_in_conversation(obj):
    """Aplica fix de URLs a todos los mensajes de una conversación."""
    changed = False
    msgs = obj.get("messages", [])
    for msg in msgs:
        content = msg.get("content")
        if content and isinstance(content, str):
            fixed = fix_urls_in_text(content)
            if fixed != content:
                msg["content"] = fixed
                changed = True
    return changed


# ─── Greeting Fix ───────────────────────────────────────────────

GREETINGS_SEARCH = [
    "¡Hola! 😊 Déjame revisar qué opciones tenemos para ti.",
    "¡Hola! Qué gusto saludarte 😊 Voy a buscar lo que necesitas.",
    "¡Hola! Bienvenid@ a Autos TREFA 🚗 Déjame buscar eso para ti.",
    "¡Hola! Con mucho gusto te ayudo 😊 Voy a revisar nuestro inventario.",
    "¡Hola! Qué bueno que nos contactas 😊 Déjame checarlo.",
]

GREETINGS_INFO = [
    "¡Hola! 😊 Déjame revisar esa información para ti.",
    "¡Hola! Con gusto te ayudo 😊 Voy a consultar los detalles.",
    "¡Hola! Bienvenid@ a Autos TREFA 🚗 Déjame buscar esa info.",
]

GREETINGS_GENERIC = [
    "¡Hola! 😊 Déjame revisarlo.",
    "¡Hola! Con mucho gusto te ayudo 😊",
    "¡Hola! Bienvenid@ 🚗 Déjame checar eso.",
]


def needs_greeting_fix(msgs):
    """Verifica si el primer mensaje del asistente necesita saludo."""
    for msg in msgs:
        if msg.get("role") == "assistant":
            content = str(msg.get("content", "") or "")
            # Si empieza con <tool_call> sin texto previo
            stripped = content.strip()
            if stripped.startswith("<tool_call>"):
                return True
            # Si el content es None (tool_calls nativo) sin texto
            if not content.strip() and msg.get("tool_calls"):
                return True
            return False
    return False


def inject_greeting(msgs):
    """Inyecta un saludo apropiado antes del primer <tool_call>."""
    for msg in msgs:
        if msg.get("role") != "assistant":
            continue

        content = str(msg.get("content", "") or "").strip()
        if not content.startswith("<tool_call>"):
            return False

        # Detectar qué tipo de tool se llama para elegir saludo apropiado
        tool_match = re.search(r'"name":\s*"(\w+)"', content)
        tool_name = tool_match.group(1) if tool_match else ""

        if tool_name in ("buscar_vehiculos", "buscar_alternativas",
                         "obtener_vehiculo", "comparar_vehiculos",
                         "estadisticas_inventario"):
            greeting = random.choice(GREETINGS_SEARCH)
        elif tool_name in ("obtener_info_negocio", "buscar_informacion",
                           "obtener_faqs", "calcular_financiamiento"):
            greeting = random.choice(GREETINGS_INFO)
        else:
            greeting = random.choice(GREETINGS_GENERIC)

        msg["content"] = f"{greeting}\n\n{content}"
        return True

    return False


# ─── User-last fix ──────────────────────────────────────────────

def conversation_ends_with_user(msgs):
    """Verifica si el último mensaje es del usuario."""
    if not msgs:
        return False
    return msgs[-1].get("role") == "user"


# ─── Empty URL fix ──────────────────────────────────────────────

def has_empty_liga(msgs):
    """Detecta si hay campos liga_web/liga_bot vacíos en tool responses
    Y el asistente referencia un enlace."""
    has_empty = False
    assistant_promises_link = False

    for msg in msgs:
        content = str(msg.get("content", "") or "")
        role = msg.get("role", "")

        if role in ("tool", "assistant"):
            empties = re.findall(r'"liga_(?:web|bot)":\s*""', content)
            if empties:
                has_empty = True

        if role == "assistant":
            if re.search(r'(?:aquí.*(?:link|liga|enlace)|(?:link|liga|enlace).*aquí)', content, re.IGNORECASE):
                if 'autostrefa.com' not in content:
                    assistant_promises_link = True

    return has_empty and assistant_promises_link


# ─── Process file ───────────────────────────────────────────────

def process_file(filepath, dry_run=True):
    fname = os.path.basename(filepath)

    if not os.path.exists(filepath):
        return {"file": fname, "exists": False}

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total = 0
    urls_fixed = 0
    greetings_fixed = 0
    user_last_removed = 0
    empty_liga_removed = 0
    kept_lines = []

    for line_num, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        if not stripped:
            kept_lines.append(raw_line)
            continue

        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            kept_lines.append(raw_line)
            continue

        msgs = obj.get("messages", [])
        if not msgs:
            kept_lines.append(raw_line)
            continue

        total += 1

        # Check: remove user-last conversations
        if conversation_ends_with_user(msgs):
            user_last_removed += 1
            continue

        # Check: remove conversations with empty liga + broken link promises
        if has_empty_liga(msgs):
            empty_liga_removed += 1
            continue

        # Fix URLs
        if fix_urls_in_conversation(obj):
            urls_fixed += 1

        # Fix greetings
        if needs_greeting_fix(msgs):
            if inject_greeting(msgs):
                greetings_fixed += 1

        kept_lines.append(json.dumps(obj, ensure_ascii=False) + "\n")

    result = {
        "file": fname,
        "exists": True,
        "total": total,
        "kept": total - user_last_removed - empty_liga_removed,
        "urls_fixed": urls_fixed,
        "greetings_fixed": greetings_fixed,
        "user_last_removed": user_last_removed,
        "empty_liga_removed": empty_liga_removed,
    }

    if not dry_run:
        with open(filepath, "w", encoding="utf-8") as f:
            for line in kept_lines:
                f.write(line if line.endswith("\n") else line + "\n")

    return result


# ─── Main ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--dir", default=BASE)
    args = parser.parse_args()

    dry_run = not args.apply

    print("=" * 70)
    print("  fix_final_issues.py — URLs + Saludos + User-last + Empty ligas")
    print(f"  Modo: {'DRY-RUN' if dry_run else 'APLICANDO CAMBIOS'}")
    print("=" * 70)

    totals = Counter()

    for fname in SOURCE_FILES:
        fpath = os.path.join(args.dir, fname)
        result = process_file(fpath, dry_run=dry_run)

        if not result.get("exists"):
            continue

        has_changes = (result["urls_fixed"] + result["greetings_fixed"] +
                       result["user_last_removed"] + result["empty_liga_removed"]) > 0

        if has_changes:
            print(f"\n  {fname} ({result['total']} convos)")
            if result["urls_fixed"]:
                print(f"    URLs corregidas: {result['urls_fixed']}")
            if result["greetings_fixed"]:
                print(f"    Saludos inyectados: {result['greetings_fixed']}")
            if result["user_last_removed"]:
                print(f"    User-last removidas: {result['user_last_removed']}")
            if result["empty_liga_removed"]:
                print(f"    Empty-liga removidas: {result['empty_liga_removed']}")
        else:
            print(f"\n  {fname}: sin cambios")

        for key in ("urls_fixed", "greetings_fixed", "user_last_removed",
                     "empty_liga_removed", "total", "kept"):
            totals[key] += result.get(key, 0)

    print(f"\n{'=' * 70}")
    print("  RESUMEN")
    print(f"{'=' * 70}")
    print(f"  Conversaciones procesadas: {totals['total']}")
    print(f"  URLs corregidas:          {totals['urls_fixed']}")
    print(f"  Saludos inyectados:       {totals['greetings_fixed']}")
    print(f"  User-last removidas:      {totals['user_last_removed']}")
    print(f"  Empty-liga removidas:     {totals['empty_liga_removed']}")
    print(f"  Conservadas:              {totals['kept']}")

    if dry_run:
        print(f"\n  Ejecuta con --apply para aplicar cambios")
    else:
        print(f"\n  Cambios aplicados a archivos fuente.")
        if args.merge:
            print(f"\n  Regenerando merge...")
            merge_script = os.path.join(args.dir, "merge_all_v10.py")
            subprocess.run([sys.executable, merge_script], cwd=args.dir)
            print(f"  Merge regenerado.")


if __name__ == "__main__":
    main()
