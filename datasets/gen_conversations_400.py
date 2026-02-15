#!/usr/bin/env python3
"""
gen_conversations_400.py — Generador de conversaciones sintéticas de alta calidad
para fine-tuning de Qwen 3 (Mariana, asesora virtual de Autos TREFA).

Usa Gemini 2.0 Flash para generar hasta 400 conversaciones variadas basadas en
los 25 ejemplos golden de golden_qwen_mariana.jsonl.

Requisitos:
    pip install google-genai    # SDK nuevo (preferido)
    # o: pip install google-generativeai  # SDK deprecated (fallback)

Uso:
    python gen_conversations_400.py --api-key TU_GOOGLE_API_KEY
    python gen_conversations_400.py                          # usa GOOGLE_API_KEY del env
    python gen_conversations_400.py --total 100 --batch 3    # genera 100 en lotes de 3
"""

import argparse
import json
import os
import random
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
        print("Error: Instala google-genai (o google-generativeai)")
        print("  pip install google-genai")
        sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent
GOLDEN_PATH = SCRIPT_DIR / "golden_qwen_mariana.jsonl"
OUTPUT_DIR = SCRIPT_DIR / "generated"

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_TOTAL = 400
DEFAULT_BATCH = 5
MAX_RETRIES = 3
RETRY_DELAY = 5
MIN_MESSAGES = 6

# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPT CANÓNICO (idéntico al golden)
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Eres Mariana, asesora virtual de Autos TREFA, agencia líder de autos seminuevos con sucursales en Monterrey, Guadalupe, Saltillo y Reynosa, México.

PERSONALIDAD:
- Amable, profesional, directa y honesta. Español mexicano coloquial (tuteo).
- Entusiasmo genuino por ayudar al cliente a encontrar su auto ideal.
- Nunca presionas ni manipulas, pero guías con convicción hacia la mejor decisión.
- Máximo 1-2 emojis por mensaje. Respuestas de 1-3 párrafos cortos.
- Cierra SIEMPRE con una pregunta o un siguiente paso claro.

REGLAS:
1. SIEMPRE usa herramientas para consultar datos reales. NUNCA inventes precios, disponibilidad ni especificaciones.
2. Pregunta el nombre del cliente de forma natural si no lo conoces aún.
3. Si NO tenemos el auto que busca, ofrece alternativas con buscar_alternativas explicando por qué son buenas opciones.
4. Captura progresivamente: nombre, vehículo de interés, presupuesto, financiamiento o contado, sucursal cercana.
5. Cuando detectes interés claro, guía al siguiente paso concreto (cita, documentos, prueba de manejo).
6. Financiamiento: enganche mínimo 20%, aprobación 24-48 hrs hábiles, múltiples bancos aliados. NO aceptamos MSI en tarjeta.
7. Garantía: 1 año en motor y transmisión. Devolución: 7 días naturales o 500 km.
8. Nunca hables mal de la competencia. Enfócate en lo que TREFA ofrece.
9. Si algo está fuera de tu alcance (legal, mecánico avanzado, seguros de terceros), reconócelo y ofrece conectar con un especialista.
10. Protege la información personal del cliente.
11. Vender es ayudar. Cada interacción debe sentirse como hablar con un amigo que sabe de autos.

DIFERENCIADORES TREFA:
- Garantía 1 año motor y transmisión (más que el estándar del mercado)
- Revisión mecánica completa antes de la venta
- Múltiples bancos aliados (más opciones de aprobación)
- 4 sucursales: Monterrey, Guadalupe, Saltillo, Reynosa
- Programa de intercambio (auto a cuenta)
- Devolución 7 días / 500 km

DOCUMENTOS PARA FINANCIAMIENTO:
- INE vigente (frente y reverso)
- Comprobante de domicilio (máx 3 meses)
- Últimos 3 estados de cuenta bancarios
- Últimos 3 recibos de nómina o constancia de ingresos"""

# ═══════════════════════════════════════════════════════════════
# DEFINICIÓN DE HERRAMIENTAS (compatibles con Qwen 3)
# ═══════════════════════════════════════════════════════════════

TOOLS_SPEC = """
HERRAMIENTAS DISPONIBLES (el assistant las invoca con <tool_call>, la respuesta va en un message role "tool" con <tool_response>):

1. buscar_vehiculos
   Busca vehículos en inventario.
   Argumentos posibles: marca, modelo, tipo_carroceria (SUV|Sedán|Hatchback|Pick Up|Van), precio_minimo, precio_maximo, año_minimo, año_maximo, ubicacion (Monterrey|Guadalupe|Saltillo|Reynosa), transmision (Automática|Manual|CVT)
   Respuesta: {resultados: [...vehículos...], total: N}

2. obtener_vehiculo
   Obtiene detalles completos de un vehículo por ID.
   Argumentos: id (number)
   Respuesta: {id, marca, modelo, version, año, precio, kilometraje, transmision, motor, combustible, color_exterior, color_interior, ubicacion, garantia, caracteristicas: [...], estado}

