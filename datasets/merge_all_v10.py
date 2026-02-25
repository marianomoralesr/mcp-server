#!/usr/bin/env python3
"""
===============================================================
 merge_all_v10.py — Merge de todos los datasets válidos TREFA
===============================================================

Combina todos los JSONL de training/eval en un solo par de archivos
deduplicados, listos para fine-tuning.

Deduplicación por fingerprint de contenido (hash de mensajes user+assistant).

Prioridad (si hay duplicados, se conserva la versión del archivo con
mayor prioridad):
  1. v10_real     (datos reales más recientes)
  2. v8           (generación anterior consolidada)
  3. gold/semillas (curated, alta calidad)
  4. trefa        (generación intermedia)
  5. synthetic    (generados con IA)
  6. otros

Uso:
    python3 merge_all_v10.py
    python3 merge_all_v10.py --dry-run
===============================================================
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets"

# ─── Definición de fuentes ────────────────────────────────────

# Fuentes de TRAINING (orden = prioridad descendente)
TRAIN_SOURCES = [
    # Prioridad 1: datos reales v10
    ("v10_real_train.jsonl", 1),
    # Prioridad 2: v8 consolidado
    ("v8_train.jsonl", 2),
    # Prioridad 3: gold y semillas (curated)
    ("etapa1_40_calidad_gold.jsonl", 3),
    ("golden_qwen_mariana.jsonl", 3),
    ("semillas_mariana.jsonl", 3),
    ("semillas_obtener_vehiculo.jsonl", 3),
    ("semillas_solicitud_financiamiento.jsonl", 3),
    ("saludos_mariana.jsonl", 3),
    ("estilo_conversacional_mariana.jsonl", 3),
    ("mariana_training_v1.jsonl", 3),
    # Prioridad 4: trefa (puede solapar con v8)
    ("trefa_train.jsonl", 4),
    # Prioridad 5: synthetic
    ("synthetic_cleaned.jsonl", 5),
]

# Fuentes de EVAL
EVAL_SOURCES = [
    ("v10_real_eval.jsonl", 1),
    ("v8_eval.jsonl", 2),
    ("trefa_eval.jsonl", 4),
]

# ─── Fingerprinting ──────────────────────────────────────────


def conversation_fingerprint(messages):
    """
    Genera un fingerprint estable para una conversación.
    Usa los mensajes de usuario + el primer mensaje de assistant
    para identificar la conversación de forma única.
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            # Normalizar: quitar espacios extra, lowercase
            text = re.sub(r'\s+', ' ', str(content)).strip().lower()
            parts.append(f"U:{text}")
        elif role == "assistant" and not parts or (parts and parts[-1].startswith("U:")):
            # Primer response del assistant después de cada user msg
            text = str(content)[:200].strip().lower()
            text = re.sub(r'\s+', ' ', text)
            parts.append(f"A:{text}")

    # Si no hay mensajes user, usar todo el content
    if not parts:
        full = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        parts.append(full[:500])

    combined = "|".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def validate_conversation(obj):
    """Valida que una conversación tiene el formato correcto."""
    if not isinstance(obj, dict):
        return False, "no es dict"
    messages = obj.get("messages")
    if not isinstance(messages, list):
        return False, "sin messages"
    if len(messages) < 2:
        return False, f"solo {len(messages)} mensaje(s)"

    roles = [m.get("role") for m in messages]

    # Debe tener al menos un user y un assistant
    if "user" not in roles:
        return False, "sin role=user"
    if "assistant" not in roles:
        return False, "sin role=assistant"

    # Verificar que los mensajes tienen content
    for m in messages:
        if m.get("role") in ("user", "assistant"):
            content = m.get("content", "")
            if not content or not str(content).strip():
                return False, f"content vacío en role={m.get('role')}"

    return True, "ok"


# ─── Main merge ───────────────────────────────────────────────


