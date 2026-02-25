#!/usr/bin/env python3
"""
Auditoría de URLs en assistant responses del dataset merged_v10_together_train.jsonl

Verifica:
1. URLs válidas vs inválidas en texto del asistente
2. Dumps de campos JSON crudos (url_imagen, fuente, traccion, etc.)
3. Volcados de JSON-like en respuestas del asistente
"""

import json
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

INPUT_FILE = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets/merged_v10_together_train.jsonl"

# ─── URL classification rules ───────────────────────────────────────────────

VALID_AUTOSTREFA_PATHS = [
    "/autos",
    "/registro",
    "/acceder",
    "/escritorio/aplicacion",
    "/escritorio/citas",
    "/vacantes",
]

def is_valid_url(url: str) -> tuple[bool, str]:
    """
    Classify a URL as VALID or INVALID per the rules.
    Returns (is_valid, category_string).
    """
    url_lower = url.lower().rstrip(".")

    # WhatsApp links -> VALID
    if "wa.me/" in url_lower:
        return True, "WhatsApp (wa.me)"

    # Google Maps (all forms: google.com/maps, maps.google, maps.app.goo.gl, goo.gl/maps)
    if ("google.com/maps" in url_lower or "maps.google" in url_lower
            or "goo.gl/maps" in url_lower or "maps.app.goo.gl" in url_lower):
        return True, "Google Maps"

    # Image URLs -> INVALID
    if any(url_lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp"]):
        return False, "Image URL (extension)"

    # YouTube -> INVALID
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return False, "YouTube"

    # S3 / CloudFront / AWS storage -> INVALID
    if any(d in url_lower for d in ["s3.amazonaws", "cloudfront.net", "storage.googleapis",
                                     "blob.core.windows", "airtableusercontent.com"]):
        return False, "Cloud storage (S3/CDN/Airtable)"

    # Calendly -> INVALID (external scheduling tool, not official TREFA)
    if "calendly.com" in url_lower:
        return False, "Calendly (external)"

    # ejemplo.com -> INVALID (fake/placeholder)
    if "ejemplo.com" in url_lower:
        return False, "Fake/placeholder (ejemplo.com)"

    # ── autostrefa.mx or trefa.mx ──
    for domain_str in ["autostrefa.mx", "trefa.mx"]:
        if domain_str in url_lower:
            # Extract path (strip query params for path classification)
            path = ""
            if f"{domain_str}/" in url_lower:
                after_domain = url_lower.split(f"{domain_str}/", 1)[1].rstrip(".").rstrip("/")
                path = "/" + after_domain.split("?")[0].split("#")[0]  # path only, no query
            elif url_lower.rstrip("/").endswith(domain_str):
                return True, f"Bare domain ({domain_str})"

            # /autos/SLUG -> VALID (vehicle page)
            if path.startswith("/autos/") and len(path) > len("/autos/"):
                return True, "Vehicle page (/autos/SLUG)"

            # Check known valid paths (exact or subpath)
            for valid_path in VALID_AUTOSTREFA_PATHS:
                if path == valid_path or path.startswith(valid_path + "/"):
                    return True, f"Valid path ({valid_path})"

            # /autos -> VALID (inventory)
            if path == "/autos":
                return True, "Inventory (/autos)"

            # ── Non-standard paths -> classify as specific INVALID types ──
            if "/inventario/" in path or path == "/inventario":
                return False, "Non-standard path: /inventario/"
            if "/busquedas" in path:
                return False, "Non-standard path: /busquedas"
            if "/financiamiento" in path:
                return False, "Non-standard path: /financiamiento/"
            if "/terminos" in path:
                return False, "Non-standard path: /terminos"

            # Anything else on the domain
            if path and path not in ["", "/"]:
                return False, f"Non-standard path: {path[:60]}"

            return True, f"Bare domain ({domain_str})"

    # www.autostrefa.com (wrong TLD)
    if "autostrefa.com" in url_lower:
        return False, "Wrong TLD: autostrefa.com (should be .mx)"

    # Any other external domain -> INVALID
    domain = get_domain(url)
    return False, f"External domain: {domain}"


def extract_assistant_text(content: str) -> str:
    """Extract text from assistant message, removing <tool_call> and <tool_response> blocks."""
    text = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL)
    text = re.sub(r'<tool_response>.*?</tool_response>', '', text, flags=re.DOTALL)
    return text.strip()


