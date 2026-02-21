#!/usr/bin/env python3
"""
consolidar_dataset_v5.py — Consolida todas las fuentes de conversaciones en un
dataset limpio v5 para entrenar desde la base.

Aplica las reglas v5:
  - Saludos expresivos con 😊
  - Bullets (•) para opciones de autos
  - Sin cotización por email → solicitud de financiamiento
  - Sin doble saludo
  - Conteo de "Hola" por conversación
  - Deduplicación por hash de contenido
  - Validación de calidad

Fuentes (en orden de prioridad):
  1. semillas_solicitud_financiamiento.jsonl  (6 semillas v5 nuevas)
  2. golden_qwen_mariana.jsonl                (28 gold standard)
  3. gold_upgraded/gold_upgraded_batch_*.jsonl (batches gemini)
  4. gold_upgraded/gold_upgraded_haiku_*.jsonl (batches haiku)
  5. gold_upgraded/synthetic_cleaned.jsonl     (389 sintéticas limpias)
  6. gold_upgraded/haiku_final.jsonl           (139 haiku limpias)
  7. saludos_mariana.jsonl                     (15 saludos)
  8. estilo_conversacional_mariana.jsonl       (20 estilo)

Salida:
  - dataset_v5_train.jsonl     (90%)
  - dataset_v5_eval.jsonl      (10%)
  - dataset_v5_completo.jsonl  (todo junto, para referencia)
  - dataset_v5_stats.json      (estadísticas)

Uso:
    python consolidar_dataset_v5.py
    python consolidar_dataset_v5.py --min-msgs 6 --eval-pct 10
"""

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# RUTAS
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent
GOLD_UPGRADED_DIR = SCRIPT_DIR / "gold_upgraded"
OUTPUT_DIR = SCRIPT_DIR / "v5_dataset"

SOURCES = [
    # (ruta, etiqueta, prioridad)
    (SCRIPT_DIR / "semillas_solicitud_financiamiento.jsonl", "semillas_fin_v5", 1),
    (SCRIPT_DIR / "semillas_obtener_vehiculo.jsonl", "semillas_obtener_vehiculo", 1),
    (SCRIPT_DIR / "golden_qwen_mariana.jsonl", "gold_28", 2),
    (SCRIPT_DIR / "saludos_mariana.jsonl", "saludos", 3),
    (SCRIPT_DIR / "estilo_conversacional_mariana.jsonl", "estilo", 4),
]

# Batches se agregan dinámicamente
BATCH_PATTERNS = [
    ("gold_upgraded_batch_*.jsonl", "batch_gemini"),
    ("gold_upgraded_haiku_*.jsonl", "batch_haiku"),
]

CLEANED_FILES = [
    (GOLD_UPGRADED_DIR / "synthetic_cleaned.jsonl", "synthetic_cleaned"),
    (GOLD_UPGRADED_DIR / "haiku_final.jsonl", "haiku_final"),
]


# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE CARGA
# ═══════════════════════════════════════════════════════════════

def load_jsonl(path: Path) -> list[dict]:
    data = []
    if not path.exists():
        return data
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return data