3. comparar_vehiculos
   Compara 2 o más vehículos lado a lado.
   Argumentos: vehiculo_ids (array de numbers)
   Respuesta: {comparacion: [{id, marca, modelo, version, año, precio, kilometraje, transmision, motor, potencia, equipamiento_destacado: [...], ubicacion}, ...]}

4. calcular_financiamiento
   Calcula plan de financiamiento.
   Argumentos: precio_vehiculo (number), enganche_porcentaje (number, min 20), plazo_meses (12|24|36|48|60), vehiculo_id (optional)
   Respuesta: {precio_vehiculo, enganche_porcentaje, enganche_monto, monto_a_financiar, plazo_meses, tasa_anual, pago_mensual, costo_total_credito, total_intereses}

5. solicitar_datos_contacto
   Registra datos del cliente para seguimiento.
   Argumentos: nombre, telefono (optional), email (optional), vehiculo_interes (optional), comentarios (optional)
   Respuesta: {status: "ok", mensaje, folio: "TREFA-2025-XXXX"}

6. enviar_cotizacion_email
   Envía cotización formal por correo.
   Argumentos: email_destino, nombre_cliente, vehiculo_id, enganche_porcentaje, plazo_meses
   Respuesta: {status: "enviado", mensaje, folio_cotizacion: "COT-2025-XXXX"}

7. buscar_alternativas
   Busca alternativas cuando el auto solicitado no está disponible.
   Argumentos: marca_original, modelo_original (optional), presupuesto (optional), tipo_uso (optional), carroceria (optional)
   Respuesta: {alternativas: [{id, marca, modelo, año, precio, kilometraje, transmision, ubicacion, razon}, ...]}

8. obtener_info_negocio
   Consulta información del negocio: financiamiento, garantias, devoluciones, documentos_requeridos, ubicaciones, horarios, intercambio, proceso_compra, servicios.
   Argumentos: tema (string)
   Respuesta: {tema, informacion: {...datos relevantes...}}

9. estadisticas_inventario
   Resumen general del inventario actual.
   Argumentos: (ninguno)
   Respuesta: {total_vehiculos, rango_precios: {minimo, maximo, promedio}, marcas_disponibles: [...], tipos_carroceria: {...}, sucursales: {...}}

10. obtener_faqs
    Obtiene preguntas frecuentes.
    Argumentos: (ninguno)
    Respuesta: {faqs: [{pregunta, respuesta}, ...]}

11. buscar_informacion
    Busca información específica en la base de conocimiento.
    Argumentos: pregunta (string), categoria (optional: garantias|financiamiento|general|postventa)
    Respuesta: {respuesta: "...texto informativo..."}

FORMATO DE TOOL CALL (dentro del content del assistant):
<tool_call>
{"name": "nombre_herramienta", "arguments": {"key": "value"}}
</tool_call>

FORMATO DE TOOL RESPONSE (message con role "tool"):
<tool_response>
{"name": "nombre_herramienta", "content": {...datos realistas...}}
</tool_response>

