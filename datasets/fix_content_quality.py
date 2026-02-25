#!/usr/bin/env python3
"""
===============================================================
 fix_content_quality.py — Correcciones de contenido post-auditoría
===============================================================

Fixes:
  C1. Direcciones falsas → direcciones reales de TREFA
  C2. Teléfonos falsos → teléfonos reales
  C3. Sucursales falsas → nombres reales
  C4. URLs inválidas (ejemplo.com, YouTube, Airtable, Calendly)
  C5. Dominio trefa.mx → autostrefa.mx
  C6. /inventario/ → /autos/
  C7. autostrefa.com → autostrefa.mx
  C8. Raw field labels (traccion:, combustible:) → lenguaje natural

Uso:
    python3 fix_content_quality.py
    python3 fix_content_quality.py --dry-run
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


# ═══════════════════════════════════════════════════════════════
# DATOS REALES DE TREFA
# ═══════════════════════════════════════════════════════════════

REAL_LOCATIONS = {
    "san_jeronimo": {
        "nombre": "San Jerónimo",
        "direccion": "Aarón Sáenz Garza 1902, Plaza Oasis, Local 1109, Col. Santa María, Monterrey, NL",
        "telefono": "81 8704 9079",
        "maps": "https://www.google.com/maps/search/?api=1&query=Autos+TREFA+San+Jeronimo&query_place_id=ChIJqV4rrgGXYoYRYTb9nxnFfN8",
    },
    "guadalupe": {
        "nombre": "Guadalupe",
        "direccion": "Calle Hidalgo #918, Paraíso, Guadalupe, NL",
        "telefono": "81 8704 9079",
        "maps": "https://www.google.com/maps/search/?api=1&query=Autos+TREFA+Guadalupe&query_place_id=ChIJ63LrGeu9YoYRyze8cOoS62U",
    },
    "las_americas": {
        "nombre": "Las Américas",
        "direccion": "América del Norte 110, Col. Las Américas, Guadalupe, NL",
        "telefono": "81 8704 9079",
        "maps": "https://www.google.com/maps/search/?api=1&query=Autos+TREFA+Las+Americas&query_place_id=ChIJCUzAl9rrYoYRKEDQZUUbPSQ",
    },
    "saltillo": {
        "nombre": "Saltillo",
        "direccion": "Plaza Santa Isabel, Blvd. Nazario Ortiz #2060, Local 132, Saltillo, Coahuila",
        "telefono": "844 212 3399",
        "maps": "https://www.google.com/maps/search/?api=1&query=Autos+TREFA+Saltillo&query_place_id=ChIJYzGroKETiIYR50LyF0uC0VM",
    },
    "reynosa": {
        "nombre": "Reynosa",
        "direccion": "Blvd. Beethoven #100, Narciso Mendoza, Reynosa, Tamaulipas",
        "telefono": "899 460 2822",
        "maps": "https://www.google.com/maps/search/?api=1&query=Autos+TREFA+Reynosa&query_place_id=ChIJyaxfslkFZYYRIp9ElX9zCSM",
    },
}

REAL_PHONES = {"8187049079", "8442123399", "8994602822"}


# ═══════════════════════════════════════════════════════════════
# C1: REEMPLAZO DE DIRECCIONES FALSAS
# ═══════════════════════════════════════════════════════════════

# (patrón regex, reemplazo, ciudad destino)
ADDRESS_REPLACEMENTS = [
    # ── Monterrey / San Jerónimo ──
    # Gonzalitos con dirección completa
    (r'(?:Av\.?|Avenida)\s*Gonzalitos\s*#?\d*[^,.\n]*(?:,\s*(?:Col\.?\s*)?Mitras\s*Centro[^,.\n]*)?',
     REAL_LOCATIONS["san_jeronimo"]["direccion"]),
    # Gonzalitos sin Av. pero con número
    (r'\bGonzalitos\s+\d+[^,.\n]*(?:,\s*(?:Col\.?\s*)?Mitras[^,.\n]*)?',
     REAL_LOCATIONS["san_jeronimo"]["direccion"]),
    # Gonzalitos como referencia de sucursal (muchas variantes)
    (r'(?:en|sobre|por)\s+(?:la\s+)?(?:Av\.?\s*)?Gonzalitos\b',
     'en nuestra sucursal San Jerónimo'),
    (r'(?:sucursal\s+(?:de\s+)?(?:Monterrey\s*)?)\(?Gonzalitos\)?\b',
     'sucursal San Jerónimo'),
    (r'(?:la\s+de\s+(?:Monterrey\s*)?)\(?Gonzalitos\)?\b',
     'la de San Jerónimo'),
    (r'(?:zona\s+de\s+)Gonzalitos\b',
     'zona de San Jerónimo'),
    # Catch-all: cualquier "Gonzalitos" restante → "San Jerónimo"
    (r'\bGonzalitos\b', 'San Jerónimo'),
    # Garza Sada
    (r'(?:Av\.?|Avenida)\s*(?:Eugenio\s*)?Garza\s*Sada\s*#?\d*[^,.\n]*',
     REAL_LOCATIONS["san_jeronimo"]["direccion"]),
    # Morelos como dirección Monterrey
    (r'Blvd\.?\s*Morelos\s*#?\d+[^,.\n]*',
     REAL_LOCATIONS["san_jeronimo"]["direccion"]),

    # ── Guadalupe ──
    (r'(?:Av\.?|Avenida)\s*Benito\s*Ju[aá]rez\s*#?\d*[^,.\n]*(?:,\s*(?:Col\.?\s*)?Guadalupe\s*Centro[^,.\n]*)?',
     REAL_LOCATIONS["guadalupe"]["direccion"]),
    # Benito Juárez suelto como referencia a sucursal
    (r'(?:en|sobre|por)\s+(?:la\s+)?(?:Av\.?\s*)?Benito\s*Ju[aá]rez\b',
     'en nuestra sucursal Guadalupe'),
    (r'(?:sucursal\s+(?:de\s+)?(?:Guadalupe\s*)?)\(?Benito\s*Ju[aá]rez\)?\b',
     'sucursal Guadalupe'),
    # Catch-all Benito Juárez como dirección → Guadalupe
    (r'(?:Av\.?\s*)?Benito\s*Ju[aá]rez\s*\d+', REAL_LOCATIONS["guadalupe"]["direccion"]),
    (r'(?:Avenida|Av\.?)\s*Miguel\s*Alem[aá]n\s*#?\d*[^,.\n]*(?:,\s*(?:Col\.?\s*)?La\s*Fe[^,.\n]*)?',
     REAL_LOCATIONS["guadalupe"]["direccion"]),

    # ── Saltillo ──
    (r'Blvd\.?\s*Venustiano\s*Carranza\s*#?\d*[^,.\n]*(?:,\s*(?:Col\.?\s*)?Rep[uú]blica[^,.\n]*)?',
     REAL_LOCATIONS["saltillo"]["direccion"]),

    # ── Reynosa ──
    # Blvd. Hidalgo como dirección Reynosa (NOT the real Calle Hidalgo in Guadalupe)
    (r'Blvd\.?\s*Hidalgo\s*#?\d*[^,.\n]*(?:,\s*(?:Col\.?\s*)?Rodr[ií]guez[^,.\n]*)?',
     REAL_LOCATIONS["reynosa"]["direccion"]),
]


def fix_addresses(text):
    """C1: Reemplaza direcciones falsas con las reales."""
    original = text
    for pattern, replacement in ADDRESS_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    if text != original:
        stats["C1_addresses_fixed"] += 1
    return text


# ═══════════════════════════════════════════════════════════════
# C2: REEMPLAZO DE TELÉFONOS FALSOS
# ═══════════════════════════════════════════════════════════════

def normalize_phone(phone_str):
    """Normaliza un teléfono a solo dígitos."""
    return re.sub(r'[\s.\-()]+', '', phone_str)


def find_city_context(text):
    """Determina la ciudad del contexto para asignar teléfono correcto."""
    t = text.lower()
    if any(w in t for w in ["saltillo", "coahuila"]):
        return "saltillo"
    if any(w in t for w in ["reynosa", "tamaulipas"]):
        return "reynosa"
    return "san_jeronimo"  # default: Monterrey


def fix_phones(text):
    """C2: Reemplaza teléfonos falsos con los reales."""
    original = text

    def replace_phone(match):
        phone = match.group(0)
        digits = normalize_phone(phone)
        if digits in REAL_PHONES:
            return phone  # Es real, no tocar
        # Determinar ciudad del contexto
        city = find_city_context(text)
        real = REAL_LOCATIONS[city]["telefono"]
        stats["C2_phones_fixed"] += 1
        return real

    # Patrones de teléfono mexicano
    # 81 XXXX XXXX, 844 XXX XXXX, 899 XXX XXXX, 800 XXX XXXX
    text = re.sub(
        r'\b\d{2,3}[\s.\-]*\d{3,4}[\s.\-]*\d{4}\b',
        replace_phone, text
    )
    # (XX) XXXX-XXXX
    text = re.sub(
        r'\(\d{2,3}\)\s*\d{3,4}[\s.\-]*\d{4}',
        replace_phone, text
    )
    return text


# ═══════════════════════════════════════════════════════════════
# C3: SUCURSALES FALSAS
# ═══════════════════════════════════════════════════════════════

BRANCH_REPLACEMENTS = [
    # Nombre falso → nombre real
    (r'\bMitras\s*Centro\b', 'San Jerónimo'),
    (r'\bMonterrey\s*Centro\b', 'San Jerónimo'),
    (r'\bGuadalupe\s*Centro\b', 'Guadalupe'),
]


def fix_branches(text):
    """C3: Reemplaza nombres de sucursales falsas."""
    original = text
    for pattern, replacement in BRANCH_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    if text != original:
        stats["C3_branches_fixed"] += 1
    return text


# ═══════════════════════════════════════════════════════════════
# C4: URLs INVÁLIDAS
# ═══════════════════════════════════════════════════════════════

FAKE_MAPS_REPLACEMENTS = [
    # maps.app.goo.gl/TREFAmty → San Jerónimo real
    (r'(?:https?://)?maps\.app\.goo\.gl/(?:TREFAmty|autos-trefa-san-jeronimo)\b[)\s.]*',
     REAL_LOCATIONS["san_jeronimo"]["maps"]),
    # maps.app.goo.gl/TREFAgpe → Guadalupe real
    (r'(?:https?://)?maps\.app\.goo\.gl/TREFAgpe\b[)\s.]*',
     REAL_LOCATIONS["guadalupe"]["maps"]),
    # maps.app.goo.gl/TREFAslt → Saltillo real
    (r'(?:https?://)?maps\.app\.goo\.gl/TREFAslt\b[)\s.]*',
     REAL_LOCATIONS["saltillo"]["maps"]),
    # maps.app.goo.gl/TREFArey → Reynosa real
    (r'(?:https?://)?maps\.app\.goo\.gl/TREFArey\b[)\s.]*',
     REAL_LOCATIONS["reynosa"]["maps"]),
    # maps.app.goo.gl/GuadalupeLasAmericas → Las Américas real
    (r'(?:https?://)?maps\.app\.goo\.gl/GuadalupeLasAmericas\b[)\s.]*',
     REAL_LOCATIONS["las_americas"]["maps"]),
]


def fix_invalid_urls(text):
    """C4: Remueve/corrige URLs inválidas en texto del assistant."""
    original = text

    # Fake Google Maps short links → links reales
    for pattern, replacement in FAKE_MAPS_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)

    # Remaining goo.gl links with random hashes → replace by city context
    def replace_googl(match):
        url = match.group(0)
        t = text.lower()
        if any(w in t for w in ["saltillo", "coahuila"]):
            return REAL_LOCATIONS["saltillo"]["maps"]
        if any(w in t for w in ["reynosa", "tamaulipas"]):
            return REAL_LOCATIONS["reynosa"]["maps"]
        if any(w in t for w in ["las américas", "las americas", "américa del norte"]):
            return REAL_LOCATIONS["las_americas"]["maps"]
        if any(w in t for w in ["guadalupe", "hidalgo #918"]):
            return REAL_LOCATIONS["guadalupe"]["maps"]
        return REAL_LOCATIONS["san_jeronimo"]["maps"]

    text = re.sub(
        r'(?:https?://)?maps\.app\.goo\.gl/[A-Za-z0-9]+[)\s.]*',
        replace_googl, text
    )

    # ejemplo.com — markdown links con imagen [text](url) o [url](url)
    text = re.sub(
        r'\[(?:[^\]]*)\]\(https?://ejemplo\.com/[^\)]+\)',
        '', text
    )
    # ejemplo.com — URLs sueltas (sin markdown)
    text = re.sub(
        r'https?://ejemplo\.com/[^\s)"\]]+',
        '', text
    )
    # ejemplo.com — referencias sueltas a la URL
    text = re.sub(
        r'ejemplo\.com/\S+',
        '', text
    )

    # Airtable storage URLs
    text = re.sub(
        r'https?://v\d+\.airtableusercontent\.com/[^\s)"\]]+',
        '', text
    )

    # YouTube
    text = re.sub(
        r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s)"\]]+',
        '', text
    )

    # Calendly → link interno de citas
    text = re.sub(
        r'https?://calendly\.com/[^\s)"\]]+',
        'autostrefa.mx/escritorio/citas', text
    )

    # Limpiar artefactos: dobles espacios, "enlace: " sin URL, etc.
    text = re.sub(r'(?:enlace|link|liga|imagen|foto|video):\s*(?=\s|$)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'  +', ' ', text)

    if text != original:
        stats["C4_urls_fixed"] += 1
    return text


# ═══════════════════════════════════════════════════════════════
# C5: DOMINIO VIEJO trefa.mx → autostrefa.mx
# ═══════════════════════════════════════════════════════════════

def fix_old_domain(text):
    """C5: Migra trefa.mx → autostrefa.mx (sin tocar autostrefa.mx ni api.trefa.mx)."""
    original = text
    # Negative lookbehinds: no matchear si viene después de "autos", "api." o "www."
    text = re.sub(
        r'(?<!autos)(?<!api\.)(?<!www\.)trefa\.mx/',
        'autostrefa.mx/', text
    )
    if text != original:
        stats["C5_domain_fixed"] += 1
    return text


# ═══════════════════════════════════════════════════════════════
# C6: /inventario/ → /autos/
# ═══════════════════════════════════════════════════════════════

def fix_inventario_path(text):
    """C6: Corrige path /inventario/ → /autos/."""
    original = text
    text = text.replace('/inventario/', '/autos/')
    if text != original:
        stats["C6_inventario_fixed"] += 1
    return text


# ═══════════════════════════════════════════════════════════════
# C7: autostrefa.com → autostrefa.mx
# ═══════════════════════════════════════════════════════════════

def fix_wrong_tld(text):
    """C7: Corrige TLD .com → .mx."""
    original = text
    text = text.replace('autostrefa.com', 'autostrefa.mx')
    if text != original:
        stats["C7_tld_fixed"] += 1
    return text


# ═══════════════════════════════════════════════════════════════
# C8: RAW FIELD LABELS → LENGUAJE NATURAL
# ═══════════════════════════════════════════════════════════════

FIELD_LABEL_FIXES = [
    # traccion: XXX → tracción XXX (natural)
    (r'\btraccion\s*:\s*FWD\b', 'tracción delantera'),
    (r'\btraccion\s*:\s*AWD\b', 'tracción integral'),
    (r'\btraccion\s*:\s*4WD\b', 'tracción 4x4'),
    (r'\btraccion\s*:\s*4x4\b', 'tracción 4x4'),
    (r'\btraccion\s*:\s*RWD\b', 'tracción trasera'),
    (r'\btraccion\s*:\s*([A-Za-záéíóú]+)', r'tracción \1'),
    # url_imagen (nunca debe aparecer)
    (r'\burl_imagen\s*:\s*\S+', ''),
    # fuente: URL
    (r'\bfuente\s*:\s*https?://\S+', ''),
    # record_id, vehiculo_id, slug como labels
    (r'\brecord_id\s*:\s*\S+', ''),
    (r'\bvehiculo_id\s*:\s*\S+', ''),
    (r'\bslug\s*:\s*\S+', ''),
]


def fix_field_labels(text):
    """C8: Reemplaza labels técnicos con lenguaje natural."""
    original = text
    for pattern, replacement in FIELD_LABEL_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Limpiar dobles espacios resultantes
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    if text != original:
        stats["C8_fields_fixed"] += 1
    return text


# ═══════════════════════════════════════════════════════════════
# APLICAR TODOS LOS FIXES A UN MENSAJE
# ═══════════════════════════════════════════════════════════════

def extract_text_without_tc(content):
    """Remueve tool_call blocks."""
    return re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', str(content)).strip()


def apply_content_fixes(text):
    """Aplica todos los fixes de contenido a un texto."""
    text = fix_addresses(text)
    text = fix_phones(text)
    text = fix_branches(text)
    text = fix_invalid_urls(text)
    text = fix_old_domain(text)
    text = fix_inventario_path(text)
    text = fix_wrong_tld(text)
    text = fix_field_labels(text)
    return text


def fix_assistant_content(content):
    """
    Aplica fixes al content de un mensaje assistant.
    Solo modifica las partes de TEXTO, no las partes de tool_call.
    """
    parts = re.split(r'(<tool_call>[\s\S]*?</tool_call>)', content)

    for i, part in enumerate(parts):
        if part.strip().startswith('<tool_call>'):
            continue
        parts[i] = apply_content_fixes(part)

    return ''.join(parts)


def fix_tool_content(content):
    """
    Aplica fixes al content de tool_responses.
    Corrige direcciones, sucursales y URLs pero NO teléfonos
    (pueden ser datos de clientes en solicitar_datos_contacto).
    """
    text = fix_addresses(content)
    text = fix_branches(text)
    text = fix_invalid_urls(text)
    text = fix_old_domain(text)
    text = fix_inventario_path(text)
    text = fix_wrong_tld(text)
    return text


def fix_user_content(content):
    """
    Aplica fixes al content de mensajes user.
    Solo direcciones y sucursales (no teléfonos del cliente).
    """
    text = fix_addresses(content)
    text = fix_branches(text)
    return text


# ═══════════════════════════════════════════════════════════════
# VERIFICACIÓN POST-FIX
# ═══════════════════════════════════════════════════════════════

# Calles falsas conocidas
FAKE_STREETS = [
    'gonzalitos', 'benito juárez', 'benito juarez',
    'venustiano carranza',
    'garza sada', 'morelos #',
]

FAKE_BRANCHES = ['mitras centro', 'monterrey centro', 'guadalupe centro']

INVALID_URL_PATTERNS = [
    r'https?://ejemplo\.com',  # URLs (no emails @ejemplo.com)
    r'youtube\.com', r'youtu\.be',
    r'airtableusercontent\.com', r'calendly\.com',
    r'autostrefa\.com', r'/inventario/',
]

LEAKED_FIELDS = [
    r'\burl_imagen\s*:', r'\brecord_id\s*:', r'\bslug\s*:',
    r'\bvehiculo_id\s*:', r'\btraccion\s*:\s*[A-Z]',
]


def verify_conversation(messages):
    """Verifica que no quedan problemas en la conversación (todos los roles)."""
    issues = []

    for m in messages:
        role = m.get("role", "")
        if role == "system":
            continue
        content = str(m.get("content", ""))
        text = extract_text_without_tc(content)
        if not text:
            continue
        text_lower = text.lower()

        # Fake streets
        for street in FAKE_STREETS:
            if street in text_lower:
                # Verificar que no sea parte de la dirección real
                if "aarón sáenz" not in text_lower and "hidalgo #918" not in text_lower:
                    issues.append(f"fake_street:{street}")

        # Fake branches
        for branch in FAKE_BRANCHES:
            if branch in text_lower:
                issues.append(f"fake_branch:{branch}")

        # Invalid URLs
        for pattern in INVALID_URL_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                issues.append(f"invalid_url:{pattern}")

        # Leaked fields
        for pattern in LEAKED_FIELDS:
            if re.search(pattern, text, re.IGNORECASE):
                issues.append(f"leaked_field:{pattern}")

        # Fake phones — solo en assistant (tool/user pueden tener teléfonos de clientes)
        if role == "assistant":
            for match in re.finditer(r'\b(\d{2,3})[\s.\-]*(\d{3,4})[\s.\-]*(\d{4})\b', text):
                digits = match.group(1) + match.group(2) + match.group(3)
                if digits not in REAL_PHONES and len(digits) == 10:
                    full = match.group(0)
                    if not re.search(r'\$|mxn|pesos|año|id|km', text_lower[max(0, match.start()-20):match.end()+10]):
                        issues.append(f"fake_phone:{full}")

    return issues


# ═══════════════════════════════════════════════════════════════
# PROCESAMIENTO PRINCIPAL
# ═══════════════════════════════════════════════════════════════

def process_file(filepath, dry_run=False):
    """Procesa un archivo JSONL."""
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

    modified = 0
    post_issues = Counter()

    for idx, conv in enumerate(convos):
        msgs = conv.get("messages", [])
        was_modified = False

        for m in msgs:
            role = m.get("role", "")
            content = str(m.get("content", ""))

            if role == "assistant":
                new_content = fix_assistant_content(content)
            elif role == "tool":
                new_content = fix_tool_content(content)
            elif role == "user":
                new_content = fix_user_content(content)
            else:
                continue  # system prompt: no tocar

            if new_content != content:
                m["content"] = new_content
                was_modified = True

        if was_modified:
            modified += 1

        # Verificación post-fix
        issues = verify_conversation(msgs)
        for issue in issues:
            post_issues[issue] += 1
            if not dry_run and post_issues[issue] <= 3:
                # Mostrar primeros 3 ejemplos de cada issue
                print(f"    ⚠ Conv {idx}: {issue}")

    # Escribir
    if dry_run:
        print(f"\n  DRY-RUN: {modified} conversaciones modificadas de {total}")
    else:
        backup = filepath + ".pre_content_fix"
        if not os.path.exists(backup):
            os.rename(filepath, backup)
            print(f"  Backup: {os.path.basename(backup)}")
        else:
            print(f"  Backup ya existe: {os.path.basename(backup)}")

        with open(filepath, "w", encoding="utf-8") as f:
            for conv in convos:
                f.write(json.dumps(conv, ensure_ascii=False) + "\n")

    print(f"  Modificadas: {modified} de {total}")

    if post_issues:
        print(f"\n  {'─' * 50}")
        print(f"  ISSUES RESIDUALES POST-FIX:")
        print(f"  {'─' * 50}")
        for issue, count in post_issues.most_common():
            print(f"    {issue:<50} {count:>5}")
    else:
        print(f"\n  ✓ Verificación post-fix: 0 issues residuales")

    return total, modified


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Correcciones de contenido al dataset merged v10"
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo reportar")
    args = parser.parse_args()

    print("=" * 70)
    print("  fix_content_quality.py — Correcciones de contenido")
    print("=" * 70)

    files = [
        os.path.join(BASE, "merged_v10_together_train.jsonl"),
        os.path.join(BASE, "merged_v10_together_eval.jsonl"),
    ]

    grand_total = 0
    grand_modified = 0

    for f in files:
        if os.path.exists(f):
            t, m = process_file(f, dry_run=args.dry_run)
            grand_total += t
            grand_modified += m
        else:
            print(f"\n  ⚠ No encontrado: {os.path.basename(f)}")

    print(f"\n{'═' * 70}")
    print("  ESTADÍSTICAS DE FIXES")
    print(f"{'═' * 70}")
    for key, val in sorted(stats.items()):
        if val > 0:
            print(f"  {key:<45} {val:>6}")

    print(f"\n{'═' * 70}")
    print("  RESUMEN FINAL")
    print(f"{'═' * 70}")
    print(f"  Total procesadas:      {grand_total}")
    print(f"  Modificadas:           {grand_modified}")
    print()


if __name__ == "__main__":
    main()