def conversation_hash(conv: dict) -> str:
    """Hash basado en el contenido de los mensajes (sin metadata)."""
    msgs = conv.get("messages", [])
    text_parts = []
    for m in msgs:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            continue
        # Normalizar espacios para evitar falsos negativos
        content = re.sub(r'\s+', ' ', content).strip()
        text_parts.append(f"{role}:{content}")
    full = "|".join(text_parts)
    return hashlib.md5(full.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════
# TRANSFORMACIONES V5 (regex)
# ═══════════════════════════════════════════════════════════════

def fix_inline_lists_to_bullets(msgs: list[dict]) -> list[dict]:
    for m in msgs:
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        content = m["content"]
        content = re.sub(r'(?m)^(?:Opción\s*\d+\s*[—\-:]\s*)', '• ', content)
        content = re.sub(r'(?m)^(?:\d+\.\s*-?\s*)(?=\*\*)', '• ', content)
        content = re.sub(r'[🔹🚗✅🔸▪️▸►]\s*', '• ', content)
        content = re.sub(r'(?m)^\*\s+\*\*', '• **', content)
        m["content"] = content
    return msgs


def fix_double_greeting(msgs: list[dict]) -> list[dict]:
    greeting_patterns = [
        r'(?i)^¡?hola[,!]?\s',
        r'(?i)soy\s+mariana\s+de\s+autos\s+trefa',
        r'(?i)me\s+da\s+(?:mucho\s+)?gusto\s+(?:atenderte|saludarte)',
        r'(?i)qué\s+gusto\s+(?:conocerte|saludarte)',
    ]
    first_greeting_found = False
    for m in msgs:
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        has_greeting = any(re.search(p, m["content"]) for p in greeting_patterns)
        if has_greeting:
            if first_greeting_found:
                content = m["content"]
                for p in greeting_patterns:
                    content = re.sub(p + r'[^\n]*\n?', '', content, count=1)
                content = content.strip()
                if content:
                    m["content"] = content
            else:
                first_greeting_found = True
    return msgs


def fix_email_quote_references(msgs: list[dict]) -> list[dict]:
    """Regex agresivo para eliminar TODA referencia a cotización por email.

    Estrategia: primero reemplazos nucleares para párrafos completos con email,
    después reemplazos quirúrgicos por oración.
    """
    # ── FASE 1: Párrafos nucleares (usan .*? para cruzar dots en emails) ──
    nuclear_patterns = [
        # Cualquier texto con "cotización" + dirección de email (@...com)
        # Usa .*? en vez de [^.] para cruzar dots dentro de emails
        (r'(?i)(?:^|\. ).*?(?:enviar?(?:te|le)?|envié|enviarte|mandé|enviamos)\s+(?:la\s+)?cotización.*?\w+@\w[\w.]*\w.*?(?::\)|[.!?])(?:\s|$)',
         'Con gusto te ayudo a iniciar tu solicitud de financiamiento 😊. '),
        # "ya salió la cotización ... @email.com"
        (r'(?i)(?:^|\. ).*?salió\s+(?:la\s+)?cotización.*?\w+@\w[\w.]*\w.*?(?::\)|[.!?])(?:\s|$)',
         'Te ayudo con los pasos para tu solicitud de financiamiento 😊. '),
        # "ya les envié la cotización ... @email.com"
        (r'(?i)(?:^|\. ).*?(?:ya\s+)?(?:les?\s+)?envié\s+(?:la\s+)?cotización.*?\w+@\w[\w.]*\w.*?(?::\)|[.!?])(?:\s|$)',
         'Con gusto te ayudo con tu solicitud de financiamiento 😊. '),
        # Catchall: "cotización" + "@" en cualquier contexto
        (r'(?i)(?:^|\. ).*?cotización.*?\w+@\w[\w.]*\w.*?(?::\)|[.!?])(?:\s|$)',
         'Te ayudo con tu solicitud de financiamiento en línea 😊. '),
        # Enviarte cotización a email (sin la palabra cotización antes del @)
        (r'(?i)(?:^|\. ).*?enviar?(?:te|le)?\s+.*?cotización.*?(?::\)|[.!?])(?:\s|$)',
         'Con gusto te ayudo a iniciar tu solicitud de financiamiento 😊. '),
        # "Revisa tu bandeja de entrada..."
        (r'(?i)\s*[Rr]evisa\s+tu\s+bandeja[^.!?\n]*[.!?]+',
         ''),
        # "checa tu spam" / "bandeja de entrada" / "no deseados"
        (r'(?i)[^\n]*(?:spam|no\s+deseados?|bandeja\s+de\s+entrada)[^\n]*?[.!?]',
         ''),
        # "te mando la cotización formal a tu correo"
        (r'(?i)[^\n]*(?:mando|envío|enviar|mandar)\s+(?:la\s+)?cotización\s+(?:formal\s+)?(?:a\s+tu|por)\s+correo[^\n]*?(?::\)|[.!?])',
         'Te ayudo con tu solicitud de financiamiento 😊.'),
        # "te puedo enviar la cotización ... correo"
        (r'(?i)[^\n]*(?:puedo|podemos)\s+enviar\s+(?:la\s+)?cotización[^\n]*?correo[^\n]*?(?::\)|[.!?])',
         '¿Te gustaría iniciar tu solicitud de financiamiento? 😊'),
        # "envío la solicitud ... correo electrónico"
        (r'(?i)[^\n]*envío?\s+(?:la\s+)?solicitud[^\n]*?correo[^\n]*?(?::\)|[.!?])',
         '¿Te gustaría iniciar tu solicitud de financiamiento en línea? 😊'),
        # "con gusto te la envío" (in context of email)
        (r'(?i)con\s+gusto\s+te\s+la\s+envío[^\n]*?(?:correo|email)[^\n]*?(?::\)|[.!?])',
         'Con gusto te ayudo con el siguiente paso 😊.'),
        # "información ... a tu correo" + "enviarte la cotización"
        (r'(?i)[^\n]*enviarte\s+la\s+cotización[^\n]*?(?:correo|email)?[^\n]*?(?::\)|[.!?])',
         'Te ayudo con tu solicitud de financiamiento 😊.'),
    ]

    # ── FASE 2: Reemplazos quirúrgicos por oración ──
    sentence_patterns = [
        # Preguntas sobre enviar cotización
        (r'(?i)¿[^?]*(?:envíe|mande|envie|haga\s+llegar)\s+(?:una?\s+)?(?:la\s+)?cotización[^?]*\?',
         '¿Te gustaría que iniciemos tu solicitud de financiamiento? Es 100% en línea y la pre-aprobación sale en 24 horas 😊'),
        # Pedir correo/email
        (r'(?i)¿[^?]*(?:correo|email|e-mail|dirección\s+de\s+correo)[^?]*envío[^?]*\?',
         '¿Te gustaría que te guíe con los pasos para iniciar tu solicitud de financiamiento? 😊'),
        (r'(?i)¿[^?]*(?:a\s+qué|cuál\s+es\s+tu)\s+(?:correo|email|e-mail)[^?]*\?',
         '¿Te gustaría iniciar tu solicitud de financiamiento? 😊'),
        # "dame/proporciona tu correo"
        (r'(?i)(?:proporciona|dame|pásame|comparte|indícame)[^.!?]*(?:correo|email|e-mail)[^.!?]*[.!?]',
         '¿Te gustaría iniciar tu solicitud de financiamiento? 😊'),
        # "te envío/mando la cotización" (sin email address)
        (r'(?i)[^.!?\n]*(?:te|le)\s+(?:acabo\s+de\s+)?(?:envío|envié|enviaré|mando|mandé|mandaré|hago\s+llegar|enviarte|envié)\s+(?:la\s+)?cotización[^.!?\n]*[.!?]',
         'Con gusto te ayudo a iniciar tu solicitud de financiamiento 😊.'),
        # "con gusto te la envío/mando"
        (r'(?i)con\s+(?:mucho\s+)?gusto\s+(?:te|le)[^.!?]*(?:cotización|correo|email)[^.!?]*[.!?]',
         'Con gusto te ayudo con el siguiente paso 😊.'),
        # "recibirás la cotización"
        (r'(?i)[^.!?\n]*(?:recibirás|vas\s+a\s+recibir|llegará)[^.!?\n]*cotización[^.!?\n]*[.!?]',
         'Te ayudo con los siguientes pasos para avanzar.'),
        # "enviar/mandar cotización por/a correo"
        (r'(?i)(?:enviar|mandar|enviarle|mandarle|enviarte|mandarte)\s+(?:una?\s+)?(?:la\s+)?cotización\s+(?:por|a\s+tu|al|a\s+su)\s+(?:correo|email|e-mail)',
         'iniciar tu solicitud de financiamiento'),
        # "cotización por correo"
        (r'(?i)cotización\s+(?:por|a\s+tu|al|vía)\s+(?:correo|email|e-mail)',
         'solicitud de financiamiento en línea'),
        # "si me das tu correo"
        (r'(?i)si\s+me\s+(?:das|proporcionas|compartes)\s+tu\s+(?:correo|email|e-mail)[^.!?]*[.!?]',
         '¿Te gustaría iniciar tu solicitud de financiamiento? 😊'),
        # Catchall: "cotización" + "correo" en la misma oración
        (r'(?i)[^.!?\n]*cotización[^.!?\n]*correo[^.!?\n]*[.!?]',
         'Te ayudo con tu solicitud de financiamiento 😊.'),
        (r'(?i)[^.!?\n]*correo[^.!?\n]*cotización[^.!?\n]*[.!?]',
         'Te ayudo con tu solicitud de financiamiento 😊.'),
    ]

    all_patterns = nuclear_patterns + sentence_patterns

    for m in msgs:
        if m.get("role") != "assistant":
            continue
        for pattern, replacement in all_patterns:
            m["content"] = re.sub(pattern, replacement, m["content"])
        # Limpiar dobles espacios y líneas vacías resultantes
        m["content"] = re.sub(r'\n{3,}', '\n\n', m["content"])
        m["content"] = re.sub(r'  +', ' ', m["content"])
        m["content"] = m["content"].strip()
    return msgs


def ensure_expressive_greeting(msgs: list[dict]) -> list[dict]:
    for m in msgs:
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        content = m["content"]
        if ':)' in content and '😊' not in content:
            content = content.replace(':)', '😊', 1)
            m["content"] = content
        if re.match(r'^Hola,\s', content) and '¡' not in content[:10]:
            content = re.sub(r'^Hola,', '¡Hola,', content, count=1)
            m["content"] = content
        break
    return msgs


def ensure_system_prompt(msgs: list[dict]) -> list[dict]:
    if msgs and msgs[0].get("role") == "system":
        msgs[0]["content"] = "__SYSTEM_PROMPT__"
    elif msgs and msgs[0].get("role") != "system":
        msgs.insert(0, {"role": "system", "content": "__SYSTEM_PROMPT__"})
    return msgs


def fix_trefabot(msgs: list[dict]) -> list[dict]:
    for m in msgs:
        if m.get("role") == "assistant":
            m["content"] = m["content"].replace("TREFABOT", "Mariana")
            m["content"] = m["content"].replace("Trefabot", "Mariana")
            m["content"] = m["content"].replace("asesora virtual", "asesora")
            m["content"] = m["content"].replace("asistente virtual", "asesora")
    return msgs


def remove_email_tool_calls(msgs: list[dict]) -> list[dict]:
    """Elimina pares tool_call/tool_response de enviar_cotizacion_email."""
    cleaned = []
    skip_next = False
    for i, m in enumerate(msgs):
        if skip_next:
            skip_next = False
            continue
        content = m.get("content", "")
        if m.get("role") == "assistant" and "enviar_cotizacion_email" in content:
            # Saltar este tool_call y el siguiente tool response
            if i + 1 < len(msgs) and msgs[i + 1].get("role") in ("tool",):
                skip_next = True
            continue
        cleaned.append(m)
    return cleaned


def ensure_mariana_identity(msgs: list[dict]) -> list[dict]:
    """Asegura que el primer mensaje de assistant incluya 'Soy Mariana de Autos TREFA'.

    Si no se presenta como Mariana, inyecta la presentación de forma natural:
      - Si tiene "Hola...!" → inserta después del saludo
      - Si tiene "Hola" sin puntuación → reescribe inicio
      - Si no tiene "Hola" → prepend completo
    También verifica si debe preguntar el nombre del cliente.
    """
    # Buscar si el usuario ya dio su nombre antes del primer assistant
    user_gave_name = False
    first_ast_idx = None
    for i, m in enumerate(msgs):
        if m.get("role") == "system":
            continue
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            first_ast_idx = i
            break
        if m.get("role") == "user":
            content = m.get("content", "")
            if re.search(r'(?i)(me llamo|mi nombre es|soy\s+[A-ZÁÉÍÓÚ]\w+)', content):
                user_gave_name = True

    if first_ast_idx is None:
        return msgs

    m = msgs[first_ast_idx]
    content = m["content"]

    # Ya se presenta como Mariana → solo verificar pregunta de nombre
    if re.search(r'(?i)\bmariana\b', content):
        # Verificar si pregunta nombre cuando debería
        if not user_gave_name and not re.search(
            r'(?i)(tu nombre|cómo te llamas|me compartes|me dices tu|nombre para)',
            content
        ):
            content = content.rstrip()
            if not content.endswith('?'):
                content += ' ¿Me compartes tu nombre para atenderte mejor?'
            m["content"] = content
        return msgs

    # ── Inyectar "Soy Mariana de Autos TREFA" ──

    # Caso 1: "¡Hola...!" con puntuación final → insertar después
    greeting_match = re.match(
        r'(¡?[Hh]ola\b[^.!?\n]*?[.!?]+\s*(?:😊\s*)?)',
        content
    )

    if greeting_match:
        greeting = greeting_match.group(1).rstrip()
        rest = content[greeting_match.end():].lstrip()
        if '😊' not in greeting:
            greeting += ' 😊'
        m["content"] = f"{greeting} Soy Mariana de Autos TREFA. {rest}"
    elif re.search(r'(?i)\bhola\b', content):
        # Caso 2: Tiene "Hola" sin puntuación clara → reescribir inicio
        hola_match = re.match(r'(¡?[Hh]ola\b[,!]?\s*)', content)
        if hola_match:
            rest = content[hola_match.end():]
            m["content"] = f"¡Hola! 😊 Soy Mariana de Autos TREFA. {rest}"
    else:
        # Caso 3: Sin "Hola" → prepend completo
        m["content"] = f"¡Hola! 😊 Soy Mariana de Autos TREFA. {content}"

    # Si el usuario no dio nombre y el assistant no pregunta, agregar
    if not user_gave_name and not re.search(
        r'(?i)(tu nombre|cómo te llamas|me compartes|me dices tu|nombre para)',
        m["content"]
    ):
        final = m["content"].rstrip()
        # Solo agregar si no termina ya con pregunta sobre otro tema
        if not final.endswith('?'):
            m["content"] = final + ' ¿Me compartes tu nombre para atenderte mejor?'

    return msgs


def ensure_friendly_closing(msgs: list[dict]) -> list[dict]:
    """Asegura que el último mensaje del assistant tenga tono amigable con 😊."""
    for m in reversed(msgs):
        if m.get("role") != "assistant" or "<tool_call>" in m.get("content", ""):
            continue
        content = m["content"]
        # Ya tiene tono amigable
        if re.search(r'(😊|con\s+gusto|aquí\s+estoy|no\s+dudes|con\s+confianza|encantad)', content):
            break
        # Agregar 😊 antes del último signo de puntuación
        content = content.rstrip()
        if content.endswith("?"):
            content = content[:-1] + " 😊?"
        elif content.endswith("!"):
            content = content[:-1] + " 😊!"
        elif content.endswith("."):
            content = content[:-1] + " 😊."
        else:
            content = content + " 😊"
        m["content"] = content
        break
    return msgs


def has_email_text_residual(msgs: list[dict]) -> bool:
    """Detecta si aún quedan referencias textuales a cotización por email."""
    email_patterns = [
        r'(?i)enviar.*cotización.*correo',
        r'(?i)correo.*cotización',
        r'(?i)cotización.*email',
        r'(?i)mandar.*cotización.*correo',
        r'(?i)te\s+(?:la\s+)?(?:envío|mando).*correo',
        r'(?i)dame\s+tu\s+(?:correo|email)',
        r'(?i)proporciona.*(?:correo|email)',
    ]
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        content = m.get("content", "")
        for p in email_patterns:
            if re.search(p, content):
                return True
    return False


def is_good_haiku(conv: dict) -> bool:
    """Filtra conversaciones haiku — solo quedan las de alta calidad."""
    msgs = conv.get("messages", [])

    # 1. Si el usuario busca autos/precios, DEBE haber tool_calls
    has_search_need = any(
        re.search(r'(?i)(busco|quiero|tienes|tienen|precio|modelo|marca|auto|carro|camioneta|suv|sedan|pickup)', m.get("content", ""))
        for m in msgs if m.get("role") == "user"
    )
    has_tool_calls = any(
        "<tool_call>" in m.get("content", "")
        for m in msgs if m.get("role") == "assistant"
    )
    if has_search_need and not has_tool_calls:
        return False

    # 2. Sin residuo de email
    for m in msgs:
        if m.get("role") == "assistant" and "enviar_cotizacion_email" in m.get("content", ""):
            return False

    # 3. Saludo correcto — al menos tiene "hola" o "gusto"
    first_ast = None
    for m in msgs:
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            first_ast = m
            break
    if first_ast:
        if not re.search(r'(?i)(hola|gusto|bienvenid)', first_ast["content"]):
            return False

    # 4. Tool calls deben tener tool responses
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant" and "<tool_call>" in m.get("content", ""):
            if i + 1 >= len(msgs):
                return False
            nxt = msgs[i + 1]
            if nxt.get("role") not in ("tool",) and "<tool_response>" not in nxt.get("content", ""):
                return False

    # 5. No demasiado corta
    non_system = [m for m in msgs if m.get("role") != "system"]
    if len(non_system) < 4:
        return False

    return True


def fix_tool_call_mixto(msgs: list[dict]) -> list[dict]:
    """Separa mensajes que mezclan <tool_call> con texto conversacional.

    Patrón correcto:
      assistant: "<tool_call>...</tool_call>"     (solo tool_call, sin texto)
      tool:      "<tool_response>...</tool_response>"
      assistant: "Texto interpretando los resultados"

    Si un mensaje tiene ambos, lo separa:
      - El tool_call va primero (aislado)
      - El texto va DESPUÉS del tool_response correspondiente
    """
    fixed = []
    deferred_text = []  # texto que espera ir después de un tool_response

    for i, m in enumerate(msgs):
        content = m.get("content", "")

        # Insertar texto diferido después de un tool_response
        if deferred_text and m.get("role") in ("tool",):
            fixed.append(m)
            for dt in deferred_text:
                fixed.append({"role": "assistant", "content": dt})
            deferred_text = []
            continue

        if m.get("role") == "assistant" and "<tool_call>" in content:
            tool_parts = re.findall(r'<tool_call>.*?</tool_call>', content, re.DOTALL)
            text_part = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()

            if text_part and tool_parts:
                # Hay mezcla: tool_calls van aislados, texto se difiere
                for tp in tool_parts:
                    fixed.append({"role": "assistant", "content": tp})
                # Si el siguiente msg es tool response, diferir el texto
                if i + 1 < len(msgs) and msgs[i + 1].get("role") in ("tool",):
                    deferred_text.append(text_part)
                else:
                    # No hay tool response, meter texto después de los tool_calls
                    fixed.append({"role": "assistant", "content": text_part})
            else:
                fixed.append(m)
        else:
            # Si quedó texto diferido sin tool_response, insertarlo
            if deferred_text:
                for dt in deferred_text:
                    fixed.append({"role": "assistant", "content": dt})
                deferred_text = []
            fixed.append(m)

    # Vaciar residuos
    for dt in deferred_text:
        fixed.append({"role": "assistant", "content": dt})

    return fixed


def has_premature_search(msgs: list[dict]) -> bool:
    """Detecta si el assistant busca en inventario ANTES de que el usuario
    especifique qué busca (marca, modelo, tipo, presupuesto, etc.).

    Patrón incorrecto:
      user: "Hola me interesa un auto" / "Me llamo Mariano"
      assistant: <tool_call>buscar_vehiculos...</tool_call>  ← sin preguntar qué busca

    Patrón correcto:
      user: "Hola me interesa un auto"
      assistant: "¡Hola! ¿Qué tipo de vehículo buscas? ¿Tienes presupuesto?"
      user: "Busco una camioneta de hasta $400,000"
      assistant: <tool_call>buscar_vehiculos...</tool_call>
    """
    # Keywords que indican que el usuario ya especificó qué busca
    search_criteria = re.compile(
        r'(?i)('
        # Marcas (incluyendo abreviaturas y marcas menos comunes)
        r'toyota|nissan|kia|honda|chevrolet|chevy|mazda|volkswagen|vw|hyundai|ford|'
        r'dodge|ram|jeep|bmw|mercedes|audi|mitsubishi|suzuki|renault|seat|'
        r'subaru|peugeot|buick|lincoln|infiniti|acura|volvo|mini|fiat|'
        r'changan|mg|jac|baic|chirey|omoda|haval|cupra|'
        # Modelos populares
        r'corolla|sentra|forte|civic|onix|cx-?5|jetta|tucson|rav4|frontier|'
        r'camry|versa|rio|sportage|cr-?v|hr-?v|aveo|spark|march|kicks|'
        r'tacoma|hilux|ranger|f-?150|sierra|silverado|colorado|'
        r'cx-?3|cx-?30|cx-?50|mazda\s*3|mazda\s*6|'
        r'prius|yaris|supra|highlander|4runner|sequoia|tundra|'
        r'pilot|passport|odyssey|accord|fit|insight|'
        r'sorento|seltos|carnival|telluride|soul|niro|'
        r'elantra|santa\s*fe|kona|venue|palisade|ioniq|'
        r'trax|tracker|equinox|blazer|tahoe|suburban|traverse|'
        r'x-?trail|pathfinder|murano|rogue|altima|maxima|leaf|ariya|'
        r'polo|tiguan|taos|t-?cross|golf|passat|id\.?\d|'
        r'ecosport|escape|edge|explorer|bronco|maverick|'
        r'wrangler|compass|renegade|cherokee|gladiator|'
        r'outlander|eclipse\s*cross|l200|mirage|xpander|'
        # Tipos de vehículo
        r'sedan|sedán|suv|camioneta|pickup|hatchback|minivan|compacto|'
        r'familiar|deportivo|coupé|convertible|van|'
        # Presupuesto / financiamiento
        r'presupuesto|\$\s*\d|pesos|mil\b|financiam|crédito|crédit|enganche|'
        r'mensualidad|pago\s+inicial|'
        # Transmisión / motorización
        r'automático|manual|estándar|híbrido|eléctrico|turbo|'
        # Año
        r'\b20[12]\d\b|kilometraje|kilómetros|'
        # Condición / intención clara
        r'económic|barato|usado|seminuevo|nuevo|certificado'
        r')'
    )

    # Buscar la primera tool_call de búsqueda
    search_tools = {"buscar_vehiculos", "buscar_alternativas", "estadisticas_inventario"}

    for i, m in enumerate(msgs):
        if m.get("role") != "assistant" or "<tool_call>" not in m.get("content", ""):
            continue

        # ¿Es una tool de búsqueda?
        tool_match = re.search(r'"name"\s*:\s*"(\w+)"', m.get("content", ""))
        if not tool_match or tool_match.group(1) not in search_tools:
            continue

        # Revisar todos los mensajes del usuario ANTES de este tool_call
        user_msgs_before = [
            msgs[j].get("content", "")
            for j in range(i)
            if msgs[j].get("role") == "user"
        ]
        all_user_text = " ".join(user_msgs_before)

        # ¿El usuario ya especificó criterios de búsqueda?
        if not search_criteria.search(all_user_text):
            return True  # Búsqueda prematura

        # Solo verificar el primer tool_call de búsqueda
        break

    return False


def apply_v5_transforms(conv: dict) -> dict:
    """Aplica todas las transformaciones v5 a una conversación."""
    msgs = conv.get("messages", [])
    msgs = ensure_system_prompt(msgs)
    msgs = fix_trefabot(msgs)
    msgs = remove_email_tool_calls(msgs)
    msgs = fix_tool_call_mixto(msgs)
    msgs = fix_inline_lists_to_bullets(msgs)
    msgs = fix_double_greeting(msgs)
    msgs = fix_email_quote_references(msgs)
    msgs = ensure_mariana_identity(msgs)
    msgs = ensure_expressive_greeting(msgs)
    msgs = ensure_friendly_closing(msgs)
    conv["messages"] = msgs
    return conv


# ═══════════════════════════════════════════════════════════════
# VALIDACIÓN
# ═══════════════════════════════════════════════════════════════

def validate_conversation(conv: dict, min_msgs: int = 4) -> tuple[bool, list[str]]:
    issues = []
    msgs = conv.get("messages", [])

    if len(msgs) < min_msgs:
        issues.append(f"muy_corta:{len(msgs)}")

    if not msgs:
        return False, ["sin_mensajes"]

    if msgs[0].get("role") != "system":
        issues.append("no_system")

    if msgs[-1].get("role") != "assistant":
        issues.append("no_termina_assistant")

    # Dos user consecutivos
    for i in range(1, len(msgs)):
        if msgs[i].get("role") == "user" and msgs[i-1].get("role") == "user":
            issues.append("user_consecutivos")
            break

    # Tool call sin response
    for i, m in enumerate(msgs):
        content = m.get("content", "")
        if m.get("role") == "assistant" and "<tool_call>" in content:
            if i + 1 < len(msgs):
                nxt = msgs[i + 1]
                if "<tool_response>" not in nxt.get("content", "") and nxt.get("role") != "tool":
                    issues.append("tool_call_sin_response")
                    break

    # Tool call mezclado con texto
    for i, m in enumerate(msgs):
        content = m.get("content", "")
        if m.get("role") == "assistant" and "<tool_call>" in content:
            clean = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
            if clean:
                issues.append("tool_call_mixto")
                break

    # TREFABOT
    for m in msgs:
        if m.get("role") == "assistant" and "TREFABOT" in m.get("content", ""):
            issues.append("trefabot")
            break

    # Email cotización residual (tool)
    for m in msgs:
        content = m.get("content", "")
        if m.get("role") == "assistant" and "enviar_cotizacion_email" in content:
            issues.append("email_tool_residual")
            break

    # Email cotización residual (texto)
    if has_email_text_residual(msgs):
        issues.append("email_texto_residual")

    # Búsqueda prematura (assistant busca sin que el usuario diga qué quiere)
    if has_premature_search(msgs):
        issues.append("busqueda_prematura")

    # Contar "Hola"
    hola_count = 0
    for m in msgs:
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            hola_count += len(re.findall(r'(?i)\bhola\b', m.get("content", "")))

    # Doble saludo "Soy Mariana"
    greeting_count = 0
    for m in msgs:
        if m.get("role") == "assistant" and "<tool_call>" not in m.get("content", ""):
            if re.search(r'(?i)soy\s+mariana', m.get("content", "")):
                greeting_count += 1
    if greeting_count > 1:
        issues.append(f"doble_saludo:{greeting_count}")

    if hola_count > 1:
        issues.append(f"hola_multiple:{hola_count}")

    is_valid = len(issues) == 0
    return is_valid, issues, hola_count


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Consolidar dataset v5 para entrenar desde base")
    parser.add_argument("--min-msgs", type=int, default=4, help="Mínimo de mensajes por conversación (default: 4)")
    parser.add_argument("--eval-pct", type=int, default=10, help="Porcentaje para evaluación (default: 10)")
    parser.add_argument("--seed", type=int, default=42, help="Seed para reproducibilidad")
    parser.add_argument("--strict", action="store_true", help="Solo incluir conversaciones sin ningún issue")
    args = parser.parse_args()

    random.seed(args.seed)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Cargar todas las fuentes ──────────────────────────
    print("=" * 60)
    print("CONSOLIDAR DATASET V5")
    print("=" * 60)

    all_convs = []  # (conv, source_label)
    source_counts = Counter()

    # Fuentes fijas
    for path, label, _ in SOURCES:
        data = load_jsonl(path)
        for c in data:
            all_convs.append((c, label))
        source_counts[label] = len(data)
        print(f"  {label}: {len(data)} conversaciones ({path.name})")

    # Batches dinámicos (con filtro de calidad para haiku)
    for pattern, label in BATCH_PATTERNS:
        files = sorted(GOLD_UPGRADED_DIR.glob(pattern))
        count = 0
        filtered_out = 0
        for f in files:
            data = load_jsonl(f)
            for c in data:
                if label in ("batch_haiku",):
                    if not is_good_haiku(c):
                        filtered_out += 1
                        continue
                all_convs.append((c, label))
                count += 1
        source_counts[label] = count
        if filtered_out:
            print(f"  {label}: {count} buenas de {count + filtered_out} ({filtered_out} eliminadas por baja calidad)")
        else:
            print(f"  {label}: {count} conversaciones ({len(files)} archivos)")

    # Archivos cleaned (haiku_final también se filtra)
    for path, label in CLEANED_FILES:
        data = load_jsonl(path)
        count = 0
        filtered_out = 0
        for c in data:
            if "haiku" in label:
                if not is_good_haiku(c):
                    filtered_out += 1
                    continue
            all_convs.append((c, label))
            count += 1
        source_counts[label] = count
        if filtered_out:
            print(f"  {label}: {count} buenas de {count + filtered_out} ({filtered_out} eliminadas)")
        else:
            print(f"  {label}: {count} conversaciones ({path.name})")

    print(f"\n  TOTAL CARGADAS: {len(all_convs)}")

    # ── 2. Aplicar transformaciones v5 ───────────────────────
    print("\nAplicando transformaciones v5...")
    transformed = []
    for conv, label in all_convs:
        conv = apply_v5_transforms(conv)
        transformed.append((conv, label))
    print(f"  {len(transformed)} transformadas")

    # ── 3. Deduplicar ────────────────────────────────────────
    print("\nDeduplicando...")
    seen_hashes = set()
    unique = []
    dupes = 0
    for conv, label in transformed:
        h = conversation_hash(conv)
        if h in seen_hashes:
            dupes += 1
            continue
        seen_hashes.add(h)
        unique.append((conv, label))
    print(f"  {dupes} duplicados eliminados")
    print(f"  {len(unique)} conversaciones únicas")

    # ── 4. Validar ───────────────────────────────────────────
    print("\nValidando...")
    valid = []
    invalid = []
    issue_counter = Counter()
    hola_counts = []

    for conv, label in unique:
        is_ok, issues, hola_count = validate_conversation(conv, args.min_msgs)
        hola_counts.append(hola_count)

        for iss in issues:
            issue_counter[iss.split(":")[0]] += 1

        if args.strict and not is_ok:
            invalid.append((conv, label, issues))
            continue

        # En modo no-estricto, solo rechazar errores graves
        fatal = {"sin_mensajes", "no_system", "trefabot", "email_tool_residual",
                 "email_texto_residual", "tool_call_mixto", "busqueda_prematura"}
        has_fatal = any(iss.split(":")[0] in fatal for iss in issues)
        if has_fatal:
            invalid.append((conv, label, issues))
            continue

        # Limpiar metadata para output
        clean_conv = {"messages": conv["messages"]}
        clean_conv["_meta"] = {
            "source": label,
            "hola_count": hola_count,
            "issues": issues if issues else None,
            "v5": True,
        }
        valid.append(clean_conv)

    print(f"  Válidas: {len(valid)}")
    print(f"  Rechazadas: {len(invalid)}")
    if issue_counter:
        print(f"  Issues encontrados:")
        for iss, cnt in issue_counter.most_common():
            print(f"    {iss}: {cnt}")

    # ── 5. Estadísticas de "Hola" ────────────────────────────
    hola_multi = sum(1 for h in hola_counts if h > 1)
    hola_total = sum(hola_counts)
    print(f"\n  --- Estadísticas 'Hola' (pre-filtro) ---")
    print(f"  Total 'Hola': {hola_total}")
    print(f"  Convs con >1 'Hola': {hola_multi} / {len(unique)}")
    if hola_counts:
        print(f"  Promedio: {hola_total / len(hola_counts):.2f}")
        print(f"  Máximo: {max(hola_counts)}")

    # ── 6. Shuffle y split train/eval ────────────────────────
    print(f"\nSplit train/eval ({100 - args.eval_pct}/{args.eval_pct})...")
    random.shuffle(valid)

    eval_size = max(1, len(valid) * args.eval_pct // 100)
    eval_set = valid[:eval_size]
    train_set = valid[eval_size:]

    print(f"  Train: {len(train_set)}")
    print(f"  Eval:  {len(eval_set)}")

    # ── 7. Guardar ───────────────────────────────────────────
    def save_jsonl(data: list[dict], path: Path):
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    train_path = OUTPUT_DIR / "dataset_v5_train.jsonl"
    eval_path = OUTPUT_DIR / "dataset_v5_eval.jsonl"
    full_path = OUTPUT_DIR / "dataset_v5_completo.jsonl"

    save_jsonl(train_set, train_path)
    save_jsonl(eval_set, eval_path)
    save_jsonl(valid, full_path)

    print(f"\n  Guardados:")
    print(f"    {train_path}")
    print(f"    {eval_path}")
    print(f"    {full_path}")

    # ── 8. Estadísticas finales ──────────────────────────────
    # Distribución por fuente en train
    train_sources = Counter(c["_meta"]["source"] for c in train_set)
    eval_sources = Counter(c["_meta"]["source"] for c in eval_set)

    # Hola stats del set final
    final_hola = [c["_meta"]["hola_count"] for c in valid]
    final_hola_multi = sum(1 for h in final_hola if h > 1)

    stats = {
        "timestamp": datetime.now().isoformat(),
        "total_cargadas": len(all_convs),
        "duplicados_eliminados": dupes,
        "unicas": len(unique),
        "validas": len(valid),
        "rechazadas": len(invalid),
        "train": len(train_set),
        "eval": len(eval_set),
        "fuentes_cargadas": dict(source_counts),
        "fuentes_train": dict(train_sources),
        "fuentes_eval": dict(eval_sources),
        "issues": dict(issue_counter),
        "hola_stats": {
            "total": sum(final_hola),
            "convs_con_mas_de_1": final_hola_multi,
            "promedio": round(sum(final_hola) / max(len(final_hola), 1), 2),
            "maximo": max(final_hola) if final_hola else 0,
        },
        "config": {
            "min_msgs": args.min_msgs,
            "eval_pct": args.eval_pct,
            "strict": args.strict,
            "seed": args.seed,
        },
    }

    stats_path = OUTPUT_DIR / "dataset_v5_stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    # Resumen
    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)
    print(f"  Cargadas:         {len(all_convs)}")
    print(f"  Duplicados:       {dupes}")
    print(f"  Únicas:           {len(unique)}")
    print(f"  Válidas:          {len(valid)}")
    print(f"  Rechazadas:       {len(invalid)}")
    print(f"  ─────────────────────")
    print(f"  Train:            {len(train_set)}")
    print(f"  Eval:             {len(eval_set)}")
    print(f"  ─────────────────────")
    print(f"  'Hola' >1 en set final: {final_hola_multi} / {len(valid)}")
    print(f"  ─────────────────────")
    print(f"  Distribución train:")
    for src, cnt in train_sources.most_common():
        print(f"    {src}: {cnt}")
    print(f"  ─────────────────────")
    print(f"  Stats: {stats_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