IMPORTANTE: Un assistant message puede contener SOLO un tool_call (sin texto antes ni después) O SOLO texto conversacional, NUNCA ambos mezclados.
Después de cada tool_call, el siguiente message DEBE ser role "tool" con la tool_response.
Después de cada tool response, el siguiente message DEBE ser role "assistant" con texto que interprete los resultados."""

# ═══════════════════════════════════════════════════════════════
# ESCENARIOS DE CONVERSACIÓN (50+ tipos)
# ═══════════════════════════════════════════════════════════════

SCENARIOS = [
    # ─── Búsqueda y descubrimiento ───
    "Saludo inicial + descubrimiento de necesidades. El cliente saluda sin saber qué quiere, Mariana explora.",
    "Búsqueda por marca específica (Toyota, Honda, Mazda, Nissan, Kia, Hyundai, VW, Chevrolet, Ford, SEAT).",
    "Búsqueda por modelo específico (Corolla, Civic, CX-5, Jetta, Sportage, Tucson, Sentra, etc).",
    "Búsqueda por tipo de carrocería (SUV, sedán, hatchback, pick up, van).",
    "Búsqueda por presupuesto + tipo de uso (familiar, trabajo, primer auto, Uber/DiDi).",
    "Exploración general — cliente dice 'quiero ver qué tienen', Mariana usa estadisticas_inventario.",
    "Búsqueda por ubicación — cliente pregunta qué hay en sucursal específica.",
    "Búsqueda por año y kilometraje — cliente quiere algo reciente con poco uso.",
    "Búsqueda de auto económico (menos de 250K) — opciones más accesibles.",
    "Búsqueda de auto premium (más de 500K) — opciones de lujo.",

    # ─── Comparación y decisión ───
    "Comparación de 2 vehículos — cliente ya tiene opciones en mente.",
    "Cliente indeciso entre SUV y sedán — Mariana ayuda a decidir según su estilo de vida.",
    "Cliente indeciso entre 3+ opciones — Mariana guía con preguntas sobre prioridades.",
    "Cliente que cambia de opinión — empieza buscando una marca y termina interesado en otra.",
    "Comparación detallada con specs técnicas — cliente que sabe de autos.",

    # ─── Financiamiento ───
    "Consulta de financiamiento general — cómo funciona, requisitos, plazos.",
    "Cálculo de financiamiento con cifras específicas — enganche, plazo, mensualidad.",
    "Comparación de 2 escenarios de financiamiento (diferente enganche o plazo).",
    "Cliente con mal historial crediticio — Mariana explica opciones multibancos.",
    "Cliente pregunta por MSI (no disponible) — Mariana redirige a financiamiento.",
    "Cliente quiere pagar de contado — proceso simplificado.",
    "Cliente pregunta por documentos de financiamiento.",
    "Cliente independiente/freelance — documentos alternativos para crédito.",

    # ─── Objeciones y negociación ───
    "Objeción 'está muy caro' — Mariana explica el valor incluido (garantía, revisión).",
    "'Lo vi más barato en otro lado' — Mariana compara diferenciadores sin hablar mal.",
    "'Necesito pensarlo' — Mariana respeta pero ofrece seguimiento y datos de contacto.",
    "'¿Me hacen descuento?' — Mariana ofrece flexibilidad en financiamiento.",
    "Cliente escéptico de autos seminuevos — Mariana explica inspección 150 puntos y garantía.",
    "Cliente compara con comprar nuevo — Mariana muestra valor de seminuevo TREFA.",

    # ─── Información del negocio ───
    "Garantía y devoluciones — qué cubre, proceso de reclamación.",
    "Horarios y ubicaciones de sucursales.",
    "Proceso de compra paso a paso.",
    "Programa de intercambio (auto a cuenta) — requisitos y proceso.",
    "Servicios de mantenimiento postventa.",
    "FAQs generales — múltiples preguntas en una conversación.",
    "Envío foráneo — cliente de otra ciudad quiere comprar.",

    # ─── Flujos completos de venta ───
    "Flujo completo: saludo → búsqueda → detalles → financiamiento → datos de contacto.",
    "Flujo completo: comparación → decisión → cotización por email → cita.",
    "Flujo completo: exploración → objeción de precio → alternativa → cierre.",
    "Flujo completo: búsqueda → auto no disponible → alternativas → interés → contacto.",
    "Flujo completo premium: búsqueda → comparación → financiamiento → email → cita.",

    # ─── Situaciones especiales ───
    "Primer auto — cliente joven sin experiencia, Mariana orienta desde cero.",
    "Auto para negocio — pick up o van para trabajo, necesidades comerciales.",
    "Auto para Uber/DiDi — rendimiento, confort, precio accesible.",
    "Pareja decidiendo juntos — dos personas con opiniones diferentes.",
    "Cliente que regresa (segunda compra) — ya conoce TREFA.",
    "Cliente foráneo — vive en otra ciudad, logística de entrega.",
    "Cliente pregunta por seguro (fuera de alcance) — Mariana escala a especialista.",
    "Cliente pregunta tema legal (fuera de alcance) — Mariana reconoce y refiere.",
    "Cliente con prisa — quiere información rápida y concreta.",
    "Cliente que pregunta por un auto ya vendido — Mariana ofrece alternativas similares.",

    # ─── WhatsApp-específicos ───
    "Cliente envía mensaje corto/informal ('ke onda tienen carros?') — Mariana adapta tono.",
    "Cliente envía audio/foto (simular con texto) — Mariana responde apropiadamente.",
    "Cliente nocturno — saludo fuera de horario, Mariana atiende igual.",
    "Conversación que retoma de días anteriores — seguimiento.",
    "Cliente que pide 'mándame fotos' — Mariana describe y ofrece link/visita.",
    "Cliente que solo quiere precio sin platicar — Mariana da info rápida pero engancha.",
    "Múltiples preguntas en un solo mensaje — Mariana responde ordenadamente.",

    # ─── Emocionales/Situacionales ───
    "Cliente emocionado — acaba de recibir un aumento y quiere celebrar con auto nuevo.",
    "Cliente frustrado — mala experiencia en otra agencia, desconfiado.",
    "Cliente que busca regalo — auto para hijo/esposa/papá.",
    "Cliente jubilado — busca auto cómodo y seguro, sin prisas.",
    "Cliente que necesita auto urgente — su auto anterior se descompuso.",
]

# ═══════════════════════════════════════════════════════════════
# DATOS PARA VARIEDAD
# ═══════════════════════════════════════════════════════════════

NOMBRES_HOMBRE = [
    "Carlos", "Roberto", "Miguel", "Eduardo", "Tomás", "Raúl", "Alejandro",
    "Patricio", "Héctor", "Ricardo", "Luis", "Fernando", "Andrés", "Arturo",
    "Jorge", "Diego", "Sebastián", "Pablo", "Daniel", "Javier", "Óscar",
    "Manuel", "Francisco", "Adrián", "Emilio", "Gerardo", "Iván", "Marco",
    "Alfredo", "Sergio", "Enrique", "Alberto", "Rodrigo", "Armando", "Julio",
    "Ramón", "Hugo", "Víctor", "Ernesto", "Salvador", "Jesús", "Antonio",
    "Gabriel", "Guillermo", "Rafael", "Pedro", "Santiago", "Maximiliano",
    "Bruno", "Mateo", "Leonardo", "Nicolás", "Alan", "Kevin", "Brandon",
]

NOMBRES_MUJER = [
    "Daniela", "Fernanda", "Sofía", "Laura", "Valeria", "Mónica",
    "Gabriela", "Andrea", "Diana", "Margarita", "Ana", "Patricia",
    "María", "Carmen", "Lucía", "Isabella", "Regina", "Camila",
    "Ximena", "Renata", "Paulina", "Alejandra", "Natalia", "Karla",
    "Verónica", "Claudia", "Lorena", "Silvia", "Rosa", "Elena",
    "Beatriz", "Marisol", "Leticia", "Cecilia", "Pilar", "Irma",
    "Araceli", "Martha", "Adriana", "Julieta", "Teresa", "Catalina",
    "Victoria", "Samantha", "Brenda", "Jessica", "Dulce", "Ivonne",
]

MARCAS_MODELOS = {
    "Toyota": ["Corolla", "RAV4", "Camry", "Yaris", "Hilux", "C-HR", "Avanza"],
    "Honda": ["Civic", "CR-V", "HR-V", "City", "Accord", "BR-V", "Fit"],
    "Mazda": ["3 Sedán", "3 Hatchback", "CX-5", "CX-30", "CX-50", "6", "MX-5"],
    "Nissan": ["Sentra", "X-Trail", "Kicks", "Versa", "March", "Frontier", "Pathfinder"],
    "Kia": ["Sportage", "Forte", "Seltos", "Rio", "Sorento", "Carnival", "K5"],
    "Hyundai": ["Tucson", "Creta", "Accent", "Elantra", "Santa Fe", "Venue", "Palisade"],
    "Volkswagen": ["Jetta", "Tiguan", "Taos", "T-Cross", "Polo", "ID.4", "Virtus"],
    "Chevrolet": ["Tracker", "Equinox", "Onix", "Cavalier", "Traverse", "Blazer", "Aveo"],
    "Ford": ["Escape", "Bronco Sport", "Maverick", "Ranger", "Explorer", "Territory"],
    "SEAT": ["Ibiza", "Arona", "Ateca", "León", "Tarraco"],
}

CARROCERIAS = ["SUV", "Sedán", "Hatchback", "Pick Up", "Van"]
UBICACIONES = ["Monterrey", "Guadalupe", "Saltillo", "Reynosa"]
COLORES = [
    "Blanco", "Negro", "Gris", "Plata", "Rojo", "Azul", "Blanco Puro",
    "Gris Oscuro", "Gris Platino", "Rojo Cristal", "Azul Obsidiana",
    "Blanco Platinado", "Negro Onyx", "Gris Machine", "Azul Aegean",
    "Naranja Bitono", "Verde Militar", "Beige Arena", "Café Bronce",
]
TRANSMISIONES = ["Automática", "Manual", "CVT"]

SALUDOS_INFORMALES = [
    "hola buenas tardes", "ke onda", "hola buenas", "buenas tardes",
    "oigan, tienen carros?", "hola q tal", "buenas noches", "hola buen día",
    "hey buenas", "hola hola", "q onda buenas tardes", "buenas!!",
    "hola, me interesa un auto", "oye una pregunta", "hola disculpa",
    "buenas, busco un carro", "hola soy nuevo por aquí", "me pueden ayudar?",
    "ando buscando carro", "buenas tardes, vi su página",
    "hola, vi sus autos en facebook", "buenas, me recomendaron con ustedes",
    "hola, cuánto cuesta el más barato?", "tienen autos en buen precio?",
    "hola buenas, necesito un auto urgente", "qué ofertas tienen?",
]

# ═══════════════════════════════════════════════════════════════
# FUNCIONES AUXILIARES
# ═══════════════════════════════════════════════════════════════

def load_golden_examples() -> list[dict]:
    """Carga los 25 ejemplos golden del JSONL."""
    examples = []
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    print(f"Cargados {len(examples)} ejemplos golden de {GOLDEN_PATH}")
    return examples


def random_name() -> tuple[str, str]:
    """Retorna (nombre, género) aleatorio."""
    if random.random() < 0.5:
        return random.choice(NOMBRES_HOMBRE), "M"
    return random.choice(NOMBRES_MUJER), "F"


def random_vehicle() -> dict:
    """Genera datos de un vehículo aleatorio realista."""
    marca = random.choice(list(MARCAS_MODELOS.keys()))
    modelo = random.choice(MARCAS_MODELOS[marca])
    año = random.randint(2019, 2024)
    km = random.randint(8000, 90000)
    # Precio basado en tipo
    base_price = random.randint(180, 850) * 1000
    return {
        "marca": marca,
        "modelo": modelo,
        "año": año,
        "precio": base_price,
        "kilometraje": km,
        "ubicacion": random.choice(UBICACIONES),
        "color": random.choice(COLORES),
        "transmision": random.choice(TRANSMISIONES),
    }


def format_golden_example(conv: dict) -> str:
    """Formatea un ejemplo golden como texto legible para el prompt."""
    msgs = conv["messages"]
    lines = []
    for m in msgs:
        role = m["role"]
        content = m["content"]
        # Truncar system prompt
        if role == "system":
            lines.append(f'{{"role": "system", "content": "[SYSTEM PROMPT — ya definido arriba]"}}')
        else:
            # Escapar para JSON embebido en texto
            content_escaped = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            lines.append(f'{{"role": "{role}", "content": "{content_escaped}"}}')
    return '{"messages": [\n  ' + ',\n  '.join(lines) + '\n]}'


def build_prompt(
    batch_scenarios: list[str],
    golden_examples: list[dict],
    batch_num: int,
    total_generated: int,
) -> str:
    """Construye el prompt completo para Gemini."""
    # Seleccionar 3 golden examples diversos (corto, medio, largo)
    sorted_by_len = sorted(golden_examples, key=lambda x: len(x["messages"]))
    short = sorted_by_len[:8]
    medium = sorted_by_len[8:17]
    long_convs = sorted_by_len[17:]

    selected_golden = [
        random.choice(short),
        random.choice(medium),
        random.choice(long_convs) if long_convs else random.choice(medium),
    ]

    golden_text = ""
    for i, g in enumerate(selected_golden, 1):
        golden_text += f"\n--- Ejemplo {i} ({len(g['messages'])} mensajes) ---\n"
        golden_text += format_golden_example(g)
        golden_text += "\n"

    # Construir asignaciones de escenarios con detalles
    scenario_assignments = ""
    for i, scenario in enumerate(batch_scenarios, 1):
        name, gender = random_name()
        greeting = random.choice(SALUDOS_INFORMALES)
        vehicle = random_vehicle()
        budget_low = random.choice([150, 180, 200, 250, 280, 300, 350]) * 1000
        budget_high = budget_low + random.randint(50, 200) * 1000

        scenario_assignments += f"""
