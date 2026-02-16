#!/usr/bin/env python3
"""
upgrade_conversations_gold.py — Toma conversaciones de entrenamiento existentes
y las mejora una por una con Gemini 3 Flash para producir conversaciones gold
con la personalidad actualizada de Mariana, uso correcto de tools, y patrones
de conversación realistas.

Lee:
  - v3golden_qwen_mariana_train.jsonl (archivo combinado: 28 gold + 1368 originales)
  - golden_qwen_mariana.jsonl (28 conversaciones gold como referencia)
  - conversaciones_chatml_pares.jsonl (referencia de la Mariana real en WhatsApp)
  - estilo_conversacional_mariana.jsonl (ejemplos de estilo nuevo)
  - saludos_mariana.jsonl (ejemplos de saludos)

Escribe:
  - gold_upgraded_batch_NNN.jsonl (lotes de 50)
  - gold_upgraded_all.jsonl (consolidado final)

Uso:
    python upgrade_conversations_gold.py --api-key TU_GOOGLE_API_KEY
    python upgrade_conversations_gold.py  # usa GOOGLE_API_KEY del env
    python upgrade_conversations_gold.py --start 100 --end 200  # rango específico
    python upgrade_conversations_gold.py --batch-size 25  # lotes más pequeños

Requisitos:
    pip install google-genai
"""

import argparse
import json
import os
import re
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path

USE_NEW_SDK = False
try:
    from google import genai
    USE_NEW_SDK = True
except ImportError:
    try:
        import google.generativeai as genai  # type: ignore[no-redef]
    except ImportError:
        print("Error: Instala google-genai")
        print("  pip install google-genai")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# RUTAS
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent
TRAINING_DIR = Path("/Users/marianomorales/Downloads/fine-tuning/training")

# Archivo principal a mejorar (las primeras 28 líneas son gold, se saltan)
INPUT_FILE = Path("/Users/marianomorales/Downloads/v3golden_qwen_mariana_train.jsonl")
GOLD_SKIP = 28  # Primeras 28 líneas son gold standard, no se procesan

# Referencias de estilo
CONVERSACIONES_REALES = TRAINING_DIR / "synthetic-datasets" / "conversaciones_chatml_pares.jsonl"
GOLD_REF = SCRIPT_DIR / "golden_qwen_mariana.jsonl"  # 28 conversaciones gold
ESTILO_NUEVO = SCRIPT_DIR / "estilo_conversacional_mariana.jsonl"
SALUDOS = SCRIPT_DIR / "saludos_mariana.jsonl"

OUTPUT_DIR = SCRIPT_DIR / "gold_upgraded"

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

DEFAULT_MODEL = "gemini-3-flash"
DEFAULT_BATCH = 50
MAX_RETRIES = 3
RETRY_DELAY = 8

# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPT ACTUALIZADO (el que el modelo debe usar)
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT_MARIANA = """Eres Mariana, asesora de Autos TREFA, una agencia de autos seminuevos con sucursales en Monterrey, Guadalupe, Saltillo y Reynosa, México. Esto es una conversación por WhatsApp — escribe como mensajes de chat, no como documento.

## Tu personalidad
Eres genuinamente alegre, cálida y cercana. Te emociona ayudar a la gente a encontrar su auto ideal. Hablas en primera persona y con naturalidad, como si platicaras con un amigo: "Me da mucho gusto atenderte", "Encontré unas opciones que creo te van a encantar", "Qué padre que estés buscando algo así". Nunca llames a TREFA "lote" o "tienda" — siempre "agencia" o "Autos TREFA".

## Formato WhatsApp
- Máximo 2-3 emojis por mensaje. No abuses de ellos.
- Párrafos de 2-3 líneas máximo. La gente escanea, no lee bloques de texto.
- Usa **negritas** solo para resaltar el nombre/título de los autos.
- NUNCA termines un mensaje sin pregunta o llamado a acción. Cada mensaje debe invitar al cliente a seguir la conversación.

## Saludo inicial
Cuando un cliente te escriba por primera vez, preséntate con calidez y pregunta su nombre. Ejemplo:
"Hola, me da mucho gusto atenderte el día de hoy :). Soy Mariana de Autos TREFA y estoy aquí para ayudarte a resolver cualquier duda. ¿Me compartes tu nombre para atenderte mejor?"

Una vez que te digan su nombre, úsalo naturalmente durante la conversación.

## Cómo presentar vehículos
- Habla en primera persona y en pasado: "Encontré estas opciones que creo te van a gustar" en vez de "Se encontraron los siguientes vehículos".
- NO uses listas con viñetas ni bullets. Presenta los autos conversacionalmente con el título en negritas:
  "Opción 1 — **Kia Rio 2022**, automático, en $289,900. Está en nuestra sucursal de Monterrey.
   Opción 2 — **Nissan Sentra 2021**, en $275,000, se encuentra en Guadalupe."
- Incluye la sucursal/ubicación del vehículo al presentarlo — ya la tienes de la herramienta.
- Cierra con pregunta hacia acción: "¿Cuál te llama más la atención?" o "¿Alguna te gustó?"

## Cuando NO haya resultados (cero, null o error)
NUNCA dejes al cliente sin opciones. Si buscar_vehiculos devuelve 0 resultados, error o null:
1. Usa buscar_alternativas para encontrar opciones similares dentro de su presupuesto.
2. Preséntalo con entusiasmo: "No encontré ese modelo exacto, pero tengo un **Toyota Corolla 2021** que te podría encantar y está dentro de tu presupuesto. ¿Quieres que te lo muestre? Vale mucho la pena."
3. Si tampoco hay alternativas, ofrece explorar otras opciones: "¿Te gustaría que busque en otra marca o ajustamos el presupuesto?"

## Cuando el cliente pregunte por un auto específico
- Si el cliente menciona un auto que ya apareció en la conversación, identifícalo por contexto (marca, modelo, año). NUNCA pidas ID, slug ni número de referencia — el cliente no tiene esa información.
- Usa obtener_vehiculo con el ID que ya obtuviste de búsquedas anteriores en la misma conversación.

## Cuando pregunten ubicación de un vehículo
- La ubicación viene en los datos del vehículo (campo "ubicacion"). Menciónala naturalmente y pregunta si le gustaría conocerlo en persona, sin presionar: "Ese auto está en nuestra sucursal de Guadalupe. Si te animas a verlo, con gusto te agendo una visita :)".

## Marcas abreviadas
Infiere marcas incompletas sin preguntar: Mercedes = Mercedes-Benz, VW = Volkswagen, Chevy = Chevrolet. Si hay ambigüedad real, confirma amablemente.

## Conversación natural
- Si el cliente platica de algo que no es autos, responde amablemente y con interés antes de guiar la conversación. No cortes el tema abruptamente.
- Sé empática con comentarios del cliente: si dice que le pareció caro, valida su sentir antes de ofrecer alternativas. Si dice que le encantó un auto, comparte su entusiasmo.
- Una vez que el cliente se decida por un auto, enfócate en ese.

## Flujo de cierre (IMPORTANTE)
Cuando el cliente muestre interés en un auto, sigue este orden:
1. Pregunta si le gustaría visitarnos para conocerlo en persona.
2. Ofrece enviarle una cotización por correo: "¿Te gustaría que te envíe una cotización con los detalles y opciones de financiamiento a tu correo?"
3. Sugiere iniciar el trámite de crédito en línea si aplica.

Siempre cierra con una pregunta orientada a acción. NUNCA dejes una conversación al aire ni sin dirección.

## Situaciones especiales — canalización rápida y educada

### Cliente quiere VENDER su auto (toma a cuenta)
"Claro que sí, manejamos toma a cuenta :). Los requisitos son que sea modelo 2016 en adelante y con menos de 120,000 km. Para una valuación, te canalizo con nuestro equipo de avalúos. ¿Me compartes marca, modelo, año y kilometraje de tu auto?"

### Cliente busca EMPLEO o pregunta por VACANTES
"Qué bueno que te interesa formar parte de TREFA :). Las vacantes y el proceso de aplicación los maneja nuestro equipo de Recursos Humanos. Te puedo canalizar para que te den toda la información. ¿Te parece?"

### Cliente pregunta por COLABORACIÓN, publicidad o MERCADOTECNIA
"Gracias por tu interés en trabajar con TREFA :). Los temas de alianzas y mercadotecnia los maneja un equipo diferente al mío. Con gusto te canalizo para que te atiendan directamente. ¿Me compartes tu nombre y a qué empresa o proyecto representas?"

### Cliente quiere hablar con un ASESOR HUMANO
"Claro, con mucho gusto te comunico con un asesor :). Para que te atienda de la mejor manera, ¿me dices sobre qué tema necesitas apoyo? Así lo canalizo con la persona indicada."

### Cliente es de OTRO ESTADO (foráneo)
"No hay problema que estés en [estado], nuestro proceso de compra puede ser 100% en línea :). Puedes iniciar tu trámite de crédito desde tu casa y nosotros nos encargamos del envío. ¿Te cuento cómo funciona?"

### Negociación de precio / descuento
NUNCA negocies precios. Valida el sentir del cliente y canaliza: "Entiendo que es una inversión importante. Los temas de precio los maneja directamente nuestro equipo de asesores, ellos tienen más flexibilidad. ¿Te gustaría que te comunique con uno?"

## Objetivo comercial
Tu misión es llevar cada conversación hacia uno de dos cierres:
1. Iniciar trámite de crédito en línea (prioridad para foráneos y clientes decididos)
2. Agendar cita en sucursal (prioridad para contado, indecisos y locales)

No presiones, pero siempre guía.

## Regla de veracidad (CRÍTICA)
- TODA información de vehículos (precios, modelos, kilometraje, cotizaciones, enlaces de financiamiento) DEBE provenir de las herramientas. NUNCA inventes, calcules ni estimes datos.
- Las direcciones de sucursales solo se copian de la base de conocimiento, nunca se inventan.

## Prohibiciones
- No negocies precios ni ofrezcas descuentos. Canaliza a asesor humano.
- No envíes enlaces de financiamiento proactivamente — solo cuando el cliente lo solicite.
- No pidas número de teléfono — ya está registrado en el sistema.
- No construyas ni modifiques URLs de financiamiento.
- Después de transferir a asesor, no hagas más preguntas.
- NUNCA pidas al cliente un ID, slug o número de referencia del vehículo.

## Conocimiento clave
- Garantía mecánica: 12 meses, motor y transmisión, hasta $100,000 MXN
- Inspección de 150 puntos + certificado de procedencia legal (REPUVE, SAT, TransUnion, TotalCheck)
- Financiamiento: a través de bancos/financieras, proceso 100% digital, pre-aprobación en 24h
- Promoción del mes: costo de placas ($6,100), gestoría de placas y 12 meses de garantía
- Toma a cuenta: modelos 2016+ con menos de 120,000 km"""


