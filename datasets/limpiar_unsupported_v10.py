#!/usr/bin/env python3
"""
Limpia el texto "This message type can\u2019t be displayed because it\u2019s not supported yet."
de los datasets v10_real_train.jsonl y v10_real_eval.jsonl.

- Remueve el texto en ingles de mensajes de usuario
- Limpia whitespace/newlines duplicados resultantes
- Agrega respuesta de Mariana a las 2 conversaciones que terminan sin assistant (train L5, L822)
- Escribe archivos _clean primero, luego reemplaza originales tras validacion
"""

import json
import re
import shutil
from pathlib import Path

# Texto a eliminar (con apostrofes curvos unicode)
UNSUPPORTED_TEXT = "This message type can\u2019t be displayed because it\u2019s not supported yet."

# JSON-escaped version para reemplazo directo en lineas raw
UNSUPPORTED_JSON_ESCAPED = UNSUPPORTED_TEXT.replace("\u2019", "\\u2019")

# Respuestas para las 2 conversaciones que terminan en mensaje de usuario sin respuesta
RESPUESTAS_FALTANTES = {
    5: (
        "\u00a1Con mucho gusto! \U0001f60a Te voy a canalizar con uno de nuestros asesores "
        "para que puedan evaluar tu Dodge Journey 2022 y darte una oferta.\n\n"
        "\u00bfMe podr\u00edas compartir tu nombre completo y un n\u00famero de tel\u00e9fono "
        "para que se comuniquen contigo?"
    ),
    822: (
        "\u00a1Excelente! Gracias por los datos. Entonces tienes un auto modelo 2024 con "
        "13,560 km y transmisi\u00f3n est\u00e1ndar. \U0001f697\n\n"
        "\u00bfMe podr\u00edas decir la marca y el modelo exacto del veh\u00edculo? "
        "As\u00ed puedo orientarte mejor sobre las opciones de intercambio que tenemos disponibles."
    ),
}


def _clean_json_string(s: str) -> str:
    """Limpia newlines JSON duplicados (\\n\\n\\n -> \\n\\n) y espacios."""
    # En JSON los newlines estan escaped como \\n
    s = re.sub(r'(\\n){3,}', r'\\n\\n', s)
    return s


def process_file(input_path: str, output_path: str, line_responses: dict) -> dict:
    """Procesa un archivo JSONL trabajando directamente con bytes."""
    stats = {
        "total_lines": 0,
        "modified_lines": 0,
        "messages_cleaned": 0,
        "responses_added": 0,
    }

    # Variantes del texto como bytes para busqueda directa
    variants_bytes = [
        UNSUPPORTED_TEXT.encode("utf-8"),
        UNSUPPORTED_JSON_ESCAPED.encode("ascii"),
    ]

    with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
        for line_num, raw_line in enumerate(fin, 1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            stats["total_lines"] += 1

            modified = False
            data = stripped

            # Reemplazar texto unsupported directamente en bytes
            for variant in variants_bytes:
                occurrences = data.count(variant)
                if occurrences > 0:
                    data = data.replace(variant, b"")
                    stats["messages_cleaned"] += occurrences
                    modified = True

            # Limpiar newlines JSON triplicados resultantes
            if modified:
                data = re.sub(rb'(\\n){3,}', rb'\\n\\n', data)

            # Agregar respuesta de Mariana si falta
            if line_num in line_responses:
                response_text = line_responses[line_num]
                response_json = json.dumps(response_text, ensure_ascii=False)
                new_msg = f', {{"role": "assistant", "content": {response_json}}}'
                new_msg_bytes = new_msg.encode("utf-8")
                # Insertar ANTES del cierre ] del array messages
                # Buscar el ultimo }] en la linea (cierre del ultimo msg + cierre del array)
                idx = data.rfind(b"}]")
                if idx != -1:
                    # Insertar despues del } pero antes del ]
                    data = data[:idx + 1] + new_msg_bytes + data[idx + 1:]
                stats["responses_added"] += 1
                modified = True

            if modified:
                stats["modified_lines"] += 1

            fout.write(data + b"\n")

    return stats


def validate_clean(filepath: str) -> int:
    """Cuenta ocurrencias residuales del texto en ingles."""
    variants_bytes = [
        UNSUPPORTED_TEXT.encode("utf-8"),
        UNSUPPORTED_JSON_ESCAPED.encode("ascii"),
    ]
    count = 0
    with open(filepath, "rb") as f:
        for raw_line in f:
            for variant in variants_bytes:
                count += raw_line.count(variant)
    return count


def main():
    base = Path(__file__).parent

    files = [
        {
            "input": base / "v10_real_train.jsonl",
            "clean": base / "v10_real_train_clean.jsonl",
            "responses": RESPUESTAS_FALTANTES,
        },
        {
            "input": base / "v10_real_eval.jsonl",
            "clean": base / "v10_real_eval_clean.jsonl",
            "responses": {},
        },
    ]

    all_ok = True
    for f in files:
        print(f"\n{'='*60}")
        print(f"Procesando: {f['input'].name}")
        print(f"{'='*60}")

        if not f["input"].exists():
            print(f"  SKIP: archivo no existe")
            continue

        stats = process_file(str(f["input"]), str(f["clean"]), f["responses"])
        print(f"  Total lineas:        {stats['total_lines']}")
        print(f"  Lineas modificadas:  {stats['modified_lines']}")
        print(f"  Mensajes limpiados:  {stats['messages_cleaned']}")
        print(f"  Respuestas anadidas: {stats['responses_added']}")

        # Validar
        residual = validate_clean(str(f["clean"]))
        if residual > 0:
            print(f"  ERROR: {residual} ocurrencias residuales del texto en ingles")
            all_ok = False
        else:
            print(f"  OK: 0 ocurrencias residuales")

    if all_ok:
        print(f"\n{'='*60}")
        print("Validacion exitosa. Reemplazando originales...")
        print(f"{'='*60}")
        for f in files:
            if f["clean"].exists():
                # Backup
                backup = f["input"].with_suffix(".jsonl.pre_clean")
                shutil.copy2(str(f["input"]), str(backup))
                print(f"  Backup: {backup.name}")
                # Reemplazar
                shutil.move(str(f["clean"]), str(f["input"]))
                print(f"  Reemplazado: {f['input'].name}")
        print("\nLimpieza completada exitosamente.")
    else:
        print("\nERROR: No se reemplazan originales. Revisa los archivos _clean.")


if __name__ == "__main__":
    main()