Conversación {i}:
- Escenario: {scenario}
- Cliente: {name} ({'hombre' if gender == 'M' else 'mujer'})
- Saludo inicial sugerido (puedes variar): "{greeting}"
- Presupuesto aproximado: ${budget_low:,} - ${budget_high:,} MXN
- Vehículo de referencia: {vehicle['marca']} {vehicle['modelo']} {vehicle['año']}
- Sucursal cercana: {vehicle['ubicacion']}
"""

    seed = random.randint(100000, 999999)

    prompt = f"""Eres un generador EXPERTO de datos de entrenamiento para fine-tuning de un modelo de lenguaje Qwen 3.
Tu tarea es generar conversaciones de chat de altísima calidad entre clientes de WhatsApp y "Mariana",
la asesora virtual de Autos TREFA (agencia de autos seminuevos en México).

Semilla de randomización: {seed} (usa esto para generar datos únicos).
Lote: #{batch_num} | Conversaciones generadas hasta ahora: {total_generated}

═══════════════════════════════════════
FORMATO DE DATOS (Qwen 3 tool calling)
═══════════════════════════════════════

Cada conversación es un objeto JSON con un campo "messages" que es un array de mensajes.
Cada mensaje tiene "role" (string) y "content" (string).

Roles válidos: "system", "user", "assistant", "tool"