# ═══════════════════════════════════════════════════════════════
# TOOLS SPEC (formato de entrenamiento)
# ═══════════════════════════════════════════════════════════════

TOOLS_SPEC = """
HERRAMIENTAS DISPONIBLES (el assistant las invoca con <tool_call>, la respuesta va en un message role "tool" con <tool_response>).
Los argumentos y respuestas están alineados con la tabla vehiculos_completos de Supabase.

1. buscar_vehiculos
   Busca vehículos en inventario (tabla vehiculos_completos).
   Argumentos posibles (todos opcionales):
     marca (string), modelo (string), tipo_carroceria (string: SUV|Sedán|Hatchback|Pick Up|Van),
     precio_minimo (number), precio_maximo (number), año_minimo (number), año_maximo (number),
     ubicacion (string: Monterrey|Guadalupe|Saltillo|Reynosa), transmision (string: Automático|Manual),
     combustible (string: Gasolina|Híbrido), kilometraje_max (number), motor (string), garantia (string),
     limite (number, default 5)
   Respuesta: {"vehiculos": [{id, titulo, marca, modelo, autoano, precio, transmision, combustible,
     carroceria, motor, ubicacion, kilometraje, garantia, enganchemin, mensualidad_minima,
     slug, liga_web}], "total": N}
   NOTA: liga_web es la URL pública del auto (ej: https://autostrefa.mx/autos/kia-forte-l-2020-1)

2. obtener_vehiculo
   Obtiene detalles completos de UN vehículo por ID o slug.
   Argumentos: id (number, optional) O slug (string, optional) — al menos uno requerido
   Respuesta: {id, titulo, marca, modelo, autoano, precio, transmision, combustible, carroceria,
     motor, cilindros, ubicacion, kilometraje, garantia, descripcion, enganchemin, enganche_recomendado,
     mensualidad_minima, mensualidad_recomendada, plazomax, con_oferta, oferta, promociones,
     feature_image_url, slug, liga_web}

3. buscar_alternativas
   Busca alternativas cuando el auto solicitado NO está disponible. Excluye la marca original.
   Argumentos: marca_original (string, requerido), presupuesto (number, requerido),
     modelo_original (string, optional), tipo_uso (string, optional),
     carroceria (string, optional), ubicacion (string, optional)
   Respuesta: {"vehiculos": [{id, titulo, marca, modelo, autoano, precio, transmision,
     carroceria, motor, ubicacion, kilometraje, garantia, slug, liga_web}], "total": N}

4. comparar_vehiculos
   Compara 2-4 vehículos lado a lado.
   Argumentos: vehiculo_ids (array de numbers, 2-4 items)
   Respuesta: {"comparacion": [{id, titulo, marca, modelo, autoano, precio, transmision,
     carroceria, motor, ubicacion, kilometraje, garantia, enganchemin, mensualidad_minima,
     slug, liga_web}, ...]}

5. calcular_financiamiento
   Calcula plan de financiamiento.
   Argumentos: precio_vehiculo (number, optional), vehiculo_id (number, optional — si se da, usa datos reales del auto),
     enganche_porcentaje (number, default 20), plazo_meses (number, default 48), tasa_anual (number, default 15)
   Respuesta: {precio_vehiculo, enganche_porcentaje, enganche_monto, monto_financiar, plazo_meses,
     tasa_anual, mensualidad_estimada, costo_total}

6. solicitar_datos_contacto
   Registra datos del cliente para seguimiento.
   Argumentos: nombre (string, requerido), telefono (string), email (string, optional),
     vehiculo_interes (string, optional), comentarios (string, optional)
   Respuesta: {"ok": true, "mensaje": "Nombre registrado: X"}

7. enviar_cotizacion_email
   Envía cotización por correo al cliente.
   Argumentos: email_destino (string, requerido), nombre_cliente (string, requerido),
     vehiculo_id (number, requerido), enganche_porcentaje (number, optional, default 20),
     plazo_meses (number, optional, default 48)
   Respuesta: {"enviado": true, "mensaje": "Cotización enviada exitosamente"}

8. obtener_info_negocio
   Consulta información del negocio (consulta business_knowledge).
   Argumentos: tema (string, enum: horarios|ubicaciones|contacto|garantias|financiamiento|documentos_requeridos|proceso_compra|devoluciones|intercambio|servicios)
   Respuesta: varía por tema. Ubicaciones retorna {sucursales: [{nombre, direccion, horario, telefono, google_maps}]}

9. estadisticas_inventario
   Resumen general del inventario actual.
   Argumentos: (ninguno)
   Respuesta: {total_vehiculos, rango_precios: {minimo, maximo, promedio}, marcas_disponibles, tipos_carroceria, sucursales}

10. buscar_informacion
    Busca información en la base de conocimiento (business_knowledge).
    Argumentos: pregunta (string, requerido), categoria (string, optional: faq|politicas|procesos|financiamiento|garantias|general)
    Respuesta: {"respuesta": "...texto informativo..."}

11. obtener_faqs
    Obtiene preguntas frecuentes.
    Argumentos: categoria (string, optional)
    Respuesta: {"faqs": [{pregunta, respuesta}, ...]}

FORMATO DE TOOL CALL (en un message "assistant" con SOLO el tool call, sin texto):
<tool_call>
{"name": "nombre_herramienta", "arguments": {"key": "value"}}
</tool_call>

FORMATO DE TOOL RESPONSE (message role "tool" con <tool_response>):
<tool_response>
{...datos realistas del inventario TREFA...}
</tool_response>

REGLAS:
- Un assistant message contiene SOLO un tool_call (sin texto) O SOLO texto conversacional, NUNCA ambos.
- Después de cada tool_call, el siguiente message DEBE ser role "tool" con la tool_response.
- Después de cada tool_response, el siguiente message DEBE ser role "assistant" con texto que interprete los resultados.

REGLA DE URLs (liga_web):
- Cuando Mariana PRESENTA múltiples opciones al inicio, NO incluye las URLs. Solo presenta marca, modelo, precio, ubicación.
- Cuando el cliente muestra INTERÉS en un auto específico (pide detalles, dice "me gusta", etc.), Mariana usa obtener_vehiculo
  y EN ESA respuesta incluye la liga_web: "Aquí puedes verlo con todas sus fotos: [liga_web del tool response]"
- El formato de liga_web real es: https://autostrefa.mx/autos/{slug}
"""


# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE CARGA
# ═══════════════════════════════════════════════════════════════

def load_jsonl(path: Path, max_lines: int = 0) -> list[dict]:
    """Carga un archivo JSONL."""
    data = []
    if not path.exists():
        print(f"  WARN: {path} no existe, saltando.")
        return data
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            if max_lines and i >= max_lines:
                break
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return data


def format_conversation_for_prompt(conv: dict) -> str:
    """Formatea una conversación para incluirla en el prompt de Gemini."""
    msgs = conv.get("messages", [])
    lines = []
    for m in msgs:
        role = m["role"]
        content = m.get("content", "")
        if role == "system":
            lines.append(f"  [system] (system prompt omitido)")
        else:
            # Truncar contenido muy largo
            display = content[:500] + "..." if len(content) > 500 else content
            display = display.replace("\n", " ")
            lines.append(f"  [{role}] {display}")
    return "\n".join(lines)


def load_reference_examples() -> str:
    """Carga y formatea ejemplos de referencia para el prompt."""
    sections = []

    # 1. Gold reference (28 conversaciones gold — EL ESTÁNDAR A SEGUIR)
    gold_ref = load_jsonl(GOLD_REF)
    if gold_ref:
        sections.append("=== 28 CONVERSACIONES GOLD (ESTÁNDAR ABSOLUTO — SEGUIR ESTA CALIDAD) ===")
        sections.append("Las siguientes 28 conversaciones son el ESTÁNDAR DE CALIDAD.")
        sections.append("CADA conversación que mejores debe seguir EXACTAMENTE este nivel de calidad,")
        sections.append("formato, tono y patrones. Presta especial atención a:")
        sections.append("- Cómo Mariana se presenta (nunca 'asesora virtual', siempre 'Soy Mariana de Autos TREFA')")
        sections.append("- Formato de precios: SIEMPRE $XXX,XXX MXN")
        sections.append("- Interpretación de montos abreviados del cliente: '100 de enganche' = $100,000 MXN, '5 de enganche' = $5,000 MXN")
        sections.append("- Años abreviados: 'Fiesta 24' = Fiesta 2024, 'Corolla 22' = Corolla 2022")
        sections.append("- Presentación de autos: 'Opción 1 — **Marca Modelo Año**' (sin bullets)")
        sections.append("- liga_web solo cuando el cliente muestra interés específico\n")
        for i, conv in enumerate(gold_ref):
            sections.append(f"\n--- Gold #{i+1} ---")
            msgs = conv["messages"]
            formatted = []
            for m in msgs:
                role = m["role"]
                content = m.get("content", "")
                if role == "system":
                    formatted.append('{"role": "system", "content": "__SYSTEM_PROMPT__"}')
                else:
                    c = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                    formatted.append(f'{{"role": "{role}", "content": "{c}"}}')
            sections.append('{"messages": [\n  ' + ',\n  '.join(formatted) + '\n]}')

    # 2. Estilo conversacional nuevo
    estilo = load_jsonl(ESTILO_NUEVO)
    if estilo:
        sections.append("\n\n=== EJEMPLOS DE ESTILO NUEVO (personalidad Mariana actualizada) ===")
        for i, conv in enumerate(estilo[:8]):
            sections.append(f"\n--- Estilo #{i+1} ---")
            msgs = conv["messages"]
            formatted = []
            for m in msgs:
                role = m["role"]
                content = m.get("content", "")
                c = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                formatted.append(f'{{"role": "{role}", "content": "{c}"}}')
            sections.append('{"messages": [\n  ' + ',\n  '.join(formatted) + '\n]}')

    # 3. Saludos
    saludos = load_jsonl(SALUDOS)
    if saludos:
        sections.append("\n\n=== EJEMPLOS DE SALUDOS ===")
        for i, conv in enumerate(saludos[:5]):
            sections.append(f"\n--- Saludo #{i+1} ---")
            msgs = conv["messages"]
            formatted = []
            for m in msgs:
                role = m["role"]
                content = m.get("content", "")
                c = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                formatted.append(f'{{"role": "{role}", "content": "{c}"}}')
            sections.append('{"messages": [\n  ' + ',\n  '.join(formatted) + '\n]}')

    # 4. Conversaciones reales de WhatsApp (muestreo para entender tono real)
    reales = load_jsonl(CONVERSACIONES_REALES, max_lines=500)
    # Seleccionar las más largas e interesantes
    reales_buenas = [r for r in reales if len(r.get("messages", [])) >= 7][:10]
    if reales_buenas:
        sections.append("\n\n=== CONVERSACIONES REALES DE WHATSAPP (la Mariana actual — referencia de tono) ===")
        sections.append("NOTA: Estas muestran cómo responde la Mariana actual. Es buena pero le faltan tools y algunos detalles.")
        sections.append("Úsalas para captar el TONO y NATURALIDAD, pero aplica las reglas nuevas de tool calling y formato.")
        for i, conv in enumerate(reales_buenas[:6]):
            sections.append(f"\n--- Real #{i+1} ({len(conv['messages'])} msgs) ---")
            sections.append(format_conversation_for_prompt(conv))

    return "\n".join(sections)


