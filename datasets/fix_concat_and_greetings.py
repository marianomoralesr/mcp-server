#!/usr/bin/env python3
"""
fix_concat_and_greetings.py
============================
1. Concatenation: Separa año pegado al modelo en tool_calls de buscar_vehiculos
   "CX-32019" → modelo="CX-3", año_minimo=2019, año_maximo=2019
2. Doble saludo: Elimina primer assistant msg cuando hay 2 assistant consecutivos
   (MSG1 es siempre un saludo corto sin contenido sustantivo)
3. Marca-concat: Elimina convos donde marca tiene todo concatenado (irrecuperables)

Uso:
    python3 fix_concat_and_greetings.py              # dry-run
    python3 fix_concat_and_greetings.py --apply       # aplica cambios
    python3 fix_concat_and_greetings.py --apply --merge  # aplica + regenera merge
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter

BASE = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets"

# Todos los archivos fuente
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

# ─── Modelos numéricos por marca (ordenados de más largo a más corto) ────

NUMERIC_MODELS = {
    "Mazda": ["CX-90", "CX-50", "CX-30", "MX-5", "CX-9", "CX-5", "CX-3", "6", "3", "2"],
    "Peugeot": ["Partner", "5008", "3008", "2008", "508", "408", "308", "301", "208"],
    "Ram": ["ProMaster", "3500", "2500", "1500", "700"],
    "Fiat": ["500X", "500L", "500"],
    "BMW": ["X7", "X6", "X5", "X4", "X3", "X2", "X1",
            "840", "740", "730", "640", "530", "520",
            "430", "420", "340", "330", "320", "318",
            "240", "230", "218", "118"],
    "Mercedes-Benz": ["GLS", "GLE", "GLC", "GLA", "GLB",
                      "CLS", "CLA",
                      "E450", "E350", "E300", "C300", "C200",
                      "A250", "A200", "A180"],
    "Lexus": ["NX", "RX", "UX", "IS", "ES", "LS"],
    "Audi": ["Q8", "Q7", "Q5", "Q3", "Q2",
             "A7", "A6", "A5", "A4", "A3", "A1"],
    "Infiniti": ["QX80", "QX60", "QX55", "QX50", "Q60", "Q50"],
}


def extract_year_from_modelo(modelo_str, marca=None):
    """Extrae año del final de modelo. Retorna (modelo_limpio, año) o (original, None)."""
    if not modelo_str or not isinstance(modelo_str, str):
        return modelo_str, None

    # Verificar si termina en 4 dígitos que sean un año válido
    match = re.search(r"(\d{4})$", modelo_str.strip())
    if not match:
        return modelo_str, None

    potential_year = int(match.group(1))
    if not (2005 <= potential_year <= 2026):
        return modelo_str, None

    before_year = modelo_str[: match.start()].strip()

    # Si no queda nada antes del año, el modelo entero era solo el año → no tocar
    if not before_year:
        return modelo_str, None

    # ── Manejo especial para marcas con modelos numéricos ──
    if marca and marca in NUMERIC_MODELS:
        for known in NUMERIC_MODELS[marca]:
            kl = known.lower().replace("-", "").replace(" ", "")
            bl = before_year.lower().replace("-", "").replace(" ", "")
            if bl.startswith(kl):
                trim_part = before_year[len(known) :].strip()
                # Puede ser que el match sea parcial (ej: "CX" de "CX-3" matchea "CX-30")
                # Verificar que el resto no empiece con dígito que sea parte del modelo
                if trim_part and trim_part[0].isdigit():
                    continue
                if trim_part:
                    trim_part = re.sub(r"(?<=[a-z\d])(?=[A-Z])", " ", trim_part)
                    clean = f"{known} {trim_part}".strip()
                else:
                    clean = known
                return clean, potential_year

    # ── Caso general: separar CamelCase ──
    clean = re.sub(r"(?<=[a-z\d])(?=[A-Z])", " ", before_year)

    # Limpiar espacios múltiples
    clean = re.sub(r"\s+", " ", clean).strip()

    return clean, potential_year


def fix_tool_call_text(content):
    """Corrige <tool_call> JSON blocks en contenido de assistant."""
    changed = False

    def fix_match(m):
        nonlocal changed
        try:
            tc = json.loads(m.group(1))
        except json.JSONDecodeError:
            return m.group(0)

        if tc.get("name") != "buscar_vehiculos":
            return m.group(0)

        args = tc.get("arguments", {})
        modelo = args.get("modelo", "")
        marca = args.get("marca", "")

        if not modelo:
            return m.group(0)

        clean_modelo, year = extract_year_from_modelo(modelo, marca)

        if year is None:
            return m.group(0)

        has_year = "año_minimo" in args or "año_maximo" in args

        # Solo modificar si hay algo que arreglar
        if clean_modelo == modelo and has_year:
            return m.group(0)

        args["modelo"] = clean_modelo
        if not has_year:
            args["año_minimo"] = year
            args["año_maximo"] = year
        tc["arguments"] = args
        changed = True

        return f"<tool_call>{json.dumps(tc, ensure_ascii=False)}</tool_call>"

    fixed = re.sub(
        r"<tool_call>(.*?)</tool_call>", fix_match, content, flags=re.DOTALL
    )
    return fixed, changed


def fix_consecutive_assistants(msgs):
    """Elimina MSG1 cuando hay 2 assistant messages consecutivos."""
    fixed = []
    removed = 0
    i = 0
    while i < len(msgs):
        if (
            i + 1 < len(msgs)
            and msgs[i].get("role") == "assistant"
            and msgs[i + 1].get("role") == "assistant"
        ):
            # Saltar MSG1 (saludo redundante)
            removed += 1
            i += 1
        else:
            fixed.append(msgs[i])
        i += 1
    return fixed, removed


def has_marca_concatenation(msgs):
    """Detecta si marca tiene todo concatenado (modelo+trim+año). Irrecuperable."""
    for msg in msgs:
        content = str(msg.get("content", "") or "")
        for m in re.finditer(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL):
            try:
                tc = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if tc.get("name") != "buscar_vehiculos":
                continue
            args = tc.get("arguments", {})
            marca = args.get("marca", "")
            modelo = args.get("modelo", "")
            # Si marca tiene año concatenado Y no hay modelo separado → roto
            if marca and not modelo and re.search(r"\d{4}$", marca):
                yr = int(marca[-4:])
                if 2005 <= yr <= 2026 and len(marca) > 4:
                    return True
    return False


# ─── Process ─────────────────────────────────────────────────────

def process_file(filepath, dry_run=True):
    fname = os.path.basename(filepath)
    if not os.path.exists(filepath):
        return {"file": fname, "exists": False}

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total = 0
    concat_fixed = 0
    greetings_removed = 0
    marca_removed = 0
    kept_lines = []

    for raw_line in lines:
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

        # Eliminar convos con marca concatenada irrecuperable
        if has_marca_concatenation(msgs):
            marca_removed += 1
            continue

        # Corregir concatenación en tool_calls
        conv_fixed = False
        for msg in msgs:
            content = str(msg.get("content", "") or "")
            if "<tool_call>" in content:
                fixed_content, changed = fix_tool_call_text(content)
                if changed:
                    msg["content"] = fixed_content
                    conv_fixed = True
        if conv_fixed:
            concat_fixed += 1

        # Eliminar assistant duplicados consecutivos
        fixed_msgs, removed = fix_consecutive_assistants(msgs)
        if removed > 0:
            obj["messages"] = fixed_msgs
            greetings_removed += 1

        kept_lines.append(json.dumps(obj, ensure_ascii=False) + "\n")

    result = {
        "file": fname,
        "exists": True,
        "total": total,
        "kept": total - marca_removed,
        "concat_fixed": concat_fixed,
        "greetings_removed": greetings_removed,
        "marca_removed": marca_removed,
    }

    if not dry_run:
        with open(filepath, "w", encoding="utf-8") as f:
            for line in kept_lines:
                f.write(line if line.endswith("\n") else line + "\n")

    return result


# ─── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--dir", default=BASE)
    args = parser.parse_args()

    dry_run = not args.apply

    print("=" * 70)
    print("  fix_concat_and_greetings.py")
    print(f"  Modo: {'DRY-RUN' if dry_run else 'APLICANDO CAMBIOS'}")
    print("=" * 70)

    totals = Counter()

    for fname in SOURCE_FILES:
        fpath = os.path.join(args.dir, fname)
        result = process_file(fpath, dry_run=dry_run)

        if not result.get("exists"):
            print(f"\n  {fname}: NO ENCONTRADO")
            continue

        print(f"\n  {fname} ({result['total']} convos)")
        print(f"    Concatenación corregida:  {result['concat_fixed']}")
        print(f"    Doble saludo limpiado:    {result['greetings_removed']}")
        print(f"    Marca-concat removidas:   {result['marca_removed']}")
        print(f"    Conservadas:              {result['kept']}")

        for key in ("concat_fixed", "greetings_removed", "marca_removed",
                     "total", "kept"):
            totals[key] += result.get(key, 0)

    print(f"\n{'=' * 70}")
    print("  RESUMEN")
    print(f"{'=' * 70}")
    print(f"  Total procesadas:          {totals['total']}")
    print(f"  Concatenación corregida:   {totals['concat_fixed']}")
    print(f"  Doble saludo limpiado:     {totals['greetings_removed']}")
    print(f"  Marca-concat removidas:    {totals['marca_removed']}")
    print(f"  Conservadas:               {totals['kept']}")

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
