#!/usr/bin/env python3
"""
Auditoría de direcciones, teléfonos y sucursales FALSAS en el dataset TREFA.

Busca información incorrecta/inventada en respuestas del asistente:
1. Direcciones falsas o calles inventadas (NO las 5 reales de TREFA)
2. Teléfonos falsos (NO los 3 reales)
3. Nombres de sucursales inventados (NO las 5 reales)

Solo analiza texto del assistant FUERA de bloques <tool_call>.
"""

import json
import re
import sys
from collections import defaultdict, Counter

DATASET_PATH = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets/merged_v10_together_train.jsonl"

# ============================================================
# VALID TREFA LOCATIONS (the ONLY correct ones)
# ============================================================
# 1. San Jerónimo (matriz): Aarón Sáenz Garza 1902, Plaza Oasis, Local 1109, Col. Santa María, Monterrey, NL | 81 8704 9079
# 2. Guadalupe: Calle Hidalgo #918, Paraíso, Guadalupe, NL | 81 8704 9079
# 3. Las Américas: América del Norte 110, Col. Las Américas, Guadalupe, NL | 81 8704 9079
# 4. Saltillo: Plaza Santa Isabel, Blvd. Nazario Ortiz #2060, Local 132, Saltillo, Coahuila | 844 212 3399
# 5. Reynosa: Blvd. Beethoven #100, Narciso Mendoza, Reynosa, Tamaulipas | 899 460 2822

VALID_PHONES_NORMALIZED = {"8187049079", "8442123399", "8994602822"}

VALID_BRANCH_NAMES_LOWER = {
    "san jerónimo", "san jeronimo", "guadalupe", "las américas", "las americas",
    "saltillo", "reynosa", "monterrey",
}

# Fragments that appear in the 5 REAL addresses
VALID_STREET_FRAGMENTS = [
    "aarón sáenz", "aaron sáenz", "aarón saenz", "aaron saenz",
    "sáenz garza", "saenz garza", "1902",
    "hidalgo 918", "hidalgo #918", "hidalgo # 918", "hidalgo no. 918",
    "hidalgo #  918", "hidalgo  918",
    "américa del norte", "america del norte",
    "nazario ortiz", "nazario órtiz", "ortiz 2060", "ortiz #2060",
    "beethoven", "beethoven 100", "beethoven #100",
    "plaza oasis",
    "plaza santa isabel",
    "narciso mendoza",
    "santa maría",  # Col. Santa María — valid neighborhood
    "santa maria",
    "paraíso", "paraiso",  # Col. Paraíso, Guadalupe — valid neighborhood
    "las américas", "las americas",
]


def strip_tool_calls(text: str) -> str:
    """Remove <tool_call>...</tool_call> blocks from assistant text."""
    return re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL).strip()


def normalize_phone(s: str) -> str:
    return re.sub(r'[^\d]', '', s)


