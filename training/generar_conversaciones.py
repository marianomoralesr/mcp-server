#!/usr/bin/env python3
"""
Generador de conversaciones de entrenamiento para Mariana (Autos TREFA).

Produce un archivo JSONL con conversaciones sinteticas que cubren
los 15+ escenarios del chatbot con tool calling en formato Qwen/ChatML.

Uso:
  python3 generar_conversaciones.py                       # genera todo
  python3 generar_conversaciones.py --output mi_dataset.jsonl
  python3 generar_conversaciones.py --count 800

Salida: dataset_mariana_training.jsonl
"""

import argparse
import json
import random
import sys
from pathlib import Path

# ─── System prompt ──────────────────────────────────────────────

SYSTEM_PROMPT_FILE = Path(__file__).parent / "system_prompt_finetuning.txt"

def load_system_prompt() -> str:
    if SYSTEM_PROMPT_FILE.exists():
        return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    print(f"ADVERTENCIA: {SYSTEM_PROMPT_FILE} no encontrado, usando prompt embebido")
    return "Eres Mariana, asesora virtual de Autos TREFA."


SYSTEM_PROMPT = load_system_prompt()


# ─── Helpers ────────────────────────────────────────────────────

def tc(name: str, args: dict) -> str:
    return f'<tool_call>\n{json.dumps({"name": name, "arguments": args}, ensure_ascii=False)}\n</tool_call>'

def tr(content: dict) -> str:
    return f'<tool_response>\n{json.dumps(content, ensure_ascii=False)}\n</tool_response>'

def fmt(precio: int) -> str:
    return f"${precio:,} MXN"

def fmt_raw(precio: int) -> str:
    return f"${precio:,}"

def mensualidad(precio: int, enganche_pct: int = 20, plazo: int = 48, tasa: float = 15.0) -> int:
    enganche = int(precio * enganche_pct / 100)
    monto = precio - enganche
    r = tasa / 100 / 12
    return int(monto * (r * (1 + r)**plazo) / ((1 + r)**plazo - 1))


# ─── Catalogo de vehiculos ─────────────────────────────────────