def load_source(filepath, priority, stats):
    """Carga un archivo JSONL y devuelve lista de (fingerprint, obj, priority)."""
    results = []
    if not os.path.exists(filepath):
        stats["files_missing"] += 1
        return results

    with open(filepath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                stats["json_errors"] += 1
                continue

            valid, reason = validate_conversation(obj)
            if not valid:
                stats["invalid_convos"] += 1
                stats[f"invalid:{reason}"] += 1
                continue

            fp = conversation_fingerprint(obj["messages"])
            results.append((fp, obj, priority))

    return results


def merge_sources(sources, base_dir, stats):
    """
    Merge múltiples fuentes con deduplicación.
    Si un fingerprint aparece en múltiples fuentes, se conserva
    la versión con menor número de prioridad (mayor prioridad).
    """
    # fp → (obj, priority, source_name)
    merged = {}

    for filename, priority in sources:
        filepath = os.path.join(base_dir, filename)
        entries = load_source(filepath, priority, stats)

        source_new = 0
        source_dup = 0
        source_upgraded = 0

        for fp, obj, prio in entries:
            if fp not in merged:
                merged[fp] = (obj, prio, filename)
                source_new += 1
            else:
                existing_prio = merged[fp][1]
                if prio < existing_prio:
                    # Reemplazar con versión de mayor prioridad
                    merged[fp] = (obj, prio, filename)
                    source_upgraded += 1
                else:
                    source_dup += 1

        total = len(entries)
        stats[f"src:{filename}"] = total
        if total > 0:
            print(f"  {filename:<50} {total:>5} ({source_new} new, {source_dup} dup, {source_upgraded} upgraded)")

    return merged


def main():
    parser = argparse.ArgumentParser(description="Merge todos los datasets TREFA válidos")
    parser.add_argument("--dry-run", action="store_true", help="Solo reportar, no escribir")
    parser.add_argument("--dir", type=str, default=BASE, help="Directorio base")
    parser.add_argument("--output-train", type=str, default=None,
                        help="Archivo de salida train (default: merged_v10_together_train.jsonl)")
    parser.add_argument("--output-eval", type=str, default=None,
                        help="Archivo de salida eval (default: merged_v10_together_eval.jsonl)")
    args = parser.parse_args()

    base_dir = args.dir
    out_train = args.output_train or os.path.join(base_dir, "merged_v10_together_train.jsonl")
    out_eval = args.output_eval or os.path.join(base_dir, "merged_v10_together_eval.jsonl")

    print("=" * 80)
    print("  merge_all_v10.py — Merge de datasets TREFA")
    print("=" * 80)

    # --- Merge TRAIN ---
    print(f"\n{'─' * 60}")
    print("  TRAINING SOURCES")
    print(f"{'─' * 60}")

    train_stats = Counter()
    train_merged = merge_sources(TRAIN_SOURCES, base_dir, train_stats)

    # --- Merge EVAL ---
    print(f"\n{'─' * 60}")
    print("  EVAL SOURCES")
    print(f"{'─' * 60}")

    eval_stats = Counter()
    eval_merged = merge_sources(EVAL_SOURCES, base_dir, eval_stats)

    # --- Eliminar del train cualquier conversación que esté en eval ---
    overlap = set(train_merged.keys()) & set(eval_merged.keys())
    if overlap:
        for fp in overlap:
            del train_merged[fp]
        print(f"\n  Removed {len(overlap)} train conversations that overlap with eval")

    # --- Extraer objetos finales ---
    train_items = [obj for obj, prio, src in train_merged.values()]
    eval_items = [obj for obj, prio, src in eval_merged.values()]

    # --- Estadísticas de contenido ---
    train_with_tools = sum(
        1 for item in train_items
        if any("<tool_call>" in str(m.get("content", ""))
               for m in item["messages"] if m.get("role") == "assistant")
    )
    eval_with_tools = sum(
        1 for item in eval_items
        if any("<tool_call>" in str(m.get("content", ""))
               for m in item["messages"] if m.get("role") == "assistant")
    )

    train_avg_msgs = sum(len(item["messages"]) for item in train_items) / max(len(train_items), 1)
    eval_avg_msgs = sum(len(item["messages"]) for item in eval_items) / max(len(eval_items), 1)

    # --- Source breakdown ---
    print(f"\n{'─' * 60}")
    print("  SOURCE BREAKDOWN (train)")
    print(f"{'─' * 60}")
    src_counts = Counter()
    for fp, (obj, prio, src) in train_merged.items():
        src_counts[src] += 1
    for src, count in src_counts.most_common():
        print(f"  {src:<50} {count:>5}")

    # --- Resumen ---
    print(f"\n{'=' * 80}")
    print("  RESUMEN DEL MERGE")
    print(f"{'=' * 80}")
    print(f"  TRAIN:")
    print(f"    Conversaciones:         {len(train_items)}")
    print(f"    Con tool calls:         {train_with_tools} ({100*train_with_tools/max(len(train_items),1):.1f}%)")
    print(f"    Promedio msgs/convo:    {train_avg_msgs:.1f}")
    print(f"    Convos inválidas:       {train_stats['invalid_convos']}")
    print(f"  EVAL:")
    print(f"    Conversaciones:         {len(eval_items)}")
    print(f"    Con tool calls:         {eval_with_tools} ({100*eval_with_tools/max(len(eval_items),1):.1f}%)")
    print(f"    Promedio msgs/convo:    {eval_avg_msgs:.1f}")
    print(f"    Convos inválidas:       {eval_stats['invalid_convos']}")
    print(f"  Train/Eval overlap:       {len(overlap)} (removidos del train)")

    # --- Escribir ---
    if args.dry_run:
        print(f"\n  MODO DRY-RUN: no se escribieron archivos")
    else:
        # Backup de archivos existentes
        for out_path in [out_train, out_eval]:
            if os.path.exists(out_path):
                backup = out_path + ".pre_merge"
                if not os.path.exists(backup):
                    os.rename(out_path, backup)
                    print(f"\n  Backup: {os.path.basename(backup)}")

        # Escribir train
        with open(out_train, "w", encoding="utf-8") as f:
            for item in train_items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"\n  Escrito: {os.path.basename(out_train)} ({len(train_items)} conversaciones)")

        # Escribir eval
        with open(out_eval, "w", encoding="utf-8") as f:
            for item in eval_items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"  Escrito: {os.path.basename(out_eval)} ({len(eval_items)} conversaciones)")

    # --- Verificación rápida post-merge ---
    print(f"\n{'=' * 80}")
    print("  VERIFICACIÓN")
    print(f"{'=' * 80}")

    # Check a few random convos from each
    for label, items in [("TRAIN", train_items), ("EVAL", eval_items)]:
        roles_ok = 0
        for item in items:
            roles = [m.get("role") for m in item["messages"]]
            if "system" in roles and "user" in roles and "assistant" in roles:
                roles_ok += 1
        print(f"  {label}: {roles_ok}/{len(items)} tienen system+user+assistant ({100*roles_ok/max(len(items),1):.1f}%)")

    print(f"\n  Archivos:")
    print(f"    {out_train}")
    print(f"    {out_eval}")
    print()


if __name__ == "__main__":
    main()