# ============================================================
# 1. FAKE PHONE DETECTION
# ============================================================
def find_phone_violations(text: str, conv_idx: int) -> list:
    violations = []

    # Patterns that look like Mexican phone numbers
    phone_patterns = [
        # 81 8704 9079 / 844 212 3399 / 899 460 2822
        r'\b(\d{2,3})\s+(\d{3,4})\s+(\d{4})\b',
        # 81-8704-9079
        r'\b(\d{2,3})\s*[-–]\s*(\d{3,4})\s*[-–]\s*(\d{4})\b',
        # (81) 8704 9079 / (81) 8704-9079
        r'\((\d{2,3})\)\s*(\d{3,4})\s*[-–\s]\s*(\d{4})\b',
        # Solid 10 digits near phone context
        r'(?:tel[eé]fono|tel\.?|celular|cel\.?|n[úu]mero|whatsapp|llam|contacto|comunic|l[íi]nea)[:\s]*(\d{10})\b',
        r'\b(\d{10})\b(?=.*(?:tel|celular|llam|contacto))',
    ]

    for pattern in phone_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            full = match.group(0)
            digits = normalize_phone(full)

            if len(digits) < 10 or len(digits) > 12:
                continue

            # Narrow context check: skip if it looks like a financial figure / price / km
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            ctx = text[start:end].lower()

            financial = ['$', 'precio', 'enganche', 'mensualidad', 'pago', 'km',
                         'kilómetro', 'kilometro', 'meses', 'plazo', 'deducible',
                         'seguro', 'interés', 'interes', 'comisión', 'comision']
            if any(f in ctx for f in financial):
                continue

            # Must be near phone-related words OR have canonical phone format
            phone_words = ['tel', 'teléfono', 'telefono', 'llam', 'contacto',
                           'celular', 'cel', 'whatsapp', 'wsp', 'número', 'numero',
                           'comunic', 'línea', 'linea', 'marca al', 'marcar']
            canonical = bool(re.match(r'^\d{2,3}\s\d{3,4}\s\d{4}$', full)) or \
                        bool(re.match(r'^\(\d{2,3}\)', full))
            near_phone = any(w in ctx for w in phone_words)

            if not near_phone and not canonical:
                continue

            # Is it valid?
            if digits[:10] not in VALID_PHONES_NORMALIZED:
                snip_s = max(0, match.start() - 40)
                snip_e = min(len(text), match.end() + 40)
                snippet = text[snip_s:snip_e].replace('\n', ' ')[:150]
                violations.append({
                    "conv_idx": conv_idx,
                    "type": "fake_phone",
                    "snippet": snippet,
                    "phone_found": full,
                })

    return violations


# ============================================================
# 2. FAKE ADDRESS / STREET DETECTION
# ============================================================
def find_address_violations(text: str, conv_idx: int) -> list:
    violations = []
    text_lower = text.lower()

    # Strategy A: Find any street-like patterns (Av., Blvd., Calle, Calzada + name + number)
    # that do NOT match the 5 valid addresses.
    # NOTE: Exclude "carretera" — too many false positives with car descriptions.
    street_patterns = [
        r'(?:av\.?|avenida|blvd\.?|boulevard|calle|calz\.?|calzada)\s+[A-ZÁÉÍÓÚÜÑa-záéíóúüñ\.\s]{3,50}?(?:#\s*)?\d{1,5}',
    ]

    seen_streets = set()  # track what Strategy A already flagged, to avoid dup with Strategy B

    for pattern in street_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            matched = match.group(0)
            matched_lower = matched.lower()

            # Check if it matches a valid address fragment
            is_valid = any(frag in matched_lower for frag in VALID_STREET_FRAGMENTS)
            if is_valid:
                continue

            # Get context to check if it's presented as a TREFA address
            ctx_s = max(0, match.start() - 100)
            ctx_e = min(len(text), match.end() + 100)
            ctx = text[ctx_s:ctx_e].lower()

            trefa_indicators = ['trefa', 'sucursal', 'nuestra', 'nuestro', 'nuestras',
                                'visítanos', 'visitanos', 'nos encontr', 'estamos en',
                                'dirección', 'direccion', 'ubicad', 'oficina',
                                'monterrey', 'guadalupe', 'saltillo', 'reynosa',
                                'horario', 'lunes', 'maps.app', 'maps.google']
            if any(ind in ctx for ind in trefa_indicators):
                snip_s = max(0, match.start() - 30)
                snip_e = min(len(text), match.end() + 40)
                snippet = text[snip_s:snip_e].replace('\n', ' ')[:150]
                violations.append({
                    "conv_idx": conv_idx,
                    "type": "fake_address",
                    "snippet": snippet,
                    "detail": f"Unknown street: {matched.strip()[:80]}",
                })
                seen_streets.add((match.start(), match.end()))

    # Strategy B: Known hallucinated streets — look for them in address-like contexts
    KNOWN_FAKE_STREETS = [
        "gonzalitos", "garza sada", "eugenio garza",
        "benito juárez", "benito juarez",
        "constitución", "constitucion",
        "insurgentes", "revolución", "revolucion", "reforma",
        "madero", "francisco i. madero", "francisco i madero",
        "morelos", "independencia",
        "cuauhtémoc", "cuauhtemoc",
        # NOTE: "colón"/"colon" removed — too many false positives with "colonia"
        "lincoln", "ruiz cortines", "ruiz cortínez",
        "morones prieto", "leones", "vasconcelos",
        "lázaro cárdenas", "lazaro cardenas",
        "venustiano carranza",
        "miguel hidalgo",
        "padre mier", "pino suárez", "pino suarez",
        "5 de mayo", "16 de septiembre", "20 de noviembre",
        "allende",
        # NOTE: "guerrero" removed — too many false positives ("auto guerrero")
        # NOTE: "matamoros" removed — too many false positives (city name Matamoros)
        "bernardo reyes", "félix u. gómez", "felix u. gomez",
        "álvaro obregón", "alvaro obregon",
        "miguel alemán", "miguel aleman",
        "rio mississippi", "río mississippi",
        "sendero", "sierra madre",
        "fidel velázquez", "fidel velazquez",
        "rodrigo gómez", "rodrigo gomez",
        "pedro infante",
    ]

    for street in KNOWN_FAKE_STREETS:
        for match in re.finditer(re.escape(street), text_lower):
            # Skip if this region was already caught by Strategy A
            overlaps = any(
                s <= match.start() <= e or s <= match.end() <= e
                for s, e in seen_streets
            )
            if overlaps:
                continue

            ctx_s = max(0, match.start() - 120)
            ctx_e = min(len(text), match.end() + 120)
            ctx = text_lower[ctx_s:ctx_e]

            # Must be in address context (not just casual mention)
            addr_context = ['dirección', 'direccion', 'sucursal', 'ubicad',
                            'estamos en', 'nos encontr', 'visítanos', 'visitanos',
                            'av.', 'avenida', 'blvd', 'boulevard', 'calle',
                            'col.', 'colonia', '#', 'local', 'plaza',
                            'trefa', 'horario', 'lunes', 'maps.app']
            if any(a in ctx for a in addr_context):
                snip_s = max(0, match.start() - 30)
                snip_e = min(len(text), match.end() + 50)
                snippet = text[snip_s:snip_e].replace('\n', ' ')[:150]
                violations.append({
                    "conv_idx": conv_idx,
                    "type": "fake_address",
                    "snippet": snippet,
                    "detail": f"Hallucinated street: {street}",
                })

    return violations