VEHICULOS = [
    {"id": 101, "titulo": "Toyota Corolla 2022", "marca": "Toyota", "modelo": "Corolla", "ano": 2022, "precio": 359900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "Sedan", "ubicacion": "Monterrey", "km": 28000, "slug": "toyota-corolla-2022-101"},
    {"id": 102, "titulo": "Honda CR-V 2021", "marca": "Honda", "modelo": "CR-V", "ano": 2021, "precio": 449900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Guadalupe", "km": 35000, "slug": "honda-cr-v-2021-102"},
    {"id": 103, "titulo": "Mazda CX-5 2023", "marca": "Mazda", "modelo": "CX-5", "ano": 2023, "precio": 489900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Monterrey", "km": 15000, "slug": "mazda-cx-5-2023-103"},
    {"id": 104, "titulo": "Nissan Sentra 2022", "marca": "Nissan", "modelo": "Sentra", "ano": 2022, "precio": 319900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "Sedan", "ubicacion": "Saltillo", "km": 32000, "slug": "nissan-sentra-2022-104"},
    {"id": 105, "titulo": "Volkswagen Jetta 2021", "marca": "Volkswagen", "modelo": "Jetta", "ano": 2021, "precio": 329900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "Sedan", "ubicacion": "Reynosa", "km": 40000, "slug": "volkswagen-jetta-2021-105"},
    {"id": 106, "titulo": "Toyota RAV4 2022", "marca": "Toyota", "modelo": "RAV4", "ano": 2022, "precio": 519900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Monterrey", "km": 22000, "slug": "toyota-rav4-2022-106"},
    {"id": 107, "titulo": "Chevrolet Aveo 2023", "marca": "Chevrolet", "modelo": "Aveo", "ano": 2023, "precio": 249900, "transmision": "Manual", "combustible": "Gasolina", "carroceria": "Sedan", "ubicacion": "Guadalupe", "km": 12000, "slug": "chevrolet-aveo-2023-107"},
    {"id": 108, "titulo": "Kia Seltos 2022", "marca": "Kia", "modelo": "Seltos", "ano": 2022, "precio": 389900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Saltillo", "km": 25000, "slug": "kia-seltos-2022-108"},
    {"id": 109, "titulo": "Hyundai Tucson 2021", "marca": "Hyundai", "modelo": "Tucson", "ano": 2021, "precio": 419900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Monterrey", "km": 38000, "slug": "hyundai-tucson-2021-109"},
    {"id": 110, "titulo": "Ford Escape 2022", "marca": "Ford", "modelo": "Escape", "ano": 2022, "precio": 459900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Guadalupe", "km": 30000, "slug": "ford-escape-2022-110"},
    {"id": 111, "titulo": "Mazda 3 Sedan 2022", "marca": "Mazda", "modelo": "3", "ano": 2022, "precio": 369900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "Sedan", "ubicacion": "Monterrey", "km": 20000, "slug": "mazda-3-sedan-2022-111"},
    {"id": 112, "titulo": "Toyota Hilux 2021", "marca": "Toyota", "modelo": "Hilux", "ano": 2021, "precio": 529900, "transmision": "Automatica", "combustible": "Diesel", "carroceria": "Pickup", "ubicacion": "Saltillo", "km": 45000, "slug": "toyota-hilux-2021-112"},
    {"id": 113, "titulo": "Nissan Kicks 2022", "marca": "Nissan", "modelo": "Kicks", "ano": 2022, "precio": 329900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Reynosa", "km": 27000, "slug": "nissan-kicks-2022-113"},
    {"id": 114, "titulo": "Suzuki Swift 2023", "marca": "Suzuki", "modelo": "Swift", "ano": 2023, "precio": 269900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "Hatchback", "ubicacion": "Guadalupe", "km": 18000, "slug": "suzuki-swift-2023-114"},
    {"id": 115, "titulo": "Hyundai Accent 2022", "marca": "Hyundai", "modelo": "Accent", "ano": 2022, "precio": 279900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "Sedan", "ubicacion": "Monterrey", "km": 33000, "slug": "hyundai-accent-2022-115"},
    {"id": 116, "titulo": "Nissan Frontier 2022", "marca": "Nissan", "modelo": "Frontier", "ano": 2022, "precio": 489900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "Pickup", "ubicacion": "Monterrey", "km": 35000, "slug": "nissan-frontier-2022-116"},
    {"id": 117, "titulo": "Mazda CX-30 2022", "marca": "Mazda", "modelo": "CX-30", "ano": 2022, "precio": 399900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Guadalupe", "km": 21000, "slug": "mazda-cx-30-2022-117"},
    {"id": 118, "titulo": "Honda HR-V 2021", "marca": "Honda", "modelo": "HR-V", "ano": 2021, "precio": 359900, "transmision": "Automatica", "combustible": "Gasolina", "carroceria": "SUV", "ubicacion": "Saltillo", "km": 41000, "slug": "honda-hr-v-2021-118"},
]

def buscar(marca=None, carroceria=None, precio_max=None, modelo=None):
    results = VEHICULOS
    if marca:
        results = [v for v in results if v["marca"].lower() == marca.lower()]
    if modelo:
        results = [v for v in results if modelo.lower() in v["modelo"].lower()]
    if carroceria:
        results = [v for v in results if v["carroceria"].lower() == carroceria.lower()]
    if precio_max:
        results = [v for v in results if v["precio"] <= precio_max]
    return results

def veh_to_search_result(v):
    return {
        "id": v["id"], "titulo": v["titulo"], "marca": v["marca"], "modelo": v["modelo"],
        "ano": v["ano"], "precio": fmt_raw(v["precio"]), "precio_numerico": v["precio"],
        "transmision": v["transmision"], "combustible": v["combustible"],
        "carroceria": v["carroceria"], "ubicacion": v["ubicacion"],
        "kilometraje": f"{v['km']:,} km", "garantia": "12 meses motor y transmision",
        "enganche_minimo": fmt_raw(int(v["precio"]*0.2)),
        "mensualidad_desde": fmt_raw(mensualidad(v["precio"])),
        "url": f"https://autostrefa.mx/inventario/{v['slug']}",
    }

def veh_to_detail(v):
    d = veh_to_search_result(v)
    d.update({
        "descripcion": f"{v['titulo']} en excelente estado, revision mecanica de 150 puntos aprobada.",
        "enganche_recomendado": fmt_raw(int(v["precio"]*0.25)),
        "mensualidad_recomendada": fmt_raw(mensualidad(v["precio"], 25)),
        "plazo_maximo": 60,
        "imagen_principal": f"https://autostrefa.mx/images/{v['slug']}.jpg",
    })
    return d

def calc_financiamiento(precio, enganche_pct=20, plazo=48, tasa=15.0):
    enganche = int(precio * enganche_pct / 100)
    monto = precio - enganche
    mens = mensualidad(precio, enganche_pct, plazo, tasa)
    total = mens * plazo + enganche
    return {
        "precio_vehiculo": fmt_raw(precio), "enganche_porcentaje": enganche_pct,
        "enganche": fmt_raw(enganche), "monto_a_financiar": fmt_raw(monto),
        "tasa_anual": f"{tasa:.0f}%", "plazo_meses": plazo,
        "mensualidad_estimada": fmt_raw(mens), "total_a_pagar": fmt_raw(total),
        "costo_financiamiento": fmt_raw(total - precio),
        "nota": "Calculo estimado. La tasa final depende del perfil crediticio y la institucion financiera.",
    }


# ─── Nombres y variaciones ─────────────────────────────────────

NOMBRES_M = ["Sofia", "Ana", "Laura", "Daniela", "Carolina", "Fernanda", "Valentina", "Gabriela", "Patricia", "Monica", "Adriana", "Claudia", "Karla", "Alejandra", "Diana", "Maria", "Lucia", "Paola", "Elena", "Regina"]
NOMBRES_H = ["Carlos", "Miguel", "Roberto", "Juan", "Pedro", "Luis", "Fernando", "Ricardo", "Eduardo", "Andres", "Jorge", "Diego", "Marco", "Hector", "Omar", "Sergio", "Pablo", "Daniel", "Arturo", "Enrique"]
TELEFONOS = [f"81-{random.randint(1000,9999)}-{random.randint(1000,9999)}" for _ in range(40)]

def nombre():
    return random.choice(NOMBRES_M + NOMBRES_H)

def telefono():
    return random.choice(TELEFONOS)

SALUDOS_USER = [
    "Hola", "Hola buenas tardes", "Buenos dias", "Hola que tal",
    "Buenas", "Hola, me pueden ayudar?", "Hola buenas noches",
    "Hola! estoy buscando un auto", "Que tal, buenas tardes",
    "Hola, quisiera informacion", "Hey que onda",
]

SALUDOS_BOT = [
    "Hola! Bienvenido a Autos TREFA, soy Mariana, tu asesora virtual. Con quien tengo el gusto?",
    "Hola, que gusto saludarte! Soy Mariana de Autos TREFA. Con quien tengo el gusto? 😊",
    "Muy buen dia! Soy Mariana, tu asesora en Autos TREFA. Con quien tengo el gusto?",
    "Hola! Bienvenida a Autos TREFA. Soy Mariana, me da gusto atenderte. Me compartes tu nombre?",
    "Hola! 😊 Que gusto que nos contactes. Soy Mariana de Autos TREFA. Con quien tengo el gusto?",
]


# ─── Generadores de conversaciones ──────────────────────────────

def conv(messages, metadata):
    return {"messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages, "metadata": metadata}


# ── ESC-12: Saludo simple ──

def gen_saludo_simple():
    convos = []
    for _ in range(15):
        n = nombre()
        convos.append(conv([
            {"role": "user", "content": random.choice(SALUDOS_USER)},
            {"role": "assistant", "content": random.choice(SALUDOS_BOT)},
            {"role": "user", "content": f"Soy {n}, busco un auto"},
            {"role": "assistant", "content": f"Mucho gusto, {n}! Para encontrarte la mejor opcion, me platicas que tipo de auto buscas? Puede ser marca, modelo, o si tienes un presupuesto en mente 😊"},
        ], {"escenario": "saludo_simple", "tools": []}))
    return convos


# ── ESC-1/busqueda_exitosa: Busqueda con resultados ──

def gen_busqueda_exitosa():
    convos = []
    busquedas = [
        ("SUV", None, 500000, "Busco una SUV para mi familia, presupuesto de 500 mil"),
        ("Sedan", None, 400000, "Quiero un sedan automatico, hasta 400 mil"),
        (None, "Toyota", 600000, "Tienen Toyota? Mi presupuesto es de 600 mil"),
        (None, "Mazda", 500000, "Me interesan los Mazda, tengo hasta 500 mil"),
        ("SUV", None, 450000, "Busco camioneta SUV por debajo de 450 mil"),
        ("Sedan", None, 350000, "Necesito un carro economico, maximo 350 mil"),
        (None, "Nissan", 400000, "Que Nissan tienen disponibles? Hasta 400 mil"),
        (None, "Hyundai", 450000, "Me gustan los Hyundai, presupuesto 450 mil"),
        ("Pickup", None, 600000, "Busco una pickup para trabajo, hasta 600 mil"),
        ("Hatchback", None, 300000, "Quiero un hatchback compacto, maximo 300 mil"),
        ("SUV", None, 400000, "Ando buscando una SUV economica, tengo 400 mil"),
        (None, "Honda", 500000, "Que Honda tienen? Mi presupuesto anda en 500"),
        ("Sedan", None, 300000, "Busco mi primer auto, sedan, hasta 300 mil"),
        (None, "Kia", 400000, "Muestrame los Kia que tengan por favor"),
    ]
    for carroceria, marca, precio_max, pregunta in busquedas:
        n = nombre()
        results = buscar(marca=marca, carroceria=carroceria, precio_max=precio_max)[:3]
        if not results:
            results = random.sample(VEHICULOS, 2)

        search_args = {}
        if marca: search_args["marca"] = marca
        if carroceria: search_args["tipo_carroceria"] = carroceria
        if precio_max: search_args["precio_maximo"] = precio_max

        search_response = {"vehiculos": [veh_to_search_result(v) for v in results], "total": len(results)}

        # Presentacion natural de resultados
        opciones = []
        for i, v in enumerate(results):
            opciones.append(f"**{v['titulo']}** - {fmt(v['precio'])} | {v['km']:,} km | {v['transmision']} | {v['ubicacion']}")
        opciones_txt = "\n".join(opciones)
        v0 = results[0]

        msgs = [
            {"role": "user", "content": random.choice(SALUDOS_USER)},
            {"role": "assistant", "content": random.choice(SALUDOS_BOT)},
            {"role": "user", "content": f"{n}. {pregunta}"},
            {"role": "assistant", "content": tc("buscar_vehiculos", search_args)},
            {"role": "tool", "content": tr(search_response)},
            {"role": "assistant", "content": f"Mucho gusto, {n}! Encontre {len(results)} opciones que se ajustan a lo que buscas:\n\n{opciones_txt}\n\nTodos incluyen nuestra garantia de 12 meses en motor y transmision, y pasaron revision mecanica de 150 puntos. Alguno te llama la atencion? 😊"},
        ]

        convos.append(conv(msgs, {"escenario": "busqueda_exitosa", "tools": ["buscar_vehiculos"]}))
    return convos


# ── ESC-1: Sin inventario + alternativas ──

def gen_sin_inventario():
    convos = []
    busquedas_vacias = [
        ("BMW", "X3", 550000, "SUV"),
        ("Audi", "Q5", 600000, "SUV"),
        ("Mercedes", "GLC", 700000, "SUV"),
        ("Honda", "Civic", 400000, "Sedan"),
        ("Tesla", "Model 3", 500000, "Sedan"),
        ("Volkswagen", "Tiguan", 500000, "SUV"),
        ("Toyota", "Supra", 600000, "Sedan"),
        ("Subaru", "Forester", 450000, "SUV"),
        ("Jeep", "Wrangler", 600000, "SUV"),
        ("Mitsubishi", "Outlander", 400000, "SUV"),
    ]
    for marca, modelo, presupuesto, carroceria in busquedas_vacias:
        n = nombre()
        alts = [v for v in VEHICULOS if v["carroceria"] == carroceria and v["precio"] <= presupuesto * 1.1]
        alts = random.sample(alts, min(2, len(alts))) if alts else random.sample(VEHICULOS, 2)

        alt_response = {"alternativas": [veh_to_search_result(v) for v in alts], "total": len(alts),
                        "nota": f"No hay {marca} {modelo} disponible. Alternativas en rango similar."}

        opciones = []
        for v in alts:
            opciones.append(f"**{v['titulo']}** - {fmt(v['precio'])} | {v['km']:,} km | {v['ubicacion']}")
        opciones_txt = "\n".join(opciones)

        msgs = [
            {"role": "user", "content": f"Hola, tienen {marca} {modelo}?"},
            {"role": "assistant", "content": f"Hola! Soy Mariana de Autos TREFA. Con gusto te busco. Con quien tengo el gusto?"},
            {"role": "user", "content": f"Soy {n}"},
            {"role": "assistant", "content": tc("buscar_vehiculos", {"marca": marca, "modelo": modelo})},
            {"role": "tool", "content": tr({"vehiculos": [], "total": 0})},
            {"role": "assistant", "content": tc("buscar_alternativas", {"marca_original": marca, "modelo_original": modelo, "presupuesto": presupuesto, "carroceria": carroceria})},
            {"role": "tool", "content": tr(alt_response)},
            {"role": "assistant", "content": f"{n}, por el momento no tenemos {marca} {modelo} en inventario. Pero encontre opciones que te pueden interesar por caracteristicas similares:\n\n{opciones_txt}\n\nTodos con garantia de 12 meses y revision mecanica completa. Te gustaria conocer mas detalles de alguno? 😊"},
        ]

        convos.append(conv(msgs, {"escenario": "sin_inventario_alternativas", "tools": ["buscar_vehiculos", "buscar_alternativas"]}))
    return convos


# ── ESC-3: Financiamiento + calculo ──

def gen_financiamiento():
    convos = []
    for v in random.sample(VEHICULOS, 10):
        n = nombre()
        enganche_pct = random.choice([10, 15, 20, 25, 30])
        plazo = random.choice([24, 36, 48, 60])
        fin = calc_financiamiento(v["precio"], enganche_pct, plazo)

        msgs = [
            {"role": "user", "content": random.choice(SALUDOS_USER)},
            {"role": "assistant", "content": random.choice(SALUDOS_BOT)},
            {"role": "user", "content": f"Soy {n}. Me interesa el {v['titulo']}, cuanto quedaria de mensualidad con {enganche_pct}% de enganche a {plazo} meses?"},
            {"role": "assistant", "content": tc("calcular_financiamiento", {"precio_vehiculo": v["precio"], "enganche_porcentaje": enganche_pct, "plazo_meses": plazo})},
            {"role": "tool", "content": tr(fin)},
            {"role": "assistant", "content": f"{n}, aqui tienes el calculo para el {v['titulo']}:\n\n**Precio:** {fmt(v['precio'])}\n**Enganche ({enganche_pct}%):** {fin['enganche']}\n**Mensualidad:** {fin['mensualidad_estimada']} a {plazo} meses\n**Total a pagar:** {fin['total_a_pagar']}\n\nRecuerda que este es un estimado, la tasa final depende de tu perfil crediticio. Trabajamos con multiples bancos para encontrarte la mejor opcion. Te gustaria iniciar el tramite de pre-aprobacion? 😊"},
        ]

        convos.append(conv(msgs, {"escenario": "financiamiento_calculo", "tools": ["calcular_financiamiento"]}))
    return convos


# ── ESC-3: Info general financiamiento ──

def gen_info_financiamiento():
    convos = []
    preguntas = [
        "Como funciona el financiamiento?",
        "Que bancos manejan para credito?",
        "Cuales son los requisitos para un credito automotriz?",
        "Que documentos necesito para sacar un auto a credito?",
        "Cuanto es el enganche minimo?",
        "Puedo aplicar a credito sin comprobantes de nomina?",
        "Aceptan meses sin intereses?",
        "Cuanto tardan en aprobar un credito?",
    ]
    info_fin = {
        "financiamiento": {
            "descripcion": "Financiamiento automotriz con multiples opciones",
            "enganche_minimo": "20%",
            "plazos": "12, 24, 36, 48, 60 meses",
            "bancos": "Trabajamos con 7+ instituciones financieras",
            "proceso": "100% digital, pre-aprobacion en 24 horas",
            "documentos": ["INE vigente", "Comprobante de domicilio (max 3 meses)", "3 estados de cuenta bancarios", "3 recibos de nomina"],
            "nota": "La aprobacion esta sujeta al perfil crediticio del cliente"
        }
    }
    for pregunta in preguntas:
        n = nombre()
        es_msi = "meses sin intereses" in pregunta.lower() or "msi" in pregunta.lower()

        msgs = [
            {"role": "user", "content": f"Hola, soy {n}. {pregunta}"},
            {"role": "assistant", "content": tc("obtener_info_negocio", {"tema": "financiamiento"})},
            {"role": "tool", "content": tr(info_fin)},
        ]

        if es_msi:
            msgs.append({"role": "assistant", "content": f"{n}, no manejamos meses sin intereses (MSI) con tarjeta de credito. Sin embargo, ofrecemos financiamiento automotriz con condiciones muy competitivas:\n\n- Enganche minimo del 20%\n- Plazos de 12 a 60 meses con mensualidades fijas\n- Pre-aprobacion en 24 horas\n- Trabajamos con 7+ bancos para encontrarte la mejor tasa\n\nEl proceso es 100% digital. Te gustaria que te calcule un estimado de mensualidad para algun vehiculo en particular?"})
        else:
            msgs.append({"role": "assistant", "content": f"{n}, con mucho gusto te explico. En TREFA ofrecemos financiamiento automotriz con estas condiciones:\n\n- **Enganche minimo:** 20%\n- **Plazos:** 12 a 60 meses\n- **Pre-aprobacion:** en 24 horas\n- **Proceso:** 100% digital\n\nDocumentos necesarios:\n- INE vigente\n- Comprobante de domicilio (max 3 meses)\n- 3 ultimos estados de cuenta bancarios\n- 3 ultimos recibos de nomina\n\nTrabajamos con multiples bancos para encontrarte la mejor tasa segun tu perfil. Ya tienes algun auto en mente? Asi te calculo un estimado 😊"})

        convos.append(conv(msgs, {"escenario": "info_financiamiento", "tools": ["obtener_info_negocio"]}))
    return convos


# ── ESC-5: Garantia ──

def gen_garantia():
    convos = []
    preguntas = [
        "Que garantia dan sus autos?",
        "Si el auto falla despues de comprarlo, que pasa?",
        "Nunca he comprado un seminuevo, es seguro?",
        "Que revision les hacen a los autos?",
        "Como se que el auto no tiene problemas legales?",
    ]
    info_garantia = {
        "garantias": {
            "cobertura": "12 meses en motor y transmision",
            "monto_maximo": "$100,000 MXN",
            "inspeccion": "150 puntos antes de la venta",
            "certificados": ["REPUVE", "SAT", "TransUnion", "TotalCheck"],
            "devolucion": "7 dias naturales o 500 km",
        }
    }
    for pregunta in preguntas:
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. {pregunta}"},
            {"role": "assistant", "content": tc("obtener_info_negocio", {"tema": "garantias"})},
            {"role": "tool", "content": tr(info_garantia)},
            {"role": "assistant", "content": f"{n}, en TREFA nos tomamos muy en serio la confianza. Cada auto pasa por:\n\n- **Revision mecanica de 150 puntos** antes de ponerse a la venta\n- **Garantia de 12 meses** en motor y transmision (hasta $100,000 MXN)\n- **Certificados de procedencia legal:** REPUVE, SAT, TransUnion y TotalCheck\n- **Devolucion:** 7 dias naturales o 500 km si no quedas satisfecho\n\nPuedes comprar con total tranquilidad. Tienes algun auto en mente que te gustaria conocer? 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "garantia", "tools": ["obtener_info_negocio"]}))
    return convos


# ── ESC-6: Ubicaciones ──

def gen_ubicaciones():
    convos = []
    info_ubicaciones = {
        "ubicaciones": [
            {"sucursal": "Monterrey - San Jeronimo", "direccion": "Aaron Saenz Garza 1902, Local 111, Col. Santa Maria", "horario": "L-V 8:30-18:00, S 9:00-16:00, D 11:00-15:00"},
            {"sucursal": "Guadalupe", "direccion": "Hidalgo 918, Paraiso, 67140 Guadalupe, N.L.", "horario": "L-V 8:30-18:00, S 9:00-16:00, D 11:00-16:00"},
            {"sucursal": "Saltillo", "direccion": "Blvd. Nazario Ortiz 2060, Saltillo, Coah.", "horario": "L-V 9:00-18:00, S 9:00-15:00"},
            {"sucursal": "Reynosa", "direccion": "Blvd. Morelos, Reynosa, Tamps.", "horario": "L-V 9:00-18:00, S 9:00-15:00"},
        ]
    }
    preguntas = [
        "Donde estan ubicados?",
        "Tienen sucursal en Monterrey?",
        "Como llego a su agencia?",
        "En que ciudades tienen sucursales?",
        "Cual es el horario de atencion?",
        "Abren los domingos?",
    ]
    for pregunta in preguntas:
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola, soy {n}. {pregunta}"},
            {"role": "assistant", "content": tc("obtener_info_negocio", {"tema": "ubicaciones"})},
            {"role": "tool", "content": tr(info_ubicaciones)},
            {"role": "assistant", "content": f"{n}, tenemos 4 sucursales:\n\n📍 **Monterrey - San Jeronimo:** Aaron Saenz Garza 1902, Local 111, Col. Santa Maria\n   L-V 8:30-18:00, S 9:00-16:00, D 11:00-15:00\n\n📍 **Guadalupe:** Hidalgo 918, Paraiso\n   L-V 8:30-18:00, S 9:00-16:00, D 11:00-16:00\n\n📍 **Saltillo:** Blvd. Nazario Ortiz 2060\n   L-V 9:00-18:00, S 9:00-15:00\n\n📍 **Reynosa:** Blvd. Morelos\n   L-V 9:00-18:00, S 9:00-15:00\n\nCual te queda mas cerca? Con gusto te agendo una visita 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "ubicaciones", "tools": ["obtener_info_negocio"]}))
    return convos


# ── ESC-7: Trade-in / Intercambio ──

def gen_intercambio():
    convos = []
    autos_cliente = [
        ("Nissan Versa 2018", 2018, 85000),
        ("Toyota Corolla 2019", 2019, 62000),
        ("Hyundai Creta 2020", 2020, 48000),
        ("Mazda 3 2017", 2017, 95000),
        ("Volkswagen Jetta 2016", 2016, 110000),
        ("Honda Civic 2019", 2019, 55000),
    ]
    info_intercambio = {
        "intercambio": {
            "descripcion": "Programa de toma a cuenta",
            "requisitos": "Modelos 2016 en adelante con maximo 120,000 km",
            "proceso": "Evaluacion en sucursal, oferta en el momento, se aplica como parte de pago",
            "documentos": ["Factura original", "INE del propietario", "Tarjeta de circulacion"],
        }
    }
    for auto, ano, km in autos_cliente:
        n = nombre()
        cumple = ano >= 2016 and km <= 120000
        msgs = [
            {"role": "user", "content": f"Hola, soy {n}. Quiero dar mi {auto} a cuenta, tiene {km:,} km"},
            {"role": "assistant", "content": tc("obtener_info_negocio", {"tema": "intercambio"})},
            {"role": "tool", "content": tr(info_intercambio)},
        ]
        if cumple:
            msgs.append({"role": "assistant", "content": f"{n}, tu {auto} con {km:,} km cumple con los requisitos de nuestro programa de toma a cuenta (modelos 2016+ con menos de 120,000 km).\n\nEl proceso es sencillo:\n1. Traes tu auto a la sucursal mas cercana\n2. Nuestro equipo lo evalua (toma aprox. 30 min)\n3. Te damos una oferta en el momento\n4. Se aplica como parte de pago del auto que elijas\n\nNecesitas traer la factura original, INE y tarjeta de circulacion. Ya tienes en mente algun auto de nuestro inventario que te interese? 😊"})
        else:
            msgs.append({"role": "assistant", "content": f"{n}, nuestro programa de toma a cuenta aplica para modelos 2016 en adelante con maximo 120,000 km. Tu {auto} {'excede el kilometraje' if km > 120000 else 'es de un modelo anterior al 2016'}, pero con gusto te puedo ayudar a encontrar financiamiento para el auto que te interese. Tienes algun vehiculo en mente?"})

        convos.append(conv(msgs, {"escenario": "intercambio", "tools": ["obtener_info_negocio"]}))
    return convos


# ── ESC-14: Estadisticas inventario ──

def gen_estadisticas():
    convos = []
    preguntas = [
        "Cuantos autos tienen?",
        "Que marcas manejan?",
        "Cual es el rango de precios?",
        "Tienen autos de menos de 200 mil?",
        "Que es lo mas barato que tienen?",
    ]
    stats = {
        "total_vehiculos": 127,
        "marcas": ["Toyota", "Honda", "Mazda", "Nissan", "Hyundai", "Kia", "Ford", "Chevrolet", "Volkswagen", "Suzuki"],
        "rango_precios": {"minimo": "$189,900", "maximo": "$789,900"},
        "carrocerias": {"SUV": 52, "Sedan": 41, "Pickup": 15, "Hatchback": 12, "Van": 7},
        "ubicaciones": {"Monterrey": 48, "Guadalupe": 35, "Saltillo": 25, "Reynosa": 19},
    }
    for pregunta in preguntas:
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. {pregunta}"},
            {"role": "assistant", "content": tc("estadisticas_inventario", {})},
            {"role": "tool", "content": tr(stats)},
            {"role": "assistant", "content": f"{n}, actualmente tenemos **{stats['total_vehiculos']} vehiculos** disponibles. Manejamos marcas como Toyota, Honda, Mazda, Nissan, Hyundai, Kia, entre otras.\n\nEl rango de precios va desde **{stats['rango_precios']['minimo']}** hasta **{stats['rango_precios']['maximo']}**, con opciones en SUV, Sedan, Pickup y mas.\n\nPara ayudarte mejor, que tipo de auto buscas? Alguna marca, presupuesto o uso especifico? 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "estadisticas_inventario", "tools": ["estadisticas_inventario"]}))
    return convos


# ── ESC-9: Comparar vehiculos ──

def gen_comparacion():
    convos = []
    pares = [(VEHICULOS[1], VEHICULOS[2]), (VEHICULOS[3], VEHICULOS[4]), (VEHICULOS[7], VEHICULOS[8]),
             (VEHICULOS[0], VEHICULOS[10]), (VEHICULOS[5], VEHICULOS[9])]
    for v1, v2 in pares:
        n = nombre()
        comp = {
            "vehiculos": [veh_to_search_result(v1), veh_to_search_result(v2)],
            "recomendacion": f"Ambos son excelentes opciones. El {v1['titulo']} destaca por {'menor precio' if v1['precio'] < v2['precio'] else 'menor kilometraje'}."
        }
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. Estoy indeciso entre el {v1['titulo']} y el {v2['titulo']}, me los puedes comparar?"},
            {"role": "assistant", "content": tc("comparar_vehiculos", {"vehiculo_ids": [v1["id"], v2["id"]]})},
            {"role": "tool", "content": tr(comp)},
            {"role": "assistant", "content": f"{n}, aqui va la comparacion:\n\n**{v1['titulo']}**\n- Precio: {fmt(v1['precio'])} | {v1['km']:,} km | {v1['ubicacion']}\n- Mensualidad desde: {fmt_raw(mensualidad(v1['precio']))}/mes\n\n**{v2['titulo']}**\n- Precio: {fmt(v2['precio'])} | {v2['km']:,} km | {v2['ubicacion']}\n- Mensualidad desde: {fmt_raw(mensualidad(v2['precio']))}/mes\n\n{'El ' + v1['titulo'] + ' tiene menor kilometraje, lo que sugiere menos desgaste.' if v1['km'] < v2['km'] else 'El ' + v2['titulo'] + ' tiene menor kilometraje.'} Ambos incluyen garantia de 12 meses.\n\nTe gustaria agendar una visita para verlos en persona? Asi puedes sentir cual te convence mas 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "comparacion", "tools": ["comparar_vehiculos"]}))
    return convos


# ── ESC-17: Cotizacion por email ──

def gen_cotizacion_email():
    convos = []
    for v in random.sample(VEHICULOS, 6):
        n = nombre()
        email = f"{n.lower()}@gmail.com"
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. Me interesa el {v['titulo']}, me pueden mandar una cotizacion a mi correo?"},
            {"role": "assistant", "content": f"Claro, {n}! Con gusto te envio la cotizacion del {v['titulo']}. Me compartes tu correo electronico?"},
            {"role": "user", "content": email},
            {"role": "assistant", "content": tc("enviar_cotizacion_email", {"email_destino": email, "nombre_cliente": n, "vehiculo_id": v["id"]})},
            {"role": "tool", "content": tr({"status": "enviado", "mensaje": f"Cotizacion enviada a {email}"})},
            {"role": "assistant", "content": f"Listo, {n}! Ya te envie la cotizacion del {v['titulo']} a **{email}**. Revisa tu bandeja de entrada (y spam por si acaso).\n\nLa cotizacion incluye precio, opciones de financiamiento y garantia. Si tienes alguna duda despues de revisarla, aqui estoy para ayudarte. Te gustaria agendar una visita para conocerlo en persona?"},
        ]
        convos.append(conv(msgs, {"escenario": "cotizacion_email", "tools": ["enviar_cotizacion_email"]}))
    return convos


# ── ESC-10: Fuera de tema ──

def gen_fuera_de_tema():
    convos = []
    preguntas = [
        ("Que hora es?", "No tengo acceso a la hora actual, pero con gusto te ayudo con lo que necesites sobre autos. Buscas algun vehiculo en especial? 😊"),
        ("Me recomiendas un restaurante?", "No soy experta en restaurantes, pero si te puedo recomendar el auto perfecto para llegar a uno. Buscas algun tipo de vehiculo?"),
        ("Cuanto cuesta un iPhone?", "Esa pregunta esta fuera de mi area, soy asesora de autos seminuevos en TREFA. Te puedo ayudar a encontrar tu auto ideal?"),
        ("Que opinas de la politica?", "Prefiero no opinar sobre ese tema, pero de autos se un monton. Buscas algun vehiculo en particular? 😊"),
        ("Hola como te llamas y que sabes hacer?", "Soy Mariana, asistente virtual de Autos TREFA. Te puedo ayudar a buscar autos en nuestro inventario, calcular financiamiento, agendar citas y mucho mas. En que te puedo ayudar hoy?"),
    ]
    for pregunta, respuesta in preguntas:
        msgs = [
            {"role": "user", "content": pregunta},
            {"role": "assistant", "content": respuesta},
        ]
        convos.append(conv(msgs, {"escenario": "fuera_de_tema", "tools": []}))
    return convos


# ── LIM-6: Eres un bot? ──

def gen_eres_bot():
    convos = []
    preguntas = [
        "Eres un robot?",
        "Estoy hablando con una persona real?",
        "Eres IA?",
        "Eres una persona o un bot?",
    ]
    for pregunta in preguntas:
        msgs = [
            {"role": "user", "content": pregunta},
            {"role": "assistant", "content": "Soy Mariana, asistente virtual de Autos TREFA 😊 Estoy aqui para ayudarte a encontrar tu auto ideal, calcular financiamiento, y resolver todas tus dudas. En que te puedo apoyar?"},
        ]
        convos.append(conv(msgs, {"escenario": "identidad_bot", "tools": []}))
    return convos


# ── LIM-3: No negociar precios ──

def gen_no_negociar():
    convos = []
    for v in random.sample(VEHICULOS, 6):
        n = nombre()
        descuento = random.choice([10000, 20000, 30000, 50000])
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. Me interesa el {v['titulo']} pero esta en {fmt(v['precio'])}, me lo dejan en {fmt(v['precio'] - descuento)}?"},
            {"role": "assistant", "content": tc("calcular_financiamiento", {"precio_vehiculo": v["precio"], "enganche_porcentaje": 20, "plazo_meses": 48})},
            {"role": "tool", "content": tr(calc_financiamiento(v["precio"]))},
            {"role": "assistant", "content": f"{n}, nuestros precios son competitivos y finales en la plataforma. Sin embargo, lo que si podemos hacer es encontrarte la mejor opcion de financiamiento. Con 20% de enganche, la mensualidad queda en aproximadamente {fmt_raw(mensualidad(v['precio']))}/mes a 48 meses.\n\nSi aumentas el enganche o el plazo, la mensualidad baja considerablemente. Te gustaria que hagamos diferentes calculos para encontrar la mensualidad que mejor se ajuste a tu presupuesto?"},
        ]
        convos.append(conv(msgs, {"escenario": "no_negociar_precio", "tools": ["calcular_financiamiento"]}))
    return convos


# ── LIM-1: No dar consejos legales/fiscales ──

def gen_no_consejo_legal():
    convos = []
    preguntas = [
        ("Puedo deducir el auto de impuestos?", "Ese tema depende de tu regimen fiscal y es mejor consultarlo con tu contador. Lo que si puedo hacer es enviarte la factura del vehiculo y ayudarte con el tramite de compra. Tienes algun auto en mente?"),
        ("Que seguro me recomiendan?", "No somos expertos en seguros, pero te recomiendo cotizar con varias aseguradoras. Nosotros nos enfocamos en que tu auto este en perfectas condiciones con nuestra garantia de 12 meses. Buscas algun vehiculo en particular?"),
        ("Si tengo un accidente en los primeros dias, que pasa?", "Para temas de accidentes te recomiendo consultar con tu aseguradora. De nuestro lado, contamos con garantia de 12 meses en motor y transmision, y devolucion en los primeros 7 dias o 500 km. En que mas te puedo ayudar?"),
    ]
    for pregunta, respuesta in preguntas:
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. {pregunta}"},
            {"role": "assistant", "content": respuesta},
        ]
        convos.append(conv(msgs, {"escenario": "limite_legal_fiscal", "tools": []}))
    return convos


# ── ESC-11: Cliente frustrado ──

def gen_cliente_frustrado():
    convos = []
    frustraciones = [
        ("Llevo 3 dias esperando que me contesten y nadie me hace caso!", "Lamento mucho la demora, {n}. Entiendo tu frustracion y tienes toda la razon en estar molesto. Dejame ayudarte ahora mismo. En que puedo apoyarte? Si lo prefieres, puedo conectarte directamente con un asesor de ventas para atencion personalizada."),
        ("Me dijeron que el auto estaba disponible y cuando fui ya lo habian vendido!", "Que mala experiencia, {n}, te ofrezco una disculpa. El inventario cambia rapido y a veces los autos se venden en cuestion de horas. Me gustaria compensar buscandote opciones similares ahora mismo. Que tipo de auto buscabas?"),
        ("El proceso de credito ha sido un desastre, nadie me da informacion clara", "{n}, lamento mucho que hayas tenido esa experiencia. Dejame conectarte con nuestro equipo de financiamiento para que te den seguimiento personalizado. Me compartes tu nombre completo y telefono para que te contacten directamente?"),
    ]
    for queja, respuesta_template in frustraciones:
        n = nombre()
        respuesta = respuesta_template.format(n=n)
        msgs = [
            {"role": "user", "content": queja},
            {"role": "assistant", "content": f"Hola, lamento escuchar esto. Con quien tengo el gusto para poder ayudarte?"},
            {"role": "user", "content": f"Soy {n}"},
            {"role": "assistant", "content": respuesta},
        ]
        convos.append(conv(msgs, {"escenario": "cliente_frustrado", "tools": []}))
    return convos


# ── ESC-4: Documentos requeridos ──

def gen_documentos():
    convos = []
    preguntas = [
        "Que papeles necesito para comprar?",
        "Que documentos piden para el credito?",
        "Que necesito llevar para la compra de contado?",
        "Tengo que llevar algo especial si voy a la sucursal?",
    ]
    info_docs = {
        "documentos_requeridos": {
            "credito": ["INE vigente (frente y reverso)", "Comprobante de domicilio (maximo 3 meses)", "3 ultimos estados de cuenta bancarios", "3 ultimos recibos de nomina o constancia de ingresos"],
            "contado": ["INE vigente", "Comprobante de domicilio"],
            "nota": "Para credito, la aprobacion tarda 24-48 horas habiles"
        }
    }
    for pregunta in preguntas:
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola, soy {n}. {pregunta}"},
            {"role": "assistant", "content": tc("obtener_info_negocio", {"tema": "documentos_requeridos"})},
            {"role": "tool", "content": tr(info_docs)},
            {"role": "assistant", "content": f"{n}, aqui te comparto los documentos segun tu forma de pago:\n\n**Para credito:**\n- INE vigente (frente y reverso)\n- Comprobante de domicilio (max 3 meses)\n- 3 ultimos estados de cuenta bancarios\n- 3 ultimos recibos de nomina\n\n**Para contado:**\n- INE vigente\n- Comprobante de domicilio\n\nLa aprobacion de credito tarda 24-48 horas habiles. Ya tienes algun vehiculo en mente? 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "documentos_requeridos", "tools": ["obtener_info_negocio"]}))
    return convos


# ── ESC-8: Devoluciones ──

def gen_devoluciones():
    convos = []
    preguntas = [
        "Si no me gusta el auto puedo devolverlo?",
        "Tienen politica de devolucion?",
        "Cuantos dias tengo para devolver?",
    ]
    info_dev = {
        "devoluciones": {
            "plazo": "7 dias naturales o 500 km (lo que ocurra primero)",
            "condiciones": "El vehiculo debe estar en las mismas condiciones",
            "proceso": "Se realiza en la misma sucursal donde se adquirio",
        }
    }
    for pregunta in preguntas:
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. {pregunta}"},
            {"role": "assistant", "content": tc("obtener_info_negocio", {"tema": "devoluciones"})},
            {"role": "tool", "content": tr(info_dev)},
            {"role": "assistant", "content": f"{n}, si! Tienes **7 dias naturales o 500 km** (lo que ocurra primero) para devolver el vehiculo si no quedas satisfecho. La condicion es que el auto este en las mismas condiciones en que lo recibiste.\n\nEs una de las ventajas de comprar en una agencia establecida como TREFA. Buscas algun auto en especial? 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "devoluciones", "tools": ["obtener_info_negocio"]}))
    return convos


# ── ESC-16: Proceso de compra ──

def gen_proceso_compra():
    convos = []
    preguntas = [
        "Como es el proceso de compra?",
        "Cuales son los pasos para comprar un auto con ustedes?",
        "Quiero comprar, que sigue?",
    ]
    info_proceso = {
        "proceso_compra": {
            "pasos": [
                "1. Elige tu vehiculo en nuestro sitio o con tu asesora",
                "2. Agenda una cita para conocerlo y probarlo",
                "3. Si decides comprarlo, elige forma de pago (contado o credito)",
                "4. Para credito: sube tus documentos y recibe pre-aprobacion en 24h",
                "5. Firma de contrato y entrega del vehiculo",
            ],
            "tiempo_estimado": "El proceso puede completarse en 3-5 dias habiles"
        }
    }
    for pregunta in preguntas:
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola, soy {n}. {pregunta}"},
            {"role": "assistant", "content": tc("obtener_info_negocio", {"tema": "proceso_compra"})},
            {"role": "tool", "content": tr(info_proceso)},
            {"role": "assistant", "content": f"{n}, el proceso es muy sencillo:\n\n1. **Elige tu vehiculo** en nuestro sitio o aqui conmigo\n2. **Agenda una cita** para conocerlo y probarlo en sucursal\n3. **Elige forma de pago:** contado o credito\n4. **Para credito:** sube tus documentos y recibe pre-aprobacion en 24h\n5. **Firma y entrega** del vehiculo\n\nTodo puede estar listo en 3-5 dias habiles. Ya tienes algun auto en mente o te ayudo a buscar opciones?"},
        ]
        convos.append(conv(msgs, {"escenario": "proceso_compra", "tools": ["obtener_info_negocio"]}))
    return convos


# ── ESC-15: Cita / prueba de manejo ──

def gen_cita_prueba():
    convos = []
    for v in random.sample(VEHICULOS, 6):
        n = nombre()
        tel = telefono()
        sucursal = v["ubicacion"]
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. Quiero ir a ver el {v['titulo']}, puedo agendar una cita?"},
            {"role": "assistant", "content": f"Claro, {n}! El {v['titulo']} esta disponible en nuestra sucursal de **{sucursal}**. Para agendarte, me compartes tu numero de telefono?"},
            {"role": "user", "content": tel},
            {"role": "assistant", "content": tc("solicitar_datos_contacto", {"nombre": n, "telefono": tel, "vehiculo_interes": v["titulo"], "comentarios": f"Quiere agendar cita en {sucursal}"})},
            {"role": "tool", "content": tr({"status": "registrado", "mensaje": f"Datos de {n} registrados exitosamente"})},
            {"role": "assistant", "content": f"Listo, {n}! Ya registre tus datos. Un asesor de nuestra sucursal de {sucursal} te contactara para confirmar fecha y hora de tu visita.\n\nRecuerda traer tu INE vigente, y si quieres hacer prueba de manejo, tambien tu licencia de conducir. Te esperamos! 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "cita_prueba_manejo", "tools": ["solicitar_datos_contacto"]}))
    return convos


# ── Flujo completo: busqueda → detalle → financiamiento → contacto ──

def gen_flujo_completo():
    convos = []
    flujos = [
        ("SUV", None, 500000, "Busco una SUV familiar, presupuesto de 500 mil"),
        ("Sedan", None, 350000, "Necesito un sedan, hasta 350 mil"),
        (None, "Toyota", 550000, "Me gustan los Toyota, tengo 550 mil"),
        ("SUV", None, 420000, "Busco camioneta por debajo de 420 mil"),
        (None, "Mazda", 500000, "Quiero un Mazda, presupuesto 500 mil"),
        ("Sedan", None, 300000, "Busco un sedan economico hasta 300 mil"),
        ("SUV", None, 450000, "Ando buscando SUV, tengo 450 mil"),
        (None, "Nissan", 350000, "Que Nissan tienen? Presupuesto 350 mil"),
    ]
    for carroceria, marca, precio_max, pregunta in flujos:
        n = nombre()
        tel = telefono()
        email = f"{n.lower().replace(' ','')}@gmail.com"

        results = buscar(marca=marca, carroceria=carroceria, precio_max=precio_max)[:3]
        if not results:
            results = random.sample(VEHICULOS, 2)
        v = results[0]

        search_args = {}
        if marca: search_args["marca"] = marca
        if carroceria: search_args["tipo_carroceria"] = carroceria
        if precio_max: search_args["precio_maximo"] = precio_max

        enganche = random.choice([20, 25, 30])
        plazo = random.choice([36, 48, 60])
        fin = calc_financiamiento(v["precio"], enganche, plazo)

        opciones = "\n".join([f"**{r['titulo']}** - {fmt(r['precio'])} | {r['km']:,} km | {r['ubicacion']}" for r in results])

        msgs = [
            {"role": "user", "content": random.choice(SALUDOS_USER)},
            {"role": "assistant", "content": random.choice(SALUDOS_BOT)},
            {"role": "user", "content": f"Soy {n}. {pregunta}"},
            # Busqueda
            {"role": "assistant", "content": tc("buscar_vehiculos", search_args)},
            {"role": "tool", "content": tr({"vehiculos": [veh_to_search_result(r) for r in results], "total": len(results)})},
            {"role": "assistant", "content": f"Mucho gusto, {n}! Encontre estas opciones:\n\n{opciones}\n\nTodos incluyen garantia de 12 meses y revision mecanica completa. Alguno te llama la atencion?"},
            # Interes en uno
            {"role": "user", "content": f"Me interesa el {v['titulo']}, cuanto saldria con {enganche}% de enganche?"},
            # Financiamiento
            {"role": "assistant", "content": tc("calcular_financiamiento", {"precio_vehiculo": v["precio"], "enganche_porcentaje": enganche, "plazo_meses": plazo})},
            {"role": "tool", "content": tr(fin)},
            {"role": "assistant", "content": f"Excelente eleccion! Aqui va el calculo del {v['titulo']}:\n\n**Enganche ({enganche}%):** {fin['enganche']}\n**Mensualidad:** {fin['mensualidad_estimada']} a {plazo} meses\n\nEste es un estimado, la tasa puede variar segun tu perfil. Te gustaria que un asesor te contacte para iniciar el proceso de pre-aprobacion?"},
            # Datos de contacto
            {"role": "user", "content": f"Si, mi telefono es {tel}"},
            {"role": "assistant", "content": tc("solicitar_datos_contacto", {"nombre": n, "telefono": tel, "vehiculo_interes": v["titulo"], "comentarios": f"Interesado en financiamiento {enganche}% enganche, {plazo} meses"})},
            {"role": "tool", "content": tr({"status": "registrado", "mensaje": f"Datos de {n} registrados"})},
            {"role": "assistant", "content": f"Perfecto, {n}! Ya registre tus datos. Un asesor te contactara en breve para guiarte con el proceso de financiamiento del {v['titulo']}.\n\nMientras tanto, puedes ir reuniendo tus documentos: INE, comprobante de domicilio, estados de cuenta y recibos de nomina. Algo mas en lo que te pueda ayudar? 😊"},
        ]

        convos.append(conv(msgs, {"escenario": "flujo_completo", "tools": ["buscar_vehiculos", "calcular_financiamiento", "solicitar_datos_contacto"]}))
    return convos


# ── Flujo completo con cotizacion email ──

def gen_flujo_con_email():
    convos = []
    for v in random.sample(VEHICULOS, 5):
        n = nombre()
        email = f"{n.lower().replace(' ','')}@gmail.com"
        enganche = random.choice([20, 25])
        plazo = random.choice([48, 60])
        fin = calc_financiamiento(v["precio"], enganche, plazo)

        msgs = [
            {"role": "user", "content": f"Hola soy {n}, me interesa el {v['titulo']}"},
            {"role": "assistant", "content": tc("obtener_vehiculo", {"id": v["id"]})},
            {"role": "tool", "content": tr(veh_to_detail(v))},
            {"role": "assistant", "content": f"Hola {n}! El {v['titulo']} es una excelente opcion:\n\n**{v['titulo']}**\n- Precio: {fmt(v['precio'])}\n- Kilometraje: {v['km']:,} km\n- Transmision: {v['transmision']}\n- Ubicacion: {v['ubicacion']}\n- Garantia: 12 meses motor y transmision\n\nRevision mecanica de 150 puntos aprobada. Te gustaria que te calcule el financiamiento o prefieres verlo de contado?"},
            {"role": "user", "content": f"Si, calculame con {enganche}% a {plazo} meses. Y me mandas la cotizacion a mi correo {email}?"},
            {"role": "assistant", "content": tc("calcular_financiamiento", {"precio_vehiculo": v["precio"], "enganche_porcentaje": enganche, "plazo_meses": plazo})},
            {"role": "tool", "content": tr(fin)},
            {"role": "assistant", "content": tc("enviar_cotizacion_email", {"email_destino": email, "nombre_cliente": n, "vehiculo_id": v["id"], "enganche_porcentaje": enganche, "plazo_meses": plazo})},
            {"role": "tool", "content": tr({"status": "enviado", "mensaje": f"Cotizacion enviada a {email}"})},
            {"role": "assistant", "content": f"{n}, aqui tienes el resumen y ya te envie la cotizacion completa a **{email}**:\n\n**{v['titulo']}** - {fmt(v['precio'])}\n- Enganche ({enganche}%): {fin['enganche']}\n- Mensualidad: {fin['mensualidad_estimada']} a {plazo} meses\n\nRevisa tu correo, ahi encontraras todos los detalles. Si quieres dar el siguiente paso, puedo conectarte con un asesor o agendar una visita a la sucursal de {v['ubicacion']}. Que prefieres? 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "flujo_completo_email", "tools": ["obtener_vehiculo", "calcular_financiamiento", "enviar_cotizacion_email"]}))
    return convos


# ── ESC-13: Mensaje ambiguo ──

def gen_ambiguo():
    convos = []
    ambiguos = [
        ("Quiero un carro", "Con gusto te ayudo! Para encontrarte la mejor opcion, me platicas un poco mas? Por ejemplo, que tipo de auto buscas (SUV, sedan, pickup), o si tienes un presupuesto en mente?"),
        ("Cuanto cuesta?", "Con gusto te ayudo con el precio! Me dices cual auto te interesa? O si prefieres, dime tu presupuesto y te muestro las opciones disponibles 😊"),
        ("Informacion", "Claro! Con gusto te doy informacion. Buscas algun auto en especial o te gustaria conocer nuestro inventario general?"),
        ("Me mandas fotos?", "Con gusto! Me dices de cual auto te gustaria ver las fotos? Si me compartes marca, modelo o presupuesto, te busco opciones disponibles."),
        ("200 a 300", "Perfecto, presupuesto de $200,000 a $300,000 MXN. Que tipo de auto te interesa? Tenemos sedanes, SUVs, hatchbacks y mas en ese rango. Alguna preferencia de marca?"),
    ]
    for pregunta, respuesta in ambiguos:
        msgs = [
            {"role": "user", "content": pregunta},
            {"role": "assistant", "content": respuesta},
        ]
        convos.append(conv(msgs, {"escenario": "ambiguo", "tools": []}))
    return convos


# ── Detalle de vehiculo especifico ──

def gen_detalle_vehiculo():
    convos = []
    for v in random.sample(VEHICULOS, 8):
        n = nombre()
        msgs = [
            {"role": "user", "content": f"Hola soy {n}. Me interesa el {v['titulo']}, me das mas detalles?"},
            {"role": "assistant", "content": tc("obtener_vehiculo", {"id": v["id"]})},
            {"role": "tool", "content": tr(veh_to_detail(v))},
            {"role": "assistant", "content": f"{n}, aqui tienes los detalles del {v['titulo']}:\n\n**{v['titulo']}**\n📅 {v['ano']} | ⚙️ {v['transmision']} | ⛽ {v['combustible']}\n💰 Precio: {fmt(v['precio'])}\n💳 Desde {fmt_raw(mensualidad(v['precio']))}/mes (48 meses, 20% enganche)\n📍 Disponible en: {v['ubicacion']}\n🔧 Kilometraje: {v['km']:,} km\n\nIncluye garantia de 12 meses en motor y transmision, y paso revision mecanica de 150 puntos.\n\nTe gustaria calcular un plan de financiamiento personalizado o agendar una visita para conocerlo? 😊"},
        ]
        convos.append(conv(msgs, {"escenario": "detalle_vehiculo", "tools": ["obtener_vehiculo"]}))
    return convos


# ── Ortografia mala del usuario (variacion realista) ──

def gen_ortografia_real():
    convos = []
    mensajes = [
        ("busco una pico 6 cilindros chevrole", "Pickup", "Chevrolet"),
        ("kiero un carro bonito y varato", None, None),
        ("q carros tienen en monterrei", None, None),
        ("cuanto sale la tukson", None, "Hyundai"),
        ("tienen nissan bersa?", "Sedan", "Nissan"),
    ]
    for msg, carroceria, marca in mensajes:
        n = nombre()
        if marca:
            results = buscar(marca=marca, carroceria=carroceria)[:2]
            if not results:
                results = random.sample(VEHICULOS, 2)
            search_args = {}
            if marca: search_args["marca"] = marca
            if carroceria: search_args["tipo_carroceria"] = carroceria
            opciones = "\n".join([f"**{r['titulo']}** - {fmt(r['precio'])} | {r['ubicacion']}" for r in results])
            msgs = [
                {"role": "user", "content": msg},
                {"role": "assistant", "content": f"Hola! Soy Mariana de Autos TREFA. Con gusto te busco opciones. Con quien tengo el gusto?"},
                {"role": "user", "content": n},
                {"role": "assistant", "content": tc("buscar_vehiculos", search_args)},
                {"role": "tool", "content": tr({"vehiculos": [veh_to_search_result(r) for r in results], "total": len(results)})},
                {"role": "assistant", "content": f"Mucho gusto, {n}! Encontre estas opciones:\n\n{opciones}\n\nTodas con garantia de 12 meses. Alguna te interesa? 😊"},
            ]
        else:
            msgs = [
                {"role": "user", "content": msg},
                {"role": "assistant", "content": f"Hola! Soy Mariana de Autos TREFA 😊 Con gusto te ayudo. Para mostrarte las mejores opciones, me dices que tipo de auto buscas? Algun presupuesto en mente?"},
            ]
        convos.append(conv(msgs, {"escenario": "ortografia_real", "tools": ["buscar_vehiculos"] if marca else []}))
    return convos


# ─── Main: generar todo ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generador de conversaciones de entrenamiento Mariana")
    parser.add_argument("--output", default="dataset_mariana_training.jsonl", help="Archivo de salida JSONL")
    parser.add_argument("--seed", type=int, default=42, help="Semilla para reproducibilidad")
    args = parser.parse_args()

    random.seed(args.seed)

    generators = [
        ("Saludo simple", gen_saludo_simple),
        ("Busqueda exitosa", gen_busqueda_exitosa),
        ("Sin inventario + alternativas", gen_sin_inventario),
        ("Financiamiento calculo", gen_financiamiento),
        ("Info financiamiento general", gen_info_financiamiento),
        ("Garantia", gen_garantia),
        ("Ubicaciones", gen_ubicaciones),
        ("Intercambio / trade-in", gen_intercambio),
        ("Estadisticas inventario", gen_estadisticas),
        ("Comparacion vehiculos", gen_comparacion),
        ("Cotizacion email", gen_cotizacion_email),
        ("Fuera de tema", gen_fuera_de_tema),
        ("Eres un bot?", gen_eres_bot),
        ("No negociar precio", gen_no_negociar),
        ("No consejo legal/fiscal", gen_no_consejo_legal),
        ("Cliente frustrado", gen_cliente_frustrado),
        ("Documentos requeridos", gen_documentos),
        ("Devoluciones", gen_devoluciones),
        ("Proceso de compra", gen_proceso_compra),
        ("Cita / prueba manejo", gen_cita_prueba),
        ("Flujo completo", gen_flujo_completo),
        ("Flujo completo con email", gen_flujo_con_email),
        ("Mensaje ambiguo", gen_ambiguo),
        ("Detalle vehiculo", gen_detalle_vehiculo),
        ("Ortografia real", gen_ortografia_real),
    ]

    all_convos = []
    print(f"Generando conversaciones de entrenamiento...\n")
    for name, gen_func in generators:
        convos = gen_func()
        all_convos.extend(convos)
        print(f"  {name:40s} {len(convos):3d} conversaciones")

    random.shuffle(all_convos)

    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        for c in all_convos:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\n{'='*60}")
    print(f"  Total: {len(all_convos)} conversaciones")
    print(f"  Archivo: {output_path}")
    print(f"  Tamano: {output_path.stat().st_size / 1024:.0f} KB")

    # Estadisticas por escenario
    from collections import Counter
    escenarios = Counter(c["metadata"]["escenario"] for c in all_convos)
    print(f"\n  Distribucion por escenario:")
    for esc, count in escenarios.most_common():
        print(f"    {esc:40s} {count:3d}")

    # Estadisticas de tools
    tools_used = Counter()
    for c in all_convos:
        for t in c["metadata"].get("tools", []):
            tools_used[t] += 1
    if tools_used:
        print(f"\n  Tools utilizadas:")
        for tool, count in tools_used.most_common():
            print(f"    {tool:40s} {count:3d}")

    print(f"\n  Listo!")


if __name__ == "__main__":
    main()