def find_urls(text: str) -> list[str]:
    """Find all URLs in text."""
    urls = []
    # Match http(s):// URLs
    urls.extend(re.findall(r'https?://[^\s<>\"\'\)\]\}，。、👈👉]+', text))
    # Match www. URLs without http
    for m in re.findall(r'(?<!\/)www\.[^\s<>\"\'\)\]\}，。、👈👉]+', text):
        if m not in urls:
            urls.append(m)
    # Match bare domain references like autostrefa.mx/... or trefa.mx/...
    for m in re.findall(r'(?:autostrefa|trefa)\.mx[/\w\-\.?&=]*', text):
        already_found = any(m in u for u in urls)
        if not already_found:
            urls.append(m)

    # Clean trailing punctuation
    cleaned = []
    for u in urls:
        u = u.rstrip(".,;:!?)")
        if u:
            cleaned.append(u)
    return cleaned


def get_domain(url: str) -> str:
    """Extract domain from URL."""
    url_lower = url.lower()
    if url_lower.startswith("http"):
        try:
            parsed = urlparse(url_lower)
            return parsed.netloc or "unknown"
        except Exception:
            pass
    if "autostrefa.mx" in url_lower:
        return "autostrefa.mx"
    if "trefa.mx" in url_lower:
        return "trefa.mx"
    if "wa.me" in url_lower:
        return "wa.me"
    if "google.com" in url_lower:
        return "google.com"
    if "www." in url_lower:
        return url_lower.split("www.")[1].split("/")[0]
    return url_lower.split("/")[0] if "/" in url_lower else url_lower


# ─── Raw JSON dump detection ────────────────────────────────────────────────

RAW_FIELD_PATTERNS = [
    (r'\burl_imagen\b', "url_imagen"),
    (r'(?<!\w)url:\s*["\']?https?://', "url: <link>"),
    (r'(?<!\w)fuente:\s', "fuente:"),
    (r'(?<!\w)traccion:\s', "traccion:"),
    # transmision: is common in natural language ("Transmisión: Manual"), skip it
]

# Fields that suggest JSON dump when appearing in a structured pattern
JSON_DUMP_FIELDS = [
    "precio_lista", "precio_oferta", "url_imagen", "imagen_url",
    "enganchemin", "enganche_recomendado", "mensualidad_minima",
    "mensualidad_recomendada", "plazomax", "autoano", "liga_web",
    "slug",
]

UNDERSCORE_INTERNAL_FIELDS = {
    "precio_lista", "precio_oferta", "url_imagen", "imagen_url",
    "enganche_min", "enganche_recomendado", "mensualidad_min",
    "mensualidad_minima", "mensualidad_recomendada", "plazo_max",
    "plazomax", "auto_ano", "liga_web", "año_modelo",
}


def check_raw_field_dumps(text: str) -> list[str]:
    """Check if assistant text contains raw field name references."""
    issues = []
    for pattern, label in RAW_FIELD_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            issues.append(f"Raw field: '{label}' ({len(matches)}x)")
    return issues


def check_json_dumps(text: str) -> list[str]:
    """Check for JSON-like dumps in assistant text."""
    issues = []

    # Pattern: "key": "value" repeated (JSON object dump)
    json_pairs = re.findall(r'"(\w+)":\s*"[^"]*"', text)
    if len(json_pairs) >= 3:
        issues.append(f"JSON-like pairs ({len(json_pairs)}): {', '.join(json_pairs[:5])}")

    # Pattern: multiple known internal field names appearing together
    found_fields = [f for f in JSON_DUMP_FIELDS if f in text.lower()]
    if len(found_fields) >= 3:
        issues.append(f"Multiple internal fields ({len(found_fields)}): {', '.join(found_fields[:5])}")

    # Pattern: field_name: value where field_name uses underscores (internal naming)
    underscore_fields = re.findall(r'\b(\w+_\w+):\s', text)
    internal_looking = [f for f in underscore_fields if f.lower() in UNDERSCORE_INTERNAL_FIELDS]
    if internal_looking:
        issues.append(f"Underscore fields verbatim: {', '.join(set(internal_looking))}")

    return issues