# ============================================================
# 3. FAKE BRANCH / SUCURSAL DETECTION
# ============================================================
KNOWN_FAKE_BRANCHES = [
    "cumbres", "valle", "apodaca", "san pedro", "san nicolás", "san nicolas",
    "santa catarina", "escobedo", "centro", "mitras", "contry", "del valle",
    "linda vista", "anáhuac", "anahuac", "obispado", "chipinque",
    "garza garcía", "garza garcia", "la pastora", "la fe",
    "country", "pedregal", "colinas", "industrial",
    "moderna", "terminal", "universidad",
]


def find_branch_violations(text: str, conv_idx: int) -> list:
    violations = []
    text_lower = text.lower()

    # Strategy: Look for known fake branch names being presented as TREFA locations
    for fake in KNOWN_FAKE_BRANCHES:
        patterns = [
            # "sucursal de/en <fake>"
            rf'sucursal\s+(?:de\s+|en\s+){re.escape(fake)}\b',
            # "nuestra sucursal <fake>"
            rf'nuestra\s+sucursal\s+(?:de\s+|en\s+)?{re.escape(fake)}\b',
            # "TREFA <fake>"
            rf'trefa\s+{re.escape(fake)}\b',
            # "<fake>" listed as a sucursal in a list of branches
            rf'sucursales?\s*(?:de\s+|en\s+|:).*?{re.escape(fake)}',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text_lower):
                # Verify it's not a negation ("no contamos con sucursal en <fake>")
                ctx_s = max(0, match.start() - 80)
                prefix = text_lower[ctx_s:match.start()]
                # Also check text after the match for negation patterns
                ctx_e = min(len(text_lower), match.end() + 80)
                suffix = text_lower[match.end():ctx_e]
                negations_before = ['no contamos', 'no tenemos', 'no hay', 'no está',
                                    'no esta', 'no disponible', 'no contamos con',
                                    'no tenemos sucursal', 'sin sucursal']
                negations_after = ['no está listada', 'no esta listada', 'no existe',
                                   'no tenemos', 'no contamos', 'no disponible',
                                   'no aplica', 'fuera de', 'no cubrimos']
                if any(neg in prefix for neg in negations_before):
                    continue
                if any(neg in suffix for neg in negations_after):
                    continue

                snip_s = max(0, match.start() - 20)
                snip_e = min(len(text), match.end() + 40)
                snippet = text[snip_s:snip_e].replace('\n', ' ')[:150]
                violations.append({
                    "conv_idx": conv_idx,
                    "type": "fake_branch",
                    "snippet": snippet,
                    "branch_found": fake,
                })

    # Also look for "Monterrey Centro" or "Mitras Centro" etc. being presented as branch names
    centro_patterns = [
        r'(?:monterrey|guadalupe|saltillo|reynosa)\s+centro',
        r'mitras\s+centro',
    ]
    for pattern in centro_patterns:
        for match in re.finditer(pattern, text_lower):
            ctx_s = max(0, match.start() - 80)
            ctx_e = min(len(text), match.end() + 80)
            ctx = text_lower[ctx_s:ctx_e]
            if any(w in ctx for w in ['sucursal', 'dirección', 'direccion', 'ubicad', 'trefa', 'oficina']):
                snip_s = max(0, match.start() - 20)
                snip_e = min(len(text), match.end() + 40)
                snippet = text[snip_s:snip_e].replace('\n', ' ')[:150]
                violations.append({
                    "conv_idx": conv_idx,
                    "type": "fake_branch",
                    "snippet": snippet,
                    "branch_found": match.group(0),
                })

    return violations