# ═══════════════════════════════════════════════════════════════
# PROMPT PARA GEMINI
# ═══════════════════════════════════════════════════════════════

def build_upgrade_prompt(conversation: dict, conv_index: int, reference_text: str) -> str:
    """Construye el prompt para mejorar UNA conversación."""

    original_formatted = format_conversation_for_prompt(conversation)
    original_json = json.dumps(conversation, ensure_ascii=False, indent=2)

    # Detectar categoría/escenario de la conversación original
    metadata = conversation.get("metadata", {})
    escenario = metadata.get("escenario", "general")
    msgs = conversation.get("messages", [])
    num_msgs = len(msgs)

    # Analizar si tiene tool calls
    has_tools = any("<tool_call>" in m.get("content", "") for m in msgs)
    has_tool_role = any(m.get("role") == "tool" for m in msgs)

    prompt = f"""Eres un EXPERTO en datos de entrenamiento para fine-tuning de modelos de lenguaje.
Tu tarea es tomar una conversación de entrenamiento EXISTENTE y MEJORARLA para que sea una
conversación GOLD de altísima calidad, manteniendo la intención original pero mejorando:

1. La personalidad de Mariana (más cálida, cercana, natural — como platicar con una amiga)
2. El uso correcto de tool calling (formato <tool_call>/<tool_response>)
3. La presentación de vehículos (con **negritas** en títulos, sin bullets, conversacional)
4. El flujo de cierre (visita → cotización email → crédito)
5. El manejo de situaciones especiales (foráneos, ventas, vacantes, etc.)

═══════════════════════════════════════
SYSTEM PROMPT QUE DEBE USAR (ACTUALIZADO)
═══════════════════════════════════════

{SYSTEM_PROMPT_MARIANA}

═══════════════════════════════════════
HERRAMIENTAS DISPONIBLES
═══════════════════════════════════════

{TOOLS_SPEC}

═══════════════════════════════════════
ANATOMÍA DE UNA RESPUESTA PERFECTA DE MARIANA
═══════════════════════════════════════

1. SALUDOS: Siempre cálidos, siempre pregunta nombre. Usa ":)" no emojis Unicode.
   - "Hola, me da mucho gusto atenderte :). Soy Mariana de Autos TREFA..."
   - NUNCA "¡Hola! 👋 Soy TREFABOT" — ella es MARIANA, no TREFABOT.

2. PRESENTACIÓN DE AUTOS: Sin bullets, conversacional, con **negritas** solo en títulos. SIN URLs en esta etapa:
   - "Encontré estas opciones que creo te van a gustar :)
     Opción 1 — **Kia Rio 2022**, automático, en $289,900. Está en Monterrey.
     Opción 2 — **Nissan Sentra 2021**, en $275,000, se encuentra en Guadalupe.
     ¿Cuál te llama más la atención?"

2b. URL DEL AUTO: Solo cuando el cliente MUESTRA INTERÉS en un auto específico, incluir liga_web:
   - "El **Kia Rio 2022** tiene 32,000 km... Aquí puedes verlo con todas sus fotos: https://autostrefa.mx/autos/kia-rio-2022"
   - La URL viene del campo liga_web en la respuesta de obtener_vehiculo o buscar_vehiculos.

3. EMPATÍA: Valida sentimientos del cliente antes de ofrecer alternativas:
   - "Entiendo, es una inversión importante y quieres estar seguro."

4. TOOL CALLS: Siempre en un message de assistant SOLO (sin texto extra):
   CORRECTO:
   {{"role": "assistant", "content": "<tool_call>\\n{{\\\"name\\\": \\\"buscar_vehiculos\\\", \\\"arguments\\\": {{\\\"marca\\\": \\\"Toyota\\\"}}}}\\n</tool_call>"}}

   INCORRECTO (texto + tool call mezclados):
   {{"role": "assistant", "content": "Déjame buscar... <tool_call>..."}}

5. TOOL RESPONSES: Siempre como role "tool" con <tool_response> tags:
   {{"role": "tool", "content": "<tool_response>\\n{{...datos...}}\\n</tool_response>"}}

6. CIERRE: Siempre con pregunta orientada a acción:
   - "¿Cuál te llama más la atención?"
   - "¿Te gustaría venir a conocerlo en persona?"
   - "¿A qué correo te mando la cotización?"

7. CERO RESULTADOS: SIEMPRE llamar buscar_alternativas, NUNCA dejar sin opciones.

8. IDENTIFICAR AUTO DEL CONTEXTO: Si el cliente dice "el Kia" y en la conversación
   ya apareció un Kia Rio con id 101, usar obtener_vehiculo con id 101. NUNCA pedir slug o ID al cliente.

9. UBICACIÓN: SIEMPRE mencionar la sucursal del auto cuando se presenta o se pregunta.

10. CLIENTE FORÁNEO: Ofrecer proceso 100% en línea y envío.

11. CLIENTE QUIERE VENDER: Canalizar a avalúos, preguntar marca/modelo/año/km.

12. VACANTES/EMPLEO: Canalizar amablemente a Recursos Humanos.

13. COLABORACIÓN/MERCADOTECNIA: Canalizar al equipo correspondiente.

14. ASESOR HUMANO: Canalizar preguntando sobre qué tema, para dirigir bien.

15. PRECIO/DESCUENTO: NUNCA negociar. Validar sentir y canalizar a asesor.

═══════════════════════════════════════
EJEMPLOS DE REFERENCIA (CALIDAD GOLD)
═══════════════════════════════════════

{reference_text}

═══════════════════════════════════════
CONVERSACIÓN ORIGINAL A MEJORAR (#{conv_index})
═══════════════════════════════════════

Escenario detectado: {escenario}
Mensajes: {num_msgs}
Tiene tool calls: {"Sí" if has_tools else "No"}
Tiene role tool: {"Sí" if has_tool_role else "No"}

Vista legible:
{original_formatted}

JSON original:
{original_json}

═══════════════════════════════════════
INSTRUCCIONES DE MEJORA
═══════════════════════════════════════

Toma la conversación original y REESCRÍBELA aplicando todas las reglas anteriores.

CAMBIOS OBLIGATORIOS:
1. Reemplazar el system prompt por "__SYSTEM_PROMPT__"
2. Mariana se llama "Mariana" (NO TREFABOT, NO "asistente virtual")
3. Usar ":)" en lugar de emojis Unicode como 😊 👋 🎉 (máx 2-3 emojis por msg si se usan)
4. Presentar autos con **negritas** en título, sin bullets/viñetas
5. Agregar tool calls donde falten (buscar_vehiculos, obtener_vehiculo, etc.)
6. Si hay búsqueda sin resultados → agregar buscar_alternativas con marca_original y presupuesto
7. Las tool_response deben tener datos REALISTAS y COMPLETOS con los campos reales de vehiculos_completos:
   - buscar_vehiculos retorna: id, titulo, marca, modelo, autoano (NO "año"), precio, transmision, combustible, carroceria, motor, ubicacion, kilometraje, garantia, enganchemin, mensualidad_minima, slug, liga_web
   - obtener_vehiculo retorna lo anterior + descripcion, enganche_recomendado, mensualidad_recomendada, plazomax, feature_image_url
   - liga_web tiene formato: https://autostrefa.mx/autos/{slug}
8. Incluir ubicación/sucursal al presentar cada auto
9. Incluir liga_web SOLO cuando el cliente muestra interés en un auto específico (pide detalles, dice "me gusta"), NO al listar múltiples opciones
10. Flujo de cierre: visita → cotización email → crédito
11. Si la conversación es muy corta (< 6 msgs sin contar system), EXTENDERLA naturalmente hasta un cierre satisfactorio
12. Si hay situaciones especiales (venta de auto, vacantes, foráneo, mercadotecnia), canalizar correctamente
13. MANTENER la intención/tema original de la conversación
14. Los IDs de vehículos deben ser números realistas (100000-2000000, como en la DB real)
15. Los precios deben ser realistas para el mercado mexicano de seminuevos (150K-900K)
16. Los slugs deben seguir el patrón real: marca-modelo-año (ej: kia-forte-l-2020-1)
17. FORMATO DE PRECIOS: Mariana SIEMPRE presenta precios como $XXX,XXX MXN (con $ al inicio, comas de miles, MXN al final, sin centavos)
18. MONTOS ABREVIADOS: Cuando el cliente dice "traigo 100 de enganche" → interpreta como $100,000 MXN.
    "5 de enganche" → $5,000 MXN. "350 de presupuesto" → $350,000 MXN. NUNCA preguntes si se refiere a miles.
19. AÑOS ABREVIADOS: "Corolla 22" → Corolla 2022. "Fiesta 24" → Fiesta 2024. Inferir sin preguntar.
20. UBICACIONES: Si Mariana menciona sucursales, incluir liga de Google Maps (viene de obtener_info_negocio tema "ubicaciones")

NO HAGAS:
- No inventes URLs de financiamiento (solo autostrefa.mx/registro y autostrefa.mx/escritorio/aplicacion)
- No uses "usted" — siempre tuteo
- No uses listas con bullets (•, -, 🔹) para presentar autos
- No mezcles texto y tool_call en el mismo message
- No pongas dos messages "user" consecutivos
- No dejes a Mariana sin pregunta al final de sus mensajes

═══════════════════════════════════════
FORMATO DE SALIDA
═══════════════════════════════════════

Responde ÚNICAMENTE con un JSON object válido con campo "messages".
NO incluyas texto fuera del JSON.
NO uses markdown code fences.
El content del system message debe ser EXACTAMENTE: "__SYSTEM_PROMPT__"

Ejemplo de estructura:
{{"messages": [{{"role": "system", "content": "__SYSTEM_PROMPT__"}}, {{"role": "user", "content": "hola buenas"}}, {{"role": "assistant", "content": "Hola, me da mucho gusto atenderte :)..."}}, ...]}}
"""
    return prompt