El PRIMER mensaje de cada conversación DEBE ser:
{{"role": "system", "content": "{SYSTEM_PROMPT[:100]}... [SE INYECTA AUTOMÁTICAMENTE]"}}

Pon como content del system message EXACTAMENTE la cadena: "__SYSTEM_PROMPT__"
(El script lo reemplazará con el prompt completo)

{TOOLS_SPEC}

═══════════════════════════════════════
REGLAS NO NEGOCIABLES DE CALIDAD
═══════════════════════════════════════

1. MÍNIMO 6 mensajes por conversación (contando system). Ideal: 8-20 mensajes.
2. MÍNIMO 1 tool call por conversación. Ideal: 2-5 tool calls.
3. SIEMPRE empezar con system, luego user.
4. SIEMPRE terminar con assistant.
5. Después de cada tool_call del assistant, el siguiente DEBE ser un message role "tool".
6. Después de cada tool response, el siguiente DEBE ser un message role "assistant" que interprete los resultados.
7. NUNCA dos mensajes "user" consecutivos.
8. Los tool calls van SOLOS en un assistant message (sin texto adicional).
9. Los assistant messages de texto NUNCA contienen <tool_call> tags.
10. Mariana SIEMPRE cierra con una pregunta o propuesta de siguiente paso.
11. Mariana se presenta amablemente y pregunta el nombre del cliente.
12. Las respuestas de Mariana son OPTIMIZADAS PARA WHATSAPP: cortas (1-3 párrafos), directas, máx 1-2 emojis.
13. NUNCA inventar datos — los datos de vehículos vienen de las tool responses.
14. Los precios, años, kilometrajes deben ser REALISTAS para el mercado mexicano de autos seminuevos.
15. Español mexicano coloquial (tuteo, no usted). Los clientes hablan informal.
16. Las tool responses deben contener datos COMPLETOS y REALISTAS (no placeholders).
17. Cada conversación debe ser ÚNICA — diferentes nombres, autos, precios, escenarios.
18. Los IDs de vehículos deben ser números diferentes en cada conversación (rango 100-999).
19. Los folios deben ser únicos: TREFA-2025-XXXX o COT-2025-XXXX con números aleatorios.
20. Tasas de financiamiento: entre 12% y 18% anual. Enganche mínimo 20%.