# ============================================================
# DEDUPLICATION
# ============================================================
def deduplicate(violations: list) -> list:
    seen = set()
    unique = []
    for v in violations:
        if v["type"] == "fake_phone":
            # For phones, deduplicate by conv_idx + normalized digits
            digits = normalize_phone(v.get("phone_found", ""))
            key = (v["conv_idx"], "fake_phone", digits)
        elif v["type"] == "fake_address":
            # For addresses, deduplicate by conv_idx + detail (street name)
            key = (v["conv_idx"], "fake_address", v.get("detail", "")[:60])
        else:
            # For branches, deduplicate by conv_idx + branch name
            key = (v["conv_idx"], "fake_branch", v.get("branch_found", ""))
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 80)
    print("AUDITORÍA DE UBICACIONES FALSAS — Dataset TREFA")
    print(f"Archivo: {DATASET_PATH}")
    print("=" * 80)

    with open(DATASET_PATH) as f:
        lines = f.readlines()

    total = len(lines)
    print(f"\nTotal de conversaciones: {total}")
    print("Analizando...\n")

    all_violations = []

    for idx, line in enumerate(lines):
        try:
            conv = json.loads(line.strip())
        except json.JSONDecodeError:
            continue

        for msg in conv.get("messages", []):
            if msg.get("role") != "assistant":
                continue
            clean = strip_tool_calls(msg.get("content", ""))
            if not clean:
                continue

            all_violations.extend(find_phone_violations(clean, idx))
            all_violations.extend(find_address_violations(clean, idx))
            all_violations.extend(find_branch_violations(clean, idx))

    all_violations = deduplicate(all_violations)

    # Group
    by_type = defaultdict(list)
    for v in all_violations:
        by_type[v["type"]].append(v)

    affected = sorted(set(v["conv_idx"] for v in all_violations))

    # ============================================================
    # SUMMARY
    # ============================================================
    print("=" * 80)
    print("RESUMEN DE VIOLACIONES")
    print("=" * 80)
    print(f"\nTotal de violaciones encontradas: {len(all_violations)}")
    print(f"Conversaciones afectadas: {len(affected)} de {total}")
    print(f"  ({len(affected)/total*100:.1f}% del dataset)\n")

    for vtype, label in [
        ("fake_phone", "TELÉFONOS FALSOS"),
        ("fake_address", "DIRECCIONES / CALLES FALSAS"),
        ("fake_branch", "SUCURSALES FALSAS"),
    ]:
        items = by_type.get(vtype, [])
        conv_count = len(set(v["conv_idx"] for v in items))
        print(f"  {label}: {len(items)} violaciones en {conv_count} conversaciones")

    # ============================================================
    # DETAILED LISTS
    # ============================================================
    for vtype, label in [
        ("fake_phone", "TELÉFONOS FALSOS"),
        ("fake_address", "DIRECCIONES / CALLES FALSAS"),
        ("fake_branch", "SUCURSALES FALSAS"),
    ]:
        items = by_type.get(vtype, [])
        if not items:
            continue

        print(f"\n{'='*80}")
        print(f"DETALLE: {label} ({len(items)} violaciones)")
        print(f"{'='*80}")

        for i, v in enumerate(items, 1):
            print(f"\n  [{i}] Conv #{v['conv_idx']}")
            print(f"      Snippet: {v['snippet']}")
            if "phone_found" in v:
                print(f"      Teléfono: {v['phone_found']}")
            if "detail" in v:
                print(f"      Detalle: {v['detail']}")
            if "branch_found" in v:
                print(f"      Sucursal: {v['branch_found']}")

    # ============================================================
    # CONVERSATION INDICES
    # ============================================================
    print(f"\n{'='*80}")
    print("ÍNDICES DE CONVERSACIONES AFECTADAS")
    print(f"{'='*80}")
    print(f"\nTotal: {len(affected)} conversaciones\n")

    for i in range(0, len(affected), 20):
        chunk = affected[i:i+20]
        print("  " + ", ".join(str(c) for c in chunk))

    for vtype, label in [
        ("fake_phone", "TELÉFONOS FALSOS"),
        ("fake_address", "DIRECCIONES / CALLES FALSAS"),
        ("fake_branch", "SUCURSALES FALSAS"),
    ]:
        items = by_type.get(vtype, [])
        if not items:
            continue
        idxs = sorted(set(v["conv_idx"] for v in items))
        print(f"\n  {label} ({len(idxs)} conversaciones):")
        for i in range(0, len(idxs), 20):
            chunk = idxs[i:i+20]
            print("    " + ", ".join(str(c) for c in chunk))

    # ============================================================
    # TOP HALLUCINATED STREETS
    # ============================================================
    street_counter = Counter()
    for v in by_type.get("fake_address", []):
        d = v.get("detail", "")
        if "Hallucinated street:" in d:
            street = d.replace("Hallucinated street: ", "")
            street_counter[street] += 1
        elif "Unknown street:" in d:
            street_counter[d.replace("Unknown street: ", "")[:40]] += 1

    if street_counter:
        print(f"\n{'='*80}")
        print("TOP CALLES INVENTADAS MÁS FRECUENTES")
        print(f"{'='*80}")
        for street, count in street_counter.most_common(30):
            print(f"  {count:>4}x  {street}")

    # ============================================================
    # TOP FAKE BRANCHES
    # ============================================================
    branch_counter = Counter()
    for v in by_type.get("fake_branch", []):
        branch_counter[v.get("branch_found", "")] += 1

    if branch_counter:
        print(f"\n{'='*80}")
        print("TOP SUCURSALES INVENTADAS MÁS FRECUENTES")
        print(f"{'='*80}")
        for branch, count in branch_counter.most_common(20):
            print(f"  {count:>4}x  {branch}")

    # ============================================================
    # SAVE JSON REPORT
    # ============================================================
    report_path = DATASET_PATH.replace('.jsonl', '_AUDIT_LOCATIONS.json')
    report = {
        "total_conversations": total,
        "total_violations": len(all_violations),
        "affected_conversations_count": len(affected),
        "affected_conversation_indices": affected,
        "summary": {
            "fake_phone": {
                "violations": len(by_type.get("fake_phone", [])),
                "conversations": len(set(v["conv_idx"] for v in by_type.get("fake_phone", []))),
            },
            "fake_address": {
                "violations": len(by_type.get("fake_address", [])),
                "conversations": len(set(v["conv_idx"] for v in by_type.get("fake_address", []))),
            },
            "fake_branch": {
                "violations": len(by_type.get("fake_branch", [])),
                "conversations": len(set(v["conv_idx"] for v in by_type.get("fake_branch", []))),
            },
        },
        "violations": all_violations,
    }
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReporte JSON guardado en: {report_path}")

    print(f"\n{'='*80}")
    print("FIN DE AUDITORÍA")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