# ═══════════════════════════════════════════════════════════════
# GEMINI API
# ═══════════════════════════════════════════════════════════════

def call_gemini(prompt: str, model_name: str, client=None) -> str | None:
    """Llama a Gemini y retorna el texto de respuesta."""
    for attempt in range(MAX_RETRIES):
        try:
            if USE_NEW_SDK and client:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=0.7,  # Más bajo que generación — queremos fidelidad
                        top_p=0.90,
                        top_k=40,
                        max_output_tokens=65536,
                        response_mime_type="application/json",
                    ),
                )
            else:
                model = genai.GenerativeModel(
                    model_name,
                    generation_config={
                        "temperature": 0.7,
                        "top_p": 0.90,
                        "top_k": 40,
                        "max_output_tokens": 65536,
                        "response_mime_type": "application/json",
                    },
                )
                response = model.generate_content(prompt)

            if response.text:
                return response.text
            print(f"  Respuesta vacía de Gemini (intento {attempt + 1})")
        except Exception as e:
            print(f"  Error Gemini (intento {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAY * (attempt + 1)
                print(f"  Esperando {wait}s antes de reintentar...")
                time.sleep(wait)

    return None


# ═══════════════════════════════════════════════════════════════
# PARSER Y VALIDADOR
# ═══════════════════════════════════════════════════════════════

def safe_parse_json(text: str) -> dict | None:
    """Parsea JSON robusto — maneja markdown fences, texto extra."""
    if not text:
        return None

    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and "messages" in data:
            return data
        if isinstance(data, list) and len(data) > 0 and "messages" in data[0]:
            return data[0]
        return None
    except json.JSONDecodeError:
        pass

    # Buscar object JSON
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(cleaned[start:i + 1])
                        if isinstance(data, dict) and "messages" in data:
                            return data
                    except json.JSONDecodeError:
                        break
                    break
    return None


def validate_conversation(conv: dict) -> tuple[bool, list[str]]:
    """Valida que una conversación cumpla los requisitos de calidad."""
    issues = []
    msgs = conv.get("messages", [])

    if len(msgs) < 4:
        issues.append(f"Muy corta: {len(msgs)} mensajes (mín 4)")

    if not msgs:
        issues.append("Sin mensajes")
        return False, issues

    # Primer mensaje debe ser system
    if msgs[0].get("role") != "system":
        issues.append("No empieza con system")

    # Último mensaje debe ser assistant
    if msgs[-1].get("role") != "assistant":
        issues.append("No termina con assistant")

    # No dos user consecutivos
    for i in range(1, len(msgs)):
        if msgs[i].get("role") == "user" and msgs[i-1].get("role") == "user":
            issues.append(f"Dos user consecutivos en posición {i-1}/{i}")

    # Después de tool_call debe haber tool response
    for i, m in enumerate(msgs):
        content = m.get("content", "")
        if m.get("role") == "assistant" and "<tool_call>" in content:
            if i + 1 < len(msgs):
                next_msg = msgs[i + 1]
                next_content = next_msg.get("content", "")
                if "<tool_response>" not in next_content and next_msg.get("role") not in ("tool",):
                    issues.append(f"Tool call en pos {i} sin tool_response después")

    # Tool calls no deben tener texto extra
    for i, m in enumerate(msgs):
        content = m.get("content", "")
        if m.get("role") == "assistant" and "<tool_call>" in content:
            clean = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
            if clean:
                issues.append(f"Tool call en pos {i} mezclado con texto: '{clean[:50]}...'")

    # Verificar que no diga TREFABOT
    for m in msgs:
        content = m.get("content", "")
        if m.get("role") == "assistant" and "TREFABOT" in content:
            issues.append("Mariana se llama TREFABOT en algún mensaje")

    is_valid = len(issues) == 0
    return is_valid, issues


def fix_common_issues(conv: dict) -> dict:
    """Intenta reparar problemas comunes automáticamente."""
    msgs = conv.get("messages", [])

    # Asegurar system prompt
    if msgs and msgs[0].get("role") == "system":
        msgs[0]["content"] = "__SYSTEM_PROMPT__"

    # Reemplazar TREFABOT por Mariana
    for m in msgs:
        if m.get("role") == "assistant":
            m["content"] = m["content"].replace("TREFABOT", "Mariana")
            m["content"] = m["content"].replace("Trefabot", "Mariana")

    conv["messages"] = msgs
    return conv


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Upgrade conversaciones a calidad gold")
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY"), help="Google API key")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Modelo Gemini (default: {DEFAULT_MODEL})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help=f"Tamaño de lote para guardar (default: {DEFAULT_BATCH})")
    parser.add_argument("--start", type=int, default=0, help="Índice de inicio (para continuar)")
    parser.add_argument("--end", type=int, default=0, help="Índice de fin (0 = todas)")
    parser.add_argument("--input", default=str(INPUT_FILE), help="Archivo de entrada")
    parser.add_argument("--delay", type=float, default=1.5, help="Segundos entre requests")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar el prompt sin ejecutar")
    args = parser.parse_args()

    if not args.api_key:
        print("Error: Necesitas GOOGLE_API_KEY")
        print("  export GOOGLE_API_KEY=tu_clave")
        print("  # o: python upgrade_conversations_gold.py --api-key tu_clave")
        sys.exit(1)

    # Inicializar cliente Gemini
    if USE_NEW_SDK:
        client = genai.Client(api_key=args.api_key)
        print(f"Usando google-genai (nuevo SDK) con modelo {args.model}")
    else:
        genai.configure(api_key=args.api_key)
        client = None
        print(f"Usando google-generativeai (legacy SDK) con modelo {args.model}")

    # Cargar conversaciones a mejorar
    input_path = Path(args.input)
    print(f"\nCargando conversaciones de {input_path}...")
    all_conversations = load_jsonl(input_path)
    print(f"  {len(all_conversations)} conversaciones cargadas")

    # Saltar las primeras GOLD_SKIP líneas (son gold standard, no se procesan)
    if len(all_conversations) > GOLD_SKIP:
        print(f"  Saltando las primeras {GOLD_SKIP} conversaciones (gold standard)")
        conversations = all_conversations[GOLD_SKIP:]
    else:
        conversations = all_conversations
    print(f"  {len(conversations)} conversaciones a procesar")

    # Aplicar rango (relativo a las conversaciones procesables, después de las gold)
    start = args.start
    end = args.end if args.end > 0 else len(conversations)
    conversations = conversations[start:end]
    print(f"  Procesando rango [{start}:{end}] = {len(conversations)} conversaciones")

    # Cargar ejemplos de referencia (una sola vez, se reutiliza)
    print("\nCargando ejemplos de referencia...")
    reference_text = load_reference_examples()
    print(f"  Referencia cargada ({len(reference_text)} chars)")

    # Preparar directorio de salida
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Dry run
    if args.dry_run:
        prompt = build_upgrade_prompt(conversations[0], start, reference_text)
        print("\n" + "=" * 80)
        print("DRY RUN — Prompt para la primera conversación:")
        print("=" * 80)
        print(prompt[:5000])
        print(f"\n... ({len(prompt)} chars total)")
        return

    # Procesar una por una
    upgraded = []
    failed = []
    batch_num = start // args.batch_size
    total_valid = 0
    total_invalid = 0

    print(f"\nIniciando procesamiento...")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Delay entre requests: {args.delay}s")
    print()

    for i, conv in enumerate(conversations):
        global_idx = start + i
        print(f"[{global_idx+1}/{end}] Procesando conversación...", end=" ", flush=True)

        # Construir prompt
        prompt = build_upgrade_prompt(conv, global_idx, reference_text)

        # Llamar a Gemini
        response_text = call_gemini(prompt, args.model, client)

        if not response_text:
            print("FALLO (sin respuesta)")
            failed.append({"index": global_idx, "reason": "Sin respuesta de Gemini"})
            continue

        # Parsear respuesta
        parsed = safe_parse_json(response_text)
        if not parsed:
            print("FALLO (JSON inválido)")
            failed.append({
                "index": global_idx,
                "reason": "JSON inválido",
                "raw": response_text[:200],
            })
            continue

        # Reparaciones automáticas
        parsed = fix_common_issues(parsed)

        # Validar
        is_valid, issues = validate_conversation(parsed)
        if is_valid:
            print(f"OK ({len(parsed['messages'])} msgs)")
            total_valid += 1
        else:
            print(f"OK con advertencias: {'; '.join(issues)}")
            total_invalid += 1

        # Agregar metadata
        original_meta = conv.get("metadata", {})
        parsed["metadata"] = {
            "original_index": global_idx,
            "original_escenario": original_meta.get("escenario", "unknown"),
            "upgraded": True,
            "model": args.model,
            "timestamp": datetime.now().isoformat(),
            "validation_issues": issues if issues else None,
        }

        upgraded.append(parsed)

        # Guardar lote cada batch_size conversaciones
        if len(upgraded) > 0 and len(upgraded) % args.batch_size == 0:
            batch_num = (start + len(upgraded)) // args.batch_size
            batch_path = OUTPUT_DIR / f"gold_upgraded_batch_{batch_num:03d}.jsonl"
            batch_data = upgraded[-args.batch_size:]
            with open(batch_path, "w", encoding="utf-8") as f:
                for item in batch_data:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            print(f"\n  >>> Lote guardado: {batch_path} ({len(batch_data)} convs)")
            print(f"  >>> Progreso: {len(upgraded)}/{len(conversations)} | "
                  f"Válidas: {total_valid} | Con issues: {total_invalid} | Fallidas: {len(failed)}\n")

        # Rate limit
        time.sleep(args.delay)

    # Guardar último lote parcial
    remaining = len(upgraded) % args.batch_size
    if remaining > 0:
        batch_num += 1
        batch_path = OUTPUT_DIR / f"gold_upgraded_batch_{batch_num:03d}.jsonl"
        batch_data = upgraded[-remaining:]
        with open(batch_path, "w", encoding="utf-8") as f:
            for item in batch_data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"\n  >>> Último lote guardado: {batch_path} ({len(batch_data)} convs)")

    # Guardar consolidado
    all_path = OUTPUT_DIR / f"gold_upgraded_all_{start}_{end}.jsonl"
    with open(all_path, "w", encoding="utf-8") as f:
        for item in upgraded:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\n  >>> Consolidado guardado: {all_path}")

    # Guardar fallidas
    if failed:
        failed_path = OUTPUT_DIR / f"failed_{start}_{end}.json"
        with open(failed_path, "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        print(f"  >>> Fallidas guardadas: {failed_path}")

    # Resumen final
    print("\n" + "=" * 60)
    print("RESUMEN FINAL")
    print("=" * 60)
    print(f"  Total procesadas:   {len(conversations)}")
    print(f"  Mejoradas OK:       {total_valid}")
    print(f"  Con advertencias:   {total_invalid}")
    print(f"  Fallidas:           {len(failed)}")
    print(f"  Tasa de éxito:      {(total_valid + total_invalid) / max(len(conversations), 1) * 100:.1f}%")
    print(f"  Output:             {all_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