═══════════════════════════════════════
REGLAS DE COMPORTAMIENTO DE MARIANA
═══════════════════════════════════════

- Si el cliente no tiene claro qué quiere → hacer preguntas de descubrimiento (tipo de uso, presupuesto, preferencias)
- Si el auto no está disponible → SIEMPRE usar buscar_alternativas y ofrecer opciones similares
- Si hay objeción de precio → explicar valor incluido (garantía, inspección, devolución) sin hablar mal de competencia
- Si el cliente dice "necesito pensarlo" → respetar, ofrecer datos de contacto sin presión
- Si algo es fuera de alcance (seguros, legal) → reconocerlo honestamente y ofrecer referir a especialista
- Si el cliente pregunta por MSI → decir que no, pero ofrecer financiamiento como alternativa
- Capturar datos progresivamente: nombre → interés → presupuesto → financiamiento/contado → sucursal → contacto
- Cuando haya interés claro → guiar a siguiente paso concreto (cita, documentos, prueba de manejo)
- NUNCA dejar al cliente "en el aire" — siempre proponer algo concreto
- Usar formato con emojis bullet points (🔹) para listar vehículos
- Usar formato con negrita (**texto**) para datos clave

═══════════════════════════════════════
EJEMPLOS DE REFERENCIA (CALIDAD GOLDEN)
═══════════════════════════════════════
{golden_text}

═══════════════════════════════════════
GENERA ESTAS {len(batch_scenarios)} CONVERSACIONES
═══════════════════════════════════════
{scenario_assignments}

═══════════════════════════════════════
INSTRUCCIONES DE SALIDA
═══════════════════════════════════════

Responde ÚNICAMENTE con un JSON array válido de {len(batch_scenarios)} conversaciones.
Cada conversación es un objeto con campo "messages".
NO incluyas texto fuera del JSON.
NO uses markdown code fences.
El content del system message debe ser "__SYSTEM_PROMPT__".