# ─── Main audit ──────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("AUDITORIA DE URLs EN ASSISTANT RESPONSES")
    print(f"Dataset: {INPUT_FILE}")
    print("=" * 80)

    total_convs = 0
    total_assistant_msgs = 0
    total_urls = 0
    url_domain_counts = Counter()
    valid_url_count = 0
    invalid_url_count = 0
    invalid_url_examples = []       # (conv_idx, url, category, context)
    raw_field_convs = []            # (conv_idx, msg_idx, issues, snippet)
    json_dump_convs = []            # (conv_idx, msg_idx, issues, snippet)
    affected_conv_indices_urls = set()
    affected_conv_indices_fields = set()
    affected_conv_indices_json = set()

    # Detailed breakdown
    invalid_by_category = defaultdict(list)  # category -> [(conv_idx, url)]
    valid_by_category = Counter()

    # Track trefa.mx usage specifically
    trefa_mx_convs = set()

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for conv_idx, line in enumerate(f):
            conv = json.loads(line)
            total_convs += 1

            for msg_idx, msg in enumerate(conv["messages"]):
                if msg["role"] != "assistant":
                    continue

                total_assistant_msgs += 1
                content = msg["content"]
                text = extract_assistant_text(content)

                if not text:
                    continue

                # ── Find & classify URLs ──
                urls = find_urls(text)
                for url in urls:
                    total_urls += 1
                    domain = get_domain(url)
                    url_domain_counts[domain] += 1

                    is_valid, category = is_valid_url(url)

                    if is_valid:
                        valid_url_count += 1
                        valid_by_category[category] += 1
                        # Track trefa.mx
                        if "trefa.mx" in url.lower() and "autostrefa" not in url.lower():
                            trefa_mx_convs.add(conv_idx)
                    else:
                        invalid_url_count += 1
                        affected_conv_indices_urls.add(conv_idx)
                        invalid_by_category[category].append((conv_idx, url))

                        # Get context
                        url_pos = text.find(url[:30])
                        if url_pos >= 0:
                            start = max(0, url_pos - 50)
                            end = min(len(text), url_pos + len(url) + 50)
                            context = text[start:end].replace("\n", " ")
                        else:
                            context = url
                        invalid_url_examples.append((conv_idx, url, category, context))

                # ── Check raw field dumps ──
                raw_issues = check_raw_field_dumps(text)
                if raw_issues:
                    raw_field_convs.append((conv_idx, msg_idx, raw_issues, text[:300]))
                    affected_conv_indices_fields.add(conv_idx)

                # ── Check JSON dumps ──
                json_issues = check_json_dumps(text)
                if json_issues:
                    json_dump_convs.append((conv_idx, msg_idx, json_issues, text[:300]))
                    affected_conv_indices_json.add(conv_idx)

    all_affected = affected_conv_indices_urls | affected_conv_indices_fields | affected_conv_indices_json

    # ═══════════════════════════════════════════════════════════════════════════
    # REPORT
    # ═══════════════════════════════════════════════════════════════════════════

    print(f"\n{'─' * 80}")
    print("1. RESUMEN GENERAL")
    print(f"{'─' * 80}")
    print(f"  Conversaciones analizadas:  {total_convs:,}")
    print(f"  Mensajes de asistente:      {total_assistant_msgs:,}")
    print(f"  Total URLs en texto:        {total_urls:,}")
    print(f"    VALIDAS:                  {valid_url_count:,}")
    print(f"    INVALIDAS:                {invalid_url_count:,}")
    print(f"  Raw field dumps:            {len(raw_field_convs)}")
    print(f"  JSON-like dumps:            {len(json_dump_convs)}")
    print(f"  Conversaciones afectadas:   {len(all_affected)} de {total_convs} ({100*len(all_affected)/total_convs:.1f}%)")

    # ── 2. URLs by domain ──
    print(f"\n{'─' * 80}")
    print("2. URLs POR DOMINIO (todas)")
    print(f"{'─' * 80}")
    for domain, count in url_domain_counts.most_common():
        print(f"  {domain:45s} {count:>5d}")

    # ── 3. Valid URLs breakdown ──
    print(f"\n{'─' * 80}")
    print(f"3. DESGLOSE DE URLs VALIDAS ({valid_url_count:,})")
    print(f"{'─' * 80}")
    for cat, count in valid_by_category.most_common():
        print(f"  {cat:45s} {count:>5d}")

    # ── 4. Invalid URLs breakdown ──
    print(f"\n{'─' * 80}")
    print(f"4. DESGLOSE DE URLs INVALIDAS ({invalid_url_count:,} en {len(affected_conv_indices_urls)} convs)")
    print(f"{'─' * 80}")
    for cat, entries in sorted(invalid_by_category.items(), key=lambda x: -len(x[1])):
        print(f"\n  [{len(entries):>3d}] {cat}")
        shown = set()
        for cidx, url in entries:
            if url not in shown:
                print(f"        Conv {cidx}: {url[:110]}")
                shown.add(url)
            if len(shown) >= 5:
                break

    # ── 5. Raw field dumps ──
    print(f"\n{'─' * 80}")
    print(f"5. CAMPOS CRUDOS EN TEXTO ({len(raw_field_convs)} ocurrencias, {len(affected_conv_indices_fields)} convs)")
    print(f"{'─' * 80}")
    if raw_field_convs:
        for conv_idx, msg_idx, issues, snippet in raw_field_convs[:30]:
            print(f"\n  Conv {conv_idx}, Msg {msg_idx}:")
            for issue in issues:
                print(f"    {issue}")
            print(f"    Texto: {snippet[:200].replace(chr(10), ' ')}...")
    else:
        print("  Ninguno encontrado.")

    # ── 6. JSON dumps ──
    print(f"\n{'─' * 80}")
    print(f"6. VOLCADOS JSON-LIKE ({len(json_dump_convs)} ocurrencias, {len(affected_conv_indices_json)} convs)")
    print(f"{'─' * 80}")
    if json_dump_convs:
        for conv_idx, msg_idx, issues, snippet in json_dump_convs[:30]:
            print(f"\n  Conv {conv_idx}, Msg {msg_idx}:")
            for issue in issues:
                print(f"    {issue}")
            print(f"    Texto: {snippet[:200].replace(chr(10), ' ')}...")
        if len(json_dump_convs) > 30:
            print(f"\n  ... y {len(json_dump_convs) - 30} mas.")
    else:
        print("  Ninguno encontrado.")

    # ── 7. trefa.mx domain note ──
    print(f"\n{'─' * 80}")
    print("7. USO DEL DOMINIO ANTERIOR trefa.mx")
    print(f"{'─' * 80}")
    trefa_total = url_domain_counts.get("trefa.mx", 0)
    auto_total = url_domain_counts.get("autostrefa.mx", 0)
    print(f"  trefa.mx:      {trefa_total} URLs en {len(trefa_mx_convs)} conversaciones")
    print(f"  autostrefa.mx: {auto_total} URLs")
    if trefa_mx_convs:
        print(f"  Conversaciones con trefa.mx: {sorted(trefa_mx_convs)[:50]}")
        if len(trefa_mx_convs) > 50:
            print(f"  ... y {len(trefa_mx_convs) - 50} mas")

    # ── 8. All affected conversation indices ──
    print(f"\n{'─' * 80}")
    print(f"8. INDICES DE CONVERSACIONES CON PROBLEMAS ({len(all_affected)})")
    print(f"{'─' * 80}")
    print(f"  Por URLs invalidas ({len(affected_conv_indices_urls)}):")
    s = sorted(affected_conv_indices_urls)
    for i in range(0, len(s), 25):
        print(f"    {', '.join(str(x) for x in s[i:i+25])}")
    if affected_conv_indices_fields:
        print(f"  Por campos crudos ({len(affected_conv_indices_fields)}):")
        s = sorted(affected_conv_indices_fields)
        for i in range(0, len(s), 25):
            print(f"    {', '.join(str(x) for x in s[i:i+25])}")
    if affected_conv_indices_json:
        print(f"  Por volcados JSON ({len(affected_conv_indices_json)}):")
        s = sorted(affected_conv_indices_json)
        for i in range(0, len(s), 25):
            print(f"    {', '.join(str(x) for x in s[i:i+25])}")

    # ── 9. Full invalid URL list ──
    print(f"\n{'─' * 80}")
    print(f"9. LISTA COMPLETA DE URLs INVALIDAS ({len(invalid_url_examples)})")
    print(f"{'─' * 80}")
    for conv_idx, url, category, context in invalid_url_examples:
        print(f"  Conv {conv_idx} [{category}]")
        print(f"    URL: {url}")
        print(f"    Ctx: ...{context[:150]}...")
        print()

    print(f"\n{'=' * 80}")
    print("FIN DE LA AUDITORIA")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