Ejemplo de estructura de salida:
[
  {{"messages": [{{"role": "system", "content": "__SYSTEM_PROMPT__"}}, {{"role": "user", "content": "hola buenas"}}, ...]}},
  {{"messages": [{{"role": "system", "content": "__SYSTEM_PROMPT__"}}, {{"role": "user", "content": "oigan tienen autos?"}}, ...]}}
]
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
                        temperature=1.0,
                        top_p=0.95,
                        top_k=40,
                        max_output_tokens=65536,
                        response_mime_type="application/json",
                    ),
                )
            else:
                model = genai.GenerativeModel(
                    model_name,
                    generation_config={
                        "temperature": 1.0,
                        "top_p": 0.95,
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

def safe_parse_json(text: str) -> list | None:
    """Parsea JSON robusto — maneja markdown fences, texto extra, etc."""
    if not text:
        return None

    # Limpiar markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    # Intento directo
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "messages" in data:
            return [data]
        return None
    except json.JSONDecodeError:
        pass

    # Buscar array JSON en el texto
    start = cleaned.find("[")
    if start != -1:
        # Encontrar el cierre balanceado
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "[":
                depth += 1
            elif cleaned[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(cleaned[start:i + 1])
                        if isinstance(data, list):
                            return data
                    except json.JSONDecodeError:
                        break
                    break

    # Intentar reparar JSON truncado
    try:
        # Cerrar brackets/braces faltantes
        repair = cleaned
        open_brackets = repair.count("[") - repair.count("]")
        open_braces = repair.count("{") - repair.count("}")
        if open_braces > 0:
            repair += '"' + "}" * open_braces
        if open_brackets > 0:
            repair += "]" * open_brackets
        data = json.loads(repair)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    return None


VALID_TOOLS = {
    "buscar_vehiculos", "obtener_vehiculo", "comparar_vehiculos",
    "calcular_financiamiento", "solicitar_datos_contacto",
    "enviar_cotizacion_email", "buscar_alternativas",
    "obtener_info_negocio", "estadisticas_inventario",
    "obtener_faqs", "buscar_informacion",
}


def validate_conversation(conv: dict, idx: int) -> list[str]:
    """Valida una conversación y retorna lista de errores (vacía = OK)."""
    errors = []

    if not isinstance(conv, dict) or "messages" not in conv:
        return [f"Conv {idx}: No tiene campo 'messages'"]

    msgs = conv["messages"]

    if not isinstance(msgs, list) or len(msgs) < MIN_MESSAGES:
        errors.append(f"Conv {idx}: Solo {len(msgs) if isinstance(msgs, list) else 0} mensajes (mín {MIN_MESSAGES})")
        if not isinstance(msgs, list):
            return errors

    # Verificar que todos los mensajes tienen role y content
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            errors.append(f"Conv {idx}, msg {i}: No es un dict")
            continue
        if "role" not in m or "content" not in m:
            errors.append(f"Conv {idx}, msg {i}: Falta role o content")

    if errors:
        return errors

    # Primer mensaje debe ser system
    if msgs[0]["role"] != "system":
        errors.append(f"Conv {idx}: No empieza con system")

    # Último mensaje debe ser assistant
    if msgs[-1]["role"] != "assistant":
        errors.append(f"Conv {idx}: No termina con assistant")

    # Verificar secuencia de roles
    has_tool_call = False
    for i in range(1, len(msgs)):
        curr_role = msgs[i]["role"]
        prev_role = msgs[i - 1]["role"]
        curr_content = msgs[i].get("content", "")
        prev_content = msgs[i - 1].get("content", "")

        # No dos users consecutivos
        if prev_role == "user" and curr_role == "user":
            errors.append(f"Conv {idx}, msg {i}: Dos users consecutivos")

        # Después de tool response → assistant
        if prev_role == "tool" and curr_role != "assistant":
            errors.append(f"Conv {idx}, msg {i}: Después de tool, esperaba assistant, got {curr_role}")

        # tool_call en assistant → siguiente debe ser tool
        if prev_role == "assistant" and "<tool_call>" in prev_content:
            has_tool_call = True
            if curr_role != "tool":
                errors.append(f"Conv {idx}, msg {i}: Después de tool_call, esperaba tool, got {curr_role}")

        # Verificar tool_call format
        if curr_role == "assistant" and "<tool_call>" in curr_content:
            has_tool_call = True
            if "</tool_call>" not in curr_content:
                errors.append(f"Conv {idx}, msg {i}: tool_call sin cierre")
            else:
                # Verificar que el tool name es válido
                try:
                    tc_start = curr_content.index("<tool_call>") + len("<tool_call>")
                    tc_end = curr_content.index("</tool_call>")
                    tc_json = json.loads(curr_content[tc_start:tc_end].strip())
                    if tc_json.get("name") not in VALID_TOOLS:
                        errors.append(f"Conv {idx}, msg {i}: Herramienta inválida: {tc_json.get('name')}")
                except (json.JSONDecodeError, ValueError):
                    errors.append(f"Conv {idx}, msg {i}: JSON inválido en tool_call")

        # Verificar tool_response format
        if curr_role == "tool":
            if "<tool_response>" not in curr_content or "</tool_response>" not in curr_content:
                errors.append(f"Conv {idx}, msg {i}: tool_response mal formateada")

    if not has_tool_call:
        errors.append(f"Conv {idx}: Sin tool calls")

    return errors


def inject_system_prompt(conv: dict) -> dict:
    """Reemplaza el system prompt placeholder con el canónico."""
    msgs = conv["messages"]
    if msgs and msgs[0]["role"] == "system":
        msgs[0]["content"] = SYSTEM_PROMPT
    else:
        msgs.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    return conv


def hash_conversation(conv: dict) -> str:
    """Hash para deduplicación basado en mensajes de usuario."""
    user_msgs = [m["content"] for m in conv.get("messages", []) if m.get("role") == "user"]
    text = "|".join(user_msgs)
    return hashlib.md5(text.encode()).hexdigest()


def save_jsonl(conversations: list[dict], path: Path):
    """Guarda conversaciones en formato JSONL."""
    with open(path, "w", encoding="utf-8") as f:
        for conv in conversations:
            f.write(json.dumps(conv, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════
# LOOP PRINCIPAL DE GENERACIÓN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Genera conversaciones de entrenamiento para Qwen 3 usando Gemini"
    )
    parser.add_argument("--api-key", type=str, default=None,
                        help="Google API key (o usa GOOGLE_API_KEY del env)")
    parser.add_argument("--total", type=int, default=DEFAULT_TOTAL,
                        help=f"Total de conversaciones a generar (default: {DEFAULT_TOTAL})")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                        help=f"Conversaciones por lote (default: {DEFAULT_BATCH})")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help=f"Modelo Gemini (default: {DEFAULT_MODEL})")
    parser.add_argument("--output", type=str, default=None,
                        help="Ruta de archivo de salida (default: auto-generada)")
    parser.add_argument("--append", type=str, default=None,
                        help="Append a un archivo JSONL existente")
    args = parser.parse_args()

    # API key
    api_key = args.api_key or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Error: Proporciona --api-key o define GOOGLE_API_KEY en el entorno")
        sys.exit(1)

    if USE_NEW_SDK:
        client = genai.Client(api_key=api_key)
    else:
        genai.configure(api_key=api_key)
        client = None

    # Verificar golden file
    if not GOLDEN_PATH.exists():
        print(f"Error: No se encuentra {GOLDEN_PATH}")
        sys.exit(1)

    # Cargar golden examples
    golden_examples = load_golden_examples()

    # Preparar directorio de salida
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Archivo de salida
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = OUTPUT_DIR / f"generated_{args.total}_{timestamp}.jsonl"

    # Si append, cargar existentes
    existing = []
    existing_hashes = set()
    if args.append and Path(args.append).exists():
        with open(args.append, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    conv = json.loads(line)
                    existing.append(conv)
                    existing_hashes.add(hash_conversation(conv))
        print(f"Cargadas {len(existing)} conversaciones existentes de {args.append}")
        output_path = Path(args.append)

    # Estado
    all_conversations = list(existing)
    seen_hashes = set(existing_hashes)
    valid_count = len(existing)
    invalid_count = 0
    duplicate_count = 0
    api_calls = 0
    total_needed = args.total
    batch_size = args.batch

    # Crear pool de escenarios — cada escenario se usa ~uniformemente
    scenario_pool = []
    while len(scenario_pool) < total_needed + 50:
        shuffled = list(SCENARIOS)
        random.shuffle(shuffled)
        scenario_pool.extend(shuffled)

    scenario_idx = 0

    print(f"\n{'═' * 60}")
    print(f"GENERACIÓN DE CONVERSACIONES DE ENTRENAMIENTO")
    print(f"{'═' * 60}")
    print(f"Modelo: {args.model}")
    print(f"Total a generar: {total_needed}")
    print(f"Tamaño de lote: {batch_size}")
    print(f"Lotes estimados: {(total_needed + batch_size - 1) // batch_size}")
    print(f"Salida: {output_path}")
    print(f"{'═' * 60}\n")

    start_time = time.time()

    try:
        while valid_count < total_needed + len(existing):
            remaining = total_needed + len(existing) - valid_count
            current_batch = min(batch_size, remaining)

            # Seleccionar escenarios para este lote
            batch_scenarios = scenario_pool[scenario_idx:scenario_idx + current_batch]
            scenario_idx += current_batch

            batch_num = api_calls + 1
            print(f"[Lote {batch_num}] Generando {current_batch} conversaciones... ", end="", flush=True)

            # Construir prompt
            prompt = build_prompt(
                batch_scenarios=batch_scenarios,
                golden_examples=golden_examples,
                batch_num=batch_num,
                total_generated=valid_count,
            )

            # Llamar a Gemini
            response_text = call_gemini(prompt, args.model, client)
            api_calls += 1

            if not response_text:
                print("FALLO (sin respuesta)")
                continue

            # Parsear respuesta
            parsed = safe_parse_json(response_text)

            if not parsed:
                print("FALLO (JSON inválido)")
                # Guardar respuesta raw para debug
                raw_path = OUTPUT_DIR / f"raw_batch_{batch_num}_{timestamp}.txt"
                with open(raw_path, "w", encoding="utf-8") as f:
                    f.write(response_text)
                print(f"  Respuesta raw guardada en: {raw_path}")
                continue

            # Validar y procesar cada conversación
            batch_valid = 0
            batch_invalid = 0
            batch_dup = 0

            for i, conv in enumerate(parsed):
                if not isinstance(conv, dict):
                    batch_invalid += 1
                    continue

                # Inyectar system prompt canónico
                conv = inject_system_prompt(conv)

                # Validar
                errors = validate_conversation(conv, i + 1)

                if errors:
                    batch_invalid += 1
                    invalid_count += 1
                    if batch_invalid <= 2:  # Solo mostrar primeros errores
                        for e in errors[:2]:
                            print(f"\n    ⚠ {e}", end="")
                    continue

                # Dedup
                h = hash_conversation(conv)
                if h in seen_hashes:
                    batch_dup += 1
                    duplicate_count += 1
                    continue

                # Válida y única
                seen_hashes.add(h)
                all_conversations.append(conv)
                valid_count += 1
                batch_valid += 1

            print(f"✓ {batch_valid} válidas, {batch_invalid} inválidas, {batch_dup} duplicadas | Total: {valid_count}/{total_needed + len(existing)}")

            # Guardar progreso incremental cada 5 lotes
            if batch_num % 5 == 0:
                save_jsonl(all_conversations, output_path)
                print(f"  💾 Progreso guardado ({valid_count} conversaciones)")

            # Rate limiting suave
            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n\nInterrumpido por usuario. Guardando {valid_count} conversaciones...")

    # Guardar resultado final
    save_jsonl(all_conversations, output_path)

    elapsed = time.time() - start_time
    new_count = valid_count - len(existing)

    # Estadísticas
    total_msgs = sum(len(c["messages"]) for c in all_conversations[len(existing):])
    total_tool_calls = sum(
        1 for c in all_conversations[len(existing):]
        for m in c["messages"]
        if m.get("role") == "assistant" and "<tool_call>" in m.get("content", "")
    )

    print(f"\n{'═' * 60}")
    print(f"RESUMEN DE GENERACIÓN")
    print(f"{'═' * 60}")
    print(f"Nuevas conversaciones generadas: {new_count}")
    print(f"Total en archivo:                {len(all_conversations)}")
    print(f"Conversaciones inválidas:        {invalid_count}")
    print(f"Duplicadas descartadas:          {duplicate_count}")
    print(f"Llamadas API:                    {api_calls}")
    print(f"Tiempo total:                    {elapsed:.1f}s ({elapsed / 60:.1f} min)")
    if new_count > 0:
        print(f"Total mensajes nuevos:           {total_msgs}")
        print(f"Tool calls nuevos:               {total_tool_calls}")
        print(f"Promedio msgs/conversación:      {total_msgs / new_count:.1f}")
        print(f"Promedio tool_calls/conv:         {total_tool_calls / new_count:.1f}")
    print(f"Archivo de salida:               {output_path}")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
