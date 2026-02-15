"""
generators.py — Lógica de generación de datasets TREFA.
Incluye: plantillas (sin LLM), sintético con Claude, pipeline TC con Gemini, curación.
"""

import asyncio
import csv
import json
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger()

from app.trefa_assets import (
    ESCENARIOS,
    ESCENARIOS_TC,
    META_PROMPT_E1_TC,
    META_PROMPT_E2_TC,
    META_PROMPT_SINTETICO,
    PERSONALIDADES,
    PERSONALIDADES_TC,
    PROMPT_EVALUAR_CURACION,
    PROMPT_EVALUAR_TC,
    PROMPT_MEJORAR,
    SEMILLAS_REALES,
    SYSTEM_PROMPT_CON_TOOLS,
    SYSTEM_PROMPT_SIMPLE,
    SYSTEM_PROMPT_TREFA_BOT,
    VEHICULOS_MUESTRA,
    calcular_financiamiento_mcp,
    fmt_precio,
    presentar_vehiculo,
    tool_call,
    tool_response,
    vehiculo_a_mcp_busqueda,
    vehiculo_a_mcp_comparacion,
    vehiculo_a_mcp_detalle,
)


# ============================================================
# 4.1 GENERACIÓN POR PLANTILLAS (sin LLM)
# ============================================================

def _saludo_simple():
    saludos = ["hola", "buenos días", "buenas tardes", "hola buenas", "hey", "qué tal"]
    respuestas = [
        "¡Hola! 👋 Bienvenido a Autos TREFA. ¿Estás buscando un auto en especial o quieres ver qué opciones tenemos disponibles?",
        "¡Hola! 👋 Bienvenido a Autos TREFA. ¿Tienes alguna marca o modelo en mente, o prefieres que te muestre opciones por presupuesto?",
        "¡Buenas tardes! 👋 Soy Mariana, del equipo de Autos TREFA. ¿En qué puedo ayudarte? ¿Buscas algún auto en particular?",
    ]
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": random.choice(saludos)},
        {"role": "assistant", "content": random.choice(respuestas)},
    ]}


def _busqueda_marca():
    v = random.sample(VEHICULOS_MUESTRA, min(3, len(VEHICULOS_MUESTRA)))
    marca = v[0]["marca"]
    v_mcp = [vehiculo_a_mcp_busqueda(vi) for vi in v]
    presentacion = f"¡Claro! Déjame buscar qué {marca} tenemos disponibles 🔍\n\n"
    presentacion += "\n\n".join([presentar_vehiculo(vi) for vi in v])
    presentacion += "\n\n¿Alguno te llama la atención? Puedo darte más detalles o calcular un financiamiento personalizado."
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": f"Busco un {marca}"},
        {"role": "assistant", "content": tool_call("buscar_vehiculos", {"marca": marca})},
        {"role": "tool", "content": tool_response("buscar_vehiculos", {"vehiculos": v_mcp, "total": len(v_mcp)})},
        {"role": "assistant", "content": presentacion},
    ]}


def _busqueda_presupuesto():
    presupuesto = random.choice([250000, 300000, 350000, 400000, 450000, 500000])
    v = [vi for vi in VEHICULOS_MUESTRA if vi["precio"] <= presupuesto][:3]
    if not v:
        v = VEHICULOS_MUESTRA[:3]
    v_mcp = [vehiculo_a_mcp_busqueda(vi) for vi in v]
    presentacion = f"Tengo excelentes opciones dentro de tu presupuesto de {fmt_precio(presupuesto)} 😊\n\n"
    presentacion += "\n\n".join([presentar_vehiculo(vi) for vi in v])
    presentacion += "\n\n¿Te interesa alguno? Puedo calcular las mensualidades con el enganche que prefieras."
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": f"Busco un auto, mi presupuesto es de {presupuesto // 1000} mil pesos"},
        {"role": "assistant", "content": tool_call("buscar_vehiculos", {"precio_maximo": presupuesto})},
        {"role": "tool", "content": tool_response("buscar_vehiculos", {"vehiculos": v_mcp, "total": len(v_mcp)})},
        {"role": "assistant", "content": presentacion},
    ]}


def _busqueda_tipo():
    tipos = [("SUV", "SUV"), ("sedan", "Sedan"), ("camioneta", "SUV")]
    tipo_user, tipo_filtro = random.choice(tipos)
    v = [vi for vi in VEHICULOS_MUESTRA if vi["carroceria"] == tipo_filtro][:3]
    v_mcp = [vehiculo_a_mcp_busqueda(vi) for vi in v]
    presentacion = f"¡Tenemos varias opciones de {tipo_user}! Aquí van las más destacadas:\n\n"
    presentacion += "\n\n".join([presentar_vehiculo(vi) for vi in v])
    presentacion += "\n\n¿Alguna te interesa? Puedo darte todos los detalles."
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": f"Quiero ver {tipo_user}s"},
        {"role": "assistant", "content": tool_call("buscar_vehiculos", {"tipo_carroceria": tipo_filtro})},
        {"role": "tool", "content": tool_response("buscar_vehiculos", {"vehiculos": v_mcp, "total": len(v_mcp)})},
        {"role": "assistant", "content": presentacion},
    ]}


def _auto_no_disponible():
    marca = random.choice(["BMW", "Audi", "Mercedes", "Subaru"])
    alternativas = random.sample(VEHICULOS_MUESTRA, 3)
    alt_mcp = [vehiculo_a_mcp_busqueda(vi) for vi in alternativas]
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": f"Tienen algún {marca}?"},
        {"role": "assistant", "content": tool_call("buscar_vehiculos", {"marca": marca})},
        {"role": "tool", "content": tool_response("buscar_vehiculos", {"vehiculos": [], "total": 0})},
        {"role": "assistant", "content": tool_call("buscar_alternativas", {"marca_original": marca, "presupuesto": 450000})},
        {"role": "tool", "content": tool_response("buscar_alternativas", {"alternativas": alt_mcp, "total": len(alt_mcp)})},
        {"role": "assistant", "content": f"Actualmente no tenemos {marca} en inventario, pero tengo estas opciones que podrían interesarte:\n\n" + "\n\n".join([presentar_vehiculo(v) for v in alternativas]) + "\n\n¿Te gustaría saber más de alguno?"},
    ]}


def _detalle_vehiculo():
    v = random.choice(VEHICULOS_MUESTRA)
    v_mcp = vehiculo_a_mcp_detalle(v)
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": f"Me interesa el {v['titulo']}, ¿qué más me puedes decir?"},
        {"role": "assistant", "content": tool_call("obtener_vehiculo", {"id": v["id"]})},
        {"role": "tool", "content": tool_response("obtener_vehiculo", v_mcp)},
        {"role": "assistant", "content": f"¡Excelente elección! Aquí tienes todos los detalles del **{v['titulo']}**:\n\n📅 Año: {v['autoano']}\n⚙️ Transmisión: {v['transmision']}\n⛽ Combustible: {v['combustible']}\n🔧 Motor: {v_mcp['motor']}, {v_mcp['cilindros']} cilindros\n📏 Kilometraje: {v['kilometraje']:,} km\n🚗 Carrocería: {v['carroceria']}\n🛡️ Garantía: {v['garantia']}\n\n💰 Precio: {fmt_precio(v['precio'])}\n💳 Enganche mínimo: {fmt_precio(int(v['precio'] * 0.2))}\n📊 Mensualidad desde: {fmt_precio(v['mensualidad_minima'])}/mes\n\n📍 Disponible en: {v['ubicacion']}\n🔗 Ver más: https://autostrefa.mx/inventario/{v['slug']}\n\n¿Te gustaría que calcule el financiamiento con un enganche y plazo específicos?"},
    ]}


def _calcular_financiamiento():
    v = random.choice(VEHICULOS_MUESTRA)
    enganche_pct = random.choice([20, 25, 30])
    plazo = random.choice([36, 48, 60])
    precio = v["precio"]
    resultado = calcular_financiamiento_mcp(precio, enganche_pct, plazo)
    enganche = int(precio * enganche_pct / 100)
    monto = precio - enganche
    tasa_m = 0.15 / 12
    mensualidad = int(monto * (tasa_m * (1 + tasa_m)**plazo) / ((1 + tasa_m)**plazo - 1))
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": f"¿Cuánto pagaría mensualmente por el {v['titulo']} con {enganche_pct}% de enganche a {plazo} meses?"},
        {"role": "assistant", "content": tool_call("calcular_financiamiento", {"precio_vehiculo": precio, "enganche_porcentaje": enganche_pct, "plazo_meses": plazo})},
        {"role": "tool", "content": tool_response("calcular_financiamiento", resultado)},
        {"role": "assistant", "content": f"Aquí está tu cotización para el **{v['titulo']}**:\n\n💰 Precio: {fmt_precio(precio)}\n💵 Enganche ({enganche_pct}%): {fmt_precio(enganche)}\n📊 Monto a financiar: {fmt_precio(monto)}\n📅 Plazo: {plazo} meses\n💳 **Mensualidad estimada: {fmt_precio(mensualidad)}/mes**\n\n⚠️ La mensualidad es estimada con una tasa referencia del 15% anual. La tasa final depende de tu perfil crediticio.\n\n¿Te gustaría que un asesor te contacte para iniciar el proceso?"},
    ]}


def _pregunta_info_negocio():
    temas = [
        ("¿Manejan financiamiento?", "financiamiento", "¡Claro que sí! En Autos TREFA trabajamos con múltiples bancos aliados:\n\n💵 Enganche mínimo: 20%\n📅 Plazos: 12 a 60 meses\n🏦 Múltiples instituciones financieras\n\nLa tasa depende de tu perfil crediticio. ¿Ya tienes un auto en mente? 😊"),
        ("¿Los autos tienen garantía?", "garantias", "¡Sí! Todos nuestros vehículos incluyen **garantía mecánica de 3 meses o 5,000 km**. ¿Hay algún auto que te interese? 😊"),
        ("¿Dónde están ubicados?", "ubicaciones", "Contamos con 4 sucursales:\n\n📍 Monterrey\n📍 Guadalupe\n📍 Saltillo\n📍 Reynosa\n\n🕘 Horario: Lunes a Sábado de 9:00 a 18:00\n\n¿Cuál sucursal te queda más cerca?"),
        ("¿Reciben autos a cuenta?", "intercambio", "¡Sí, recibimos tu auto como parte de pago!\n\n🚗 Modelo 2015 en adelante\n📏 Máximo 120,000 km\n\n¿Qué auto tienes actualmente?"),
        ("¿Tienen política de devolución?", "devoluciones", "Sí, contamos con **política de devolución de 7 días o 500 km**. ¿Hay algo más en lo que pueda ayudarte?"),
    ]
    pregunta, tema, respuesta_texto = random.choice(temas)
    info = {"informacion": [{"id": 1, "titulo": tema.capitalize(), "contenido": respuesta_texto}]}
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": pregunta},
        {"role": "assistant", "content": tool_call("obtener_info_negocio", {"tema": tema})},
        {"role": "tool", "content": tool_response("obtener_info_negocio", info)},
        {"role": "assistant", "content": respuesta_texto},
    ]}


def _comparar_vehiculos():
    v1, v2 = random.sample(VEHICULOS_MUESTRA, 2)
    v1_mcp = vehiculo_a_mcp_comparacion(v1)
    v2_mcp = vehiculo_a_mcp_comparacion(v2)
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": f"¿Me puedes comparar el {v1['titulo']} con el {v2['titulo']}?"},
        {"role": "assistant", "content": tool_call("comparar_vehiculos", {"vehiculo_ids": [v1["id"], v2["id"]]})},
        {"role": "tool", "content": tool_response("comparar_vehiculos", {"vehiculos": [v1_mcp, v2_mcp]})},
        {"role": "assistant", "content": f"Aquí tienes la comparación:\n\n**{v1['titulo']}**\n📅 {v1['autoano']} | ⚙️ {v1['transmision']}\n📏 {v1['kilometraje']:,} km\n💰 {fmt_precio(v1['precio'])}\n📍 {v1['ubicacion']}\n\n**{v2['titulo']}**\n📅 {v2['autoano']} | ⚙️ {v2['transmision']}\n📏 {v2['kilometraje']:,} km\n💰 {fmt_precio(v2['precio'])}\n📍 {v2['ubicacion']}\n\n¿Te interesa alguno en particular?"},
    ]}


def _conversacion_simple():
    """Escenarios simples sin tool calling."""
    opciones = [
        ("¿Qué opinas del clima?", "Me encantaría platicar de eso 😄, pero estoy especializada en ayudarte con autos. ¿Hay algo en lo que pueda ayudarte con tu próximo auto?"),
        ("Ya me cansé, llevo rato buscando", "Entiendo tu frustración, y quiero ayudarte. ¿Me podrías decir qué es lo más importante para ti? Puedo hacer una búsqueda más precisa 😊"),
        ("¿Eres un bot?", "¡Cómo adivinaste! Sí, soy Mariana, la asistente virtual de Autos TREFA. Puedo consultar inventario real, calcular financiamientos y conectarte con un asesor humano. ¿En qué puedo ayudarte? 😊"),
        ("¿Me van a aprobar el crédito?", "La aprobación está sujeta a evaluación crediticia. Trabajamos con **múltiples bancos**, lo que aumenta las posibilidades. El enganche mínimo es del 20%.\n\n¿Ya tienes un auto en mente?"),
    ]
    msg, resp = random.choice(opciones)
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": msg},
        {"role": "assistant", "content": resp},
    ]}


def _flujo_completo():
    v = random.choice(VEHICULOS_MUESTRA)
    precio = v["precio"]
    v_mcp_busq = vehiculo_a_mcp_busqueda(v)
    v_mcp_det = vehiculo_a_mcp_detalle(v)
    fin_mcp = calcular_financiamiento_mcp(precio, 20, 48)
    enganche = int(precio * 0.2)
    monto = precio - enganche
    tasa_m = 0.15 / 12
    mensualidad = int(monto * (tasa_m * (1 + tasa_m)**48) / ((1 + tasa_m)**48 - 1))
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT_SIMPLE},
        {"role": "user", "content": "Hola buenas tardes"},
        {"role": "assistant", "content": "¡Buenas tardes! 👋 Soy Mariana, del equipo de Autos TREFA. ¿Estás buscando un auto en especial?"},
        {"role": "user", "content": f"Me interesan los {v['marca']}"},
        {"role": "assistant", "content": tool_call("buscar_vehiculos", {"marca": v["marca"]})},
        {"role": "tool", "content": tool_response("buscar_vehiculos", {"vehiculos": [v_mcp_busq], "total": 1})},
        {"role": "assistant", "content": f"¡Tenemos opciones de {v['marca']}!\n\n{presentar_vehiculo(v)}\n\n¿Te gustaría más detalles?"},
        {"role": "user", "content": "Sí, cuéntame más"},
        {"role": "assistant", "content": tool_call("obtener_vehiculo", {"id": v["id"]})},
        {"role": "tool", "content": tool_response("obtener_vehiculo", v_mcp_det)},
        {"role": "assistant", "content": f"**{v['titulo']}**:\n📅 {v['autoano']} | ⚙️ {v['transmision']}\n🔧 Motor: {v_mcp_det['motor']}\n📏 {v['kilometraje']:,} km\n💰 **{fmt_precio(precio)}**\n\n¿Te gustaría que calcule las mensualidades? 😊"},
        {"role": "user", "content": "Sí, con 20% de enganche a 48 meses"},
        {"role": "assistant", "content": tool_call("calcular_financiamiento", {"precio_vehiculo": precio, "enganche_porcentaje": 20, "plazo_meses": 48})},
        {"role": "tool", "content": tool_response("calcular_financiamiento", fin_mcp)},
        {"role": "assistant", "content": f"💵 Enganche (20%): {fmt_precio(enganche)}\n📊 Monto a financiar: {fmt_precio(monto)}\n💳 **Mensualidad: {fmt_precio(mensualidad)}/mes**\n\n⚠️ Tasa referencia 15% anual, sujeta a aprobación.\n\n¿Te gustaría que un asesor te contacte?"},
    ]}


GENERADORES_PLANTILLA = [
    (_saludo_simple, 8),
    (_busqueda_marca, 10),
    (_busqueda_presupuesto, 8),
    (_busqueda_tipo, 6),
    (_auto_no_disponible, 8),
    (_detalle_vehiculo, 8),
    (_calcular_financiamiento, 8),
    (_pregunta_info_negocio, 20),
    (_comparar_vehiculos, 6),
    (_conversacion_simple, 18),
    (_flujo_completo, 10),
]


async def generate_template_dataset(
    job_manager,
    job_id: str,
    output_path: str,
    total_count: int | None = None,
) -> dict:
    """Genera dataset por plantillas (sin LLM)."""
    dataset = []
    stats = {}

    if total_count:
        total_weight = sum(w for _, w in GENERADORES_PLANTILLA)
        generators = [(g, max(1, int(w / total_weight * total_count))) for g, w in GENERADORES_PLANTILLA]
    else:
        generators = GENERADORES_PLANTILLA

    total_expected = sum(w for _, w in generators)
    generated = 0

    for gen_func, cantidad in generators:
        name = gen_func.__name__
        stats[name] = 0
        for _ in range(cantidad):
            if job_manager.is_cancelled(job_id):
                break
            try:
                conv = gen_func()
                dataset.append(conv)
                stats[name] += 1
                generated += 1
                if generated % 20 == 0:
                    pct = int(generated / total_expected * 100)
                    job_manager.update_progress(job_id, pct, f"{generated}/{total_expected}")
            except Exception:
                pass

    random.shuffle(dataset)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for conv in dataset:
            f.write(json.dumps(conv, ensure_ascii=False) + "\n")

    result = {"count": len(dataset), "output_path": output_path, "stats_by_scenario": stats}
    job_manager.complete_job(job_id, result)
    return result


# ============================================================
# 4.2 GENERACIÓN SINTÉTICA CON CLAUDE
# ============================================================

def _obtener_semilla(escenario_key: str) -> str:
    mapeo = {
        "busqueda_exitosa": ["busqueda_encontrado"],
        "sin_disponibilidad": ["sin_inventario"],
        "financiamiento": ["financiamiento"],
        "objecion_precio": ["objecion_negociacion"],
        "msi": ["financiamiento"],
        "cita_prueba": ["busqueda_encontrado", "sucursales"],
        "intercambio": ["intercambio"],
        "garantia_devolucion": ["busqueda_encontrado"],
        "indeciso": ["busqueda_encontrado", "objecion_negociacion"],
        "mal_credito": ["financiamiento"],
        "fuera_alcance": ["saludo_descubrimiento"],
        "saludo_inicio": ["saludo_descubrimiento"],
        "venta_auto": ["intercambio"],
        "contado": ["busqueda_encontrado"],
    }
    cats = mapeo.get(escenario_key, ["saludo_descubrimiento"])
    candidatas = [s for s in SEMILLAS_REALES if s["categoria"] in cats]
    if not candidatas:
        candidatas = SEMILLAS_REALES
    semilla = random.choice(candidatas)
    lines = []
    for m in semilla["mensajes"][:4]:
        prefix = "Cliente" if m["role"] == "user" else "Bot"
        lines.append(f"{prefix}: {m['content'][:200]}")
    return "\n".join(lines)


async def generate_synthetic_dataset(
    job_manager,
    job_id: str,
    output_path: str,
    anthropic_api_key: str,
    count: int = 100,
    model: str = "claude-haiku-4-5-20251001",
    scenario_filter: list[str] | None = None,
    pause_seconds: float = 0.3,
) -> dict:
    """Genera dataset sintético usando Claude."""
    from app.llm_clients import AnthropicClient, parsear_json_respuesta
    client = AnthropicClient(api_key=anthropic_api_key, model=model)

    escenarios_activos = {}
    if scenario_filter:
        for key in scenario_filter:
            if key in ESCENARIOS:
                escenarios_activos[key] = ESCENARIOS[key]
    if not escenarios_activos:
        escenarios_activos = ESCENARIOS

    keys = list(escenarios_activos.keys())
    por_escenario = max(1, count // len(keys))
    plan = {key: por_escenario for key in keys}
    extra = count - sum(plan.values())
    for key in ["busqueda_exitosa", "sin_disponibilidad", "financiamiento"]:
        if extra <= 0:
            break
        if key in plan:
            plan[key] += 1
            extra -= 1

    turnos = {
        "saludo_inicio": (2, 4), "msi": (2, 4), "fuera_alcance": (2, 4),
        "garantia_devolucion": (2, 5), "contado": (3, 5), "indeciso": (4, 8),
        "financiamiento": (3, 6), "busqueda_exitosa": (3, 7),
        "sin_disponibilidad": (3, 6), "objecion_precio": (3, 6),
        "intercambio": (3, 6), "venta_auto": (4, 8),
        "mal_credito": (3, 6), "cita_prueba": (3, 6),
    }

    resultados = []
    stats = {}
    total_planned = sum(plan.values())
    done = 0

    for escenario_key, cantidad in plan.items():
        contextos = escenarios_activos[escenario_key]["contextos"]
        stats[escenario_key] = 0

        for i in range(cantidad):
            if job_manager.is_cancelled(job_id):
                break

            contexto = contextos[i % len(contextos)]
            personalidad = random.choice(PERSONALIDADES)
            semilla = _obtener_semilla(escenario_key)
            min_t, max_t = turnos.get(escenario_key, (3, 6))

            prompt = META_PROMPT_SINTETICO.format(
                escenario_desc=escenarios_activos[escenario_key]["descripcion"],
                estrategia_venta=escenarios_activos[escenario_key]["estrategia_venta"],
                contexto=json.dumps(contexto, ensure_ascii=False),
                personalidad=personalidad,
                semilla=semilla,
                min_turnos=min_t,
                max_turnos=max_t,
            )

            texto = await client.generate(prompt, temperature=0.9, max_tokens=2000)
            datos = parsear_json_respuesta(texto)

            if datos and "mensajes" in datos:
                mensajes = datos["mensajes"]
                if len(mensajes) >= 2 and mensajes[0]["role"] == "user":
                    conv = {"messages": [
                        {"role": "system", "content": SYSTEM_PROMPT_TREFA_BOT},
                        *[{"role": m["role"], "content": m["content"]} for m in mensajes],
                    ]}
                    resultados.append(conv)
                    stats[escenario_key] += 1

            done += 1
            pct = int(done / total_planned * 100)
            job_manager.update_progress(job_id, pct, f"{done}/{total_planned} conversaciones")
            await asyncio.sleep(pause_seconds)

    random.shuffle(resultados)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for conv in resultados:
            f.write(json.dumps({"messages": conv["messages"]}, ensure_ascii=False) + "\n")

    prices = {"claude-haiku-4-5-20251001": (1.0, 5.0), "claude-sonnet-4-5-20250929": (3.0, 15.0)}
    p_in, p_out = prices.get(model, (1.0, 5.0))
    cost = (client.usage.tokens_input / 1_000_000 * p_in) + (client.usage.tokens_output / 1_000_000 * p_out)

    result = {
        "count": len(resultados),
        "output_path": output_path,
        "cost_usd": round(cost, 4),
        "stats_by_scenario": stats,
        "tokens": {"input": client.usage.tokens_input, "output": client.usage.tokens_output},
    }
    job_manager.complete_job(job_id, result)
    return result


# ============================================================
# 4.3 PIPELINE 3 ETAPAS CON TOOL CALLING (Gemini)
# ============================================================

def _formatear_conv_gold(conv: dict, max_msgs: int = 12) -> str:
    msgs = conv.get("messages", [])
    lineas = []
    for m in msgs[:max_msgs]:
        role = m["role"]
        content = m["content"]
        if role == "system":
            lineas.append('[SYSTEM]: (system prompt con tools — omitido)')
        elif role == "user":
            lineas.append(f'[USER]: {content}')
        elif role == "assistant":
            lineas.append(f'[ASSISTANT]: {content}')
        elif role == "tool":
            lineas.append(f'[TOOL]: {content}')
    return "\n".join(lineas)


def _validar_conv_tc(mensajes: list[dict]) -> bool:
    """Valida estructura y formato MCP de una conversación con tool calling."""
    if len(mensajes) < 3:
        return False
    if mensajes[0].get("role") != "system":
        return False
    roles = [m["role"] for m in mensajes]
    if "user" not in roles or "assistant" not in roles:
        return False

    for i, m in enumerate(mensajes):
        content = m.get("content", "")

        # tool_call debe ir seguido de role:tool
        if m["role"] == "assistant" and "<tool_call>" in content:
            if i + 1 >= len(mensajes) or mensajes[i + 1]["role"] != "tool":
                return False
            # tool_call no debe mezclar texto + llamada
            if not content.strip().startswith("<tool_call>"):
                return False

        # tool_response debe tener role:tool (no role:user)
        if "<tool_response>" in content and m["role"] != "tool":
            return False

        # Validar formato MCP de tool responses
        if m["role"] == "tool" and "<tool_response>" in content:
            if not _validar_tool_response_mcp(content):
                return False

    return True


# Root keys de herramientas que retornan vehículos
_VEHICLE_ROOT_KEYS = {"vehiculos", "alternativas"}

# Campos mínimos que un vehículo MCP debe tener
_VEHICLE_REQUIRED_FIELDS = {"id", "titulo", "marca", "precio", "kilometraje", "ubicacion"}

# Root keys de herramientas de conocimiento (no vehículos)
_KNOWLEDGE_ROOT_KEYS = {
    "informacion": {"id", "titulo", "contenido"},
    "resultados": {"id", "titulo", "contenido"},
    "faqs": {"id", "pregunta", "respuesta"},
}


def _validar_tool_response_mcp(content: str) -> bool:
    """Valida que un tool_response tenga formato MCP correcto."""
    match = re.search(r"<tool_response>\s*(.*?)\s*</tool_response>", content, re.DOTALL)
    if not match:
        return False
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return False

    # Si tiene wrapper {name, content}, extraer inner
    inner = data.get("content", data) if isinstance(data, dict) else data
    if not isinstance(inner, dict):
        return False

    # Validar herramientas que retornan vehículos
    for root_key in _VEHICLE_ROOT_KEYS:
        if root_key in inner:
            vehicles = inner[root_key]
            if isinstance(vehicles, list) and len(vehicles) > 0:
                v = vehicles[0]
                if isinstance(v, dict) and "marca" in v:
                    # Validar campos mínimos de vehículo
                    if not _VEHICLE_REQUIRED_FIELDS.issubset(v.keys()):
                        return False
                    # precio debe ser string formateado (no número)
                    if isinstance(v.get("precio"), (int, float)):
                        return False
                    # kilometraje debe ser string formateado (no número)
                    if isinstance(v.get("kilometraje"), (int, float)):
                        return False
            break

    # Validar herramientas de conocimiento
    for root_key, required_fields in _KNOWLEDGE_ROOT_KEYS.items():
        if root_key in inner:
            items = inner[root_key]
            if isinstance(items, list) and len(items) > 0:
                item = items[0]
                if isinstance(item, dict) and not required_fields.issubset(item.keys()):
                    return False
            break

    # Validar calcular_financiamiento
    if "mensualidad_estimada" in inner:
        # Valores monetarios deben ser strings
        for field in ("precio_vehiculo", "enganche", "monto_a_financiar", "mensualidad_estimada", "total_a_pagar"):
            val = inner.get(field)
            if val is not None and isinstance(val, (int, float)):
                return False
        if "nota" not in inner:
            return False

    # Validar solicitar_datos_contacto / enviar_cotizacion_email
    if "datos_registrados" in inner or "enviado" in inner:
        if "mensaje" not in inner:
            return False

    return True


def _generar_plan_cobertura(cantidad: int) -> list[dict]:
    escenarios = list(ESCENARIOS_TC.keys())
    plan = []
    while len(plan) < cantidad:
        random.shuffle(escenarios)
        for key in escenarios:
            if len(plan) >= cantidad:
                break
            esc = ESCENARIOS_TC[key]
            ctx = random.choice(esc["contextos"])
            plan.append({"escenario": key, "contexto": ctx})
    return plan[:cantidad]


def _cargar_gold_jsonl(ruta: str) -> list[dict]:
    convs = []
    if not os.path.exists(ruta):
        return convs
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea:
                try:
                    convs.append(json.loads(linea))
                except json.JSONDecodeError:
                    pass
    return convs


async def run_toolcalling_pipeline(
    job_manager,
    job_id: str,
    gemini_api_key: str,
    working_dir: str,
    stages: str = "todas",
    gold_files: list[str] | None = None,
    count_stage1: int = 40,
    count_stage2: int = 400,
    threshold: float = 7.0,
    pause_seconds: float = 2.0,
) -> dict:
    """Pipeline de 3 etapas para generación TC con Gemini."""
    from app.llm_clients import GeminiClient, parsear_json_respuesta
    os.makedirs(working_dir, exist_ok=True)

    # Cargar gold
    gold_all = []
    if gold_files:
        for gf in gold_files:
            gold_all.extend(_cargar_gold_jsonl(gf))
    if not gold_all:
        for f in Path(working_dir).glob("*gold*.jsonl"):
            gold_all.extend(_cargar_gold_jsonl(str(f)))

    turnos_por_esc = {
        "flujo_completo_tc": (5, 10), "comparacion_tc": (4, 7),
        "cotizacion_email_tc": (4, 8), "auto_especifico_tc": (3, 6),
        "estadisticas_inventario_tc": (2, 5), "garantia_devolucion_tc": (2, 5),
    }

    result = {"stage1_count": 0, "stage2_count": 0, "final_count": 0, "output_path": "", "cost_usd": 0}
    convs_e1 = []
    convs_e2 = []

    # ETAPA 1
    if stages in ("1", "todas"):
        job_manager.update_progress(job_id, 5, "Etapa 1: generando calidad...")
        client_pro = GeminiClient(api_key=gemini_api_key, model="gemini-2.5-pro")
        plan = _generar_plan_cobertura(count_stage1)

        for i, item in enumerate(plan):
            if job_manager.is_cancelled(job_id):
                break
            key = item["escenario"]
            ctx = item["contexto"]
            esc = ESCENARIOS_TC[key]
            personalidad = random.choice(PERSONALIDADES_TC)
            min_t, max_t = turnos_por_esc.get(key, (3, 7))
            ejemplo = random.choice(gold_all) if gold_all else None
            ejemplo_texto = _formatear_conv_gold(ejemplo) if ejemplo else "(sin ejemplo)"

            prompt = META_PROMPT_E1_TC.format(
                escenario_desc=esc["descripcion"],
                estrategia_venta=esc["estrategia_venta"],
                tools_esperadas=", ".join(esc["tools_esperadas"]),
                contexto=json.dumps(ctx, ensure_ascii=False),
                personalidad=personalidad,
                system_prompt=SYSTEM_PROMPT_CON_TOOLS,
                ejemplo_gold=ejemplo_texto,
                min_turnos=min_t, max_turnos=max_t,
            )

            respuesta = await client_pro.generate(prompt, temperature=0.85)
            datos = parsear_json_respuesta(respuesta)
            if datos and "mensajes" in datos:
                mensajes = datos["mensajes"]
                if mensajes and mensajes[0].get("role") == "system":
                    mensajes[0]["content"] = SYSTEM_PROMPT_CON_TOOLS
                else:
                    mensajes.insert(0, {"role": "system", "content": SYSTEM_PROMPT_CON_TOOLS})
                if _validar_conv_tc(mensajes):
                    convs_e1.append({"messages": mensajes})

            pct = 5 + int((i + 1) / count_stage1 * 25)
            job_manager.update_progress(job_id, pct, f"E1: {i+1}/{count_stage1}")
            await asyncio.sleep(pause_seconds)

        result["stage1_count"] = len(convs_e1)

    # ETAPA 2
    if stages in ("2", "todas"):
        job_manager.update_progress(job_id, 35, "Etapa 2: generación masiva...")
        client_flash = GeminiClient(api_key=gemini_api_key, model="gemini-2.5-flash")
        convs_calidad = list(gold_all) + list(convs_e1)
        plan = _generar_plan_cobertura(count_stage2)

        for i, item in enumerate(plan):
            if job_manager.is_cancelled(job_id):
                break
            key = item["escenario"]
            ctx = item["contexto"]
            esc = ESCENARIOS_TC[key]
            personalidad = random.choice(PERSONALIDADES_TC)
            min_t, max_t = turnos_por_esc.get(key, (3, 7))
            semilla = random.choice(convs_calidad) if convs_calidad else None
            semilla_texto = _formatear_conv_gold(semilla) if semilla else "(sin semilla)"

            prompt = META_PROMPT_E2_TC.format(
                escenario_desc=esc["descripcion"],
                estrategia_venta=esc["estrategia_venta"],
                tools_esperadas=", ".join(esc["tools_esperadas"]),
                contexto=json.dumps(ctx, ensure_ascii=False),
                personalidad=personalidad,
                semilla=semilla_texto,
                pregunta_real="Hola, busco un auto",
                system_prompt=SYSTEM_PROMPT_CON_TOOLS,
                min_turnos=min_t, max_turnos=max_t,
            )

            respuesta = await client_flash.generate(prompt, temperature=0.9)
            datos = parsear_json_respuesta(respuesta)
            if datos and "mensajes" in datos:
                mensajes = datos["mensajes"]
                if mensajes and mensajes[0].get("role") == "system":
                    mensajes[0]["content"] = SYSTEM_PROMPT_CON_TOOLS
                else:
                    mensajes.insert(0, {"role": "system", "content": SYSTEM_PROMPT_CON_TOOLS})
                if _validar_conv_tc(mensajes):
                    convs_e2.append({"messages": mensajes})

            pct = 35 + int((i + 1) / count_stage2 * 40)
            job_manager.update_progress(job_id, pct, f"E2: {i+1}/{count_stage2}")
            await asyncio.sleep(max(pause_seconds * 0.75, 1.0))

        result["stage2_count"] = len(convs_e2)

    # ETAPA 3
    if stages in ("3", "todas"):
        job_manager.update_progress(job_id, 80, "Etapa 3: evaluación...")
        client_eval = GeminiClient(api_key=gemini_api_key, model="gemini-2.5-flash")
        todas = list(convs_e1) + list(convs_e2)
        aprobadas = []
        batch_size = 5

        refs_sample = random.sample(gold_all, min(3, len(gold_all))) if gold_all else []
        refs_texto = ""
        for j, ref in enumerate(refs_sample):
            refs_texto += f"\n--- Referencia {j+1} ---\n{_formatear_conv_gold(ref)}\n"

        for lote_idx in range(0, len(todas), batch_size):
            if job_manager.is_cancelled(job_id):
                break
            lote = todas[lote_idx:lote_idx + batch_size]
            convs_texto = ""
            for j, conv in enumerate(lote):
                convs_texto += f"\n--- Conversación {j} ---\n{_formatear_conv_gold(conv)}\n"

            prompt = PROMPT_EVALUAR_TC.format(
                ejemplos_referencia=refs_texto,
                conversaciones=convs_texto,
                umbral=threshold,
            )

            respuesta = await client_eval.generate(prompt, temperature=0.3)
            evaluaciones = parsear_json_respuesta(respuesta)

            if evaluaciones and isinstance(evaluaciones, list):
                for ev in evaluaciones:
                    idx = ev.get("indice", 0)
                    if idx < len(lote) and ev.get("veredicto") == "conservar" and ev.get("puntaje_total", 0) >= threshold:
                        aprobadas.append(lote[idx])
            else:
                aprobadas.extend(lote)

            pct = 80 + int((lote_idx + batch_size) / max(len(todas), 1) * 18)
            job_manager.update_progress(job_id, min(pct, 98), f"E3: evaluando...")
            await asyncio.sleep(max(pause_seconds * 0.5, 1.0))

        convs_final = list(gold_all) + aprobadas
    else:
        convs_final = list(gold_all) + list(convs_e1) + list(convs_e2)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(working_dir, f"dataset_final_tc_{len(convs_final)}_{ts}.jsonl")
    with open(output_path, "w", encoding="utf-8") as f:
        for conv in convs_final:
            f.write(json.dumps(conv, ensure_ascii=False) + "\n")

    result["final_count"] = len(convs_final)
    result["output_path"] = output_path
    job_manager.complete_job(job_id, result)
    return result


# ============================================================
# 4.4 CURACIÓN DE CALIDAD (Gemini)
# ============================================================

def _cargar_sinteticas(ruta: str) -> list[dict]:
    convs = []
    with open(ruta, "r", encoding="utf-8") as f:
        for i, linea in enumerate(f):
            linea = linea.strip()
            if not linea:
                continue
            try:
                conv = json.loads(linea)
                if "messages" in conv and len(conv["messages"]) >= 2:
                    conv["_indice_original"] = i
                    convs.append(conv)
            except json.JSONDecodeError:
                pass
    return convs


def _cargar_reales(ruta: str) -> list[dict]:
    """Carga conversaciones reales desde JSONL o CSV."""
    convs = _cargar_sinteticas(ruta)
    if convs:
        return convs

    conversaciones = []
    with open(ruta, "r", encoding="utf-8") as f:
        muestra = f.read(8192)
        f.seek(0)
        tabs = muestra.count("\t")
        comas = muestra.count(",")
        delimitador = "\t" if tabs > comas else ","

        primera_linea = muestra.split("\n")[0]
        campos = primera_linea.split(delimitador)
        col_json_idx = None
        for idx, campo in enumerate(campos):
            if campo.strip().startswith("[{"):
                col_json_idx = idx
                break

        if col_json_idx is not None:
            reader = csv.reader(f, delimiter=delimitador)
            for fila in reader:
                if len(fila) > col_json_idx:
                    try:
                        datos = json.loads(fila[col_json_idx])
                        if isinstance(datos, list) and len(datos) >= 2:
                            msgs = [m for m in datos if isinstance(m, dict) and "role" in m]
                            if len(msgs) >= 2:
                                conversaciones.append({"messages": msgs})
                    except (json.JSONDecodeError, TypeError):
                        pass
    return conversaciones


def _formatear_conv_para_eval(conv: dict, max_turnos: int = 0) -> str:
    lineas = []
    for m in conv.get("messages", []):
        if m["role"] == "system":
            continue
        prefijo = "👤 Cliente" if m["role"] == "user" else "🤖 TREFA"
        contenido = m["content"][:300]
        lineas.append(f"{prefijo}: {contenido}")
        if max_turnos and len(lineas) >= max_turnos:
            break
    return "\n".join(lineas)


async def curate_dataset(
    job_manager,
    job_id: str,
    synthetic_path: str,
    reference_path: str,
    output_train_path: str,
    output_eval_path: str,
    gemini_api_key: str,
    threshold: int = 6,
    batch_size: int = 5,
    eval_split: float = 0.1,
    only_evaluate: bool = False,
) -> dict:
    """Curación de calidad usando Gemini."""
    from app.llm_clients import GeminiClient, parsear_json_respuesta
    client = GeminiClient(api_key=gemini_api_key, model="gemini-2.5-pro")

    sinteticas = _cargar_sinteticas(synthetic_path)
    reales = _cargar_reales(reference_path)

    if not sinteticas:
        raise ValueError(f"No se encontraron conversaciones en {synthetic_path}")
    if not reales:
        raise ValueError(f"No se encontraron conversaciones reales en {reference_path}")

    job_manager.update_progress(job_id, 5, f"Cargadas {len(sinteticas)} sint., {len(reales)} reales")

    # Preparar ejemplos reales
    muestra = random.sample(reales, min(15, len(reales)))
    ejemplos_texto = ""
    for i, conv in enumerate(muestra):
        ejemplos_texto += f"### Conversación real {i+1}\n{_formatear_conv_para_eval(conv, 12)}\n\n"

    conservar = []
    mejorar_lista = []
    descartar = []

    # Fase 1: Evaluación
    for lote_idx in range(0, len(sinteticas), batch_size):
        if job_manager.is_cancelled(job_id):
            break
        lote = sinteticas[lote_idx:lote_idx + batch_size]
        convs_texto = ""
        for j, conv in enumerate(lote):
            convs_texto += f"\n--- Conversación {j} ---\n{_formatear_conv_para_eval(conv)}\n"

        prompt = PROMPT_EVALUAR_CURACION.format(
            ejemplos_reales=ejemplos_texto,
            conversaciones=convs_texto,
            umbral=threshold,
        )

        texto = await client.generate(prompt, temperature=0.2)
        evaluaciones = parsear_json_respuesta(texto)

        if evaluaciones and isinstance(evaluaciones, list):
            for j_ev, ev in enumerate(evaluaciones):
                idx = lote_idx + ev.get("indice", j_ev)
                if idx >= len(sinteticas):
                    idx = lote_idx + j_ev
                ev["_conv"] = sinteticas[idx] if idx < len(sinteticas) else lote[j_ev]
                veredicto = ev.get("veredicto", "conservar")
                puntaje = ev.get("puntaje_total", 5)
                if veredicto == "descartar" or puntaje < threshold:
                    descartar.append(ev)
                elif veredicto == "mejorar":
                    mejorar_lista.append(ev)
                else:
                    conservar.append(ev)
        else:
            for conv in lote:
                conservar.append({"_conv": conv, "puntaje_total": 7, "veredicto": "conservar"})

        pct = 5 + int((lote_idx + batch_size) / len(sinteticas) * 45)
        job_manager.update_progress(job_id, min(pct, 50), f"Evaluando {lote_idx+len(lote)}/{len(sinteticas)}")
        await asyncio.sleep(2)

    # Fase 2: Mejora
    mejoradas = []
    if mejorar_lista and not only_evaluate:
        for k, ev in enumerate(mejorar_lista):
            if job_manager.is_cancelled(job_id):
                break
            ejemplos = ""
            muestra_r = random.sample(reales, min(8, len(reales)))
            for i, conv in enumerate(muestra_r):
                ejemplos += f"### Real {i+1}\n{_formatear_conv_para_eval(conv, 8)}\n\n"

            prompt = PROMPT_MEJORAR.format(
                ejemplos_reales=ejemplos,
                problemas=", ".join(ev.get("problemas", [])) or "Ninguno crítico",
                sugerencias=", ".join(ev.get("sugerencias", [])) or "Mejorar naturalidad",
                conversacion=json.dumps(ev["_conv"], ensure_ascii=False, indent=2),
            )

            texto = await client.generate(prompt, temperature=0.5)
            mejorada = parsear_json_respuesta(texto)
            if mejorada and "messages" in mejorada and len(mejorada["messages"]) >= 3:
                mejoradas.append(mejorada)
            else:
                if ev.get("puntaje_total", 0) >= threshold:
                    conservar.append(ev)

            pct = 50 + int((k + 1) / len(mejorar_lista) * 30)
            job_manager.update_progress(job_id, min(pct, 80), f"Mejorando {k+1}/{len(mejorar_lista)}")
            await asyncio.sleep(1)
    elif mejorar_lista and only_evaluate:
        conservar.extend(mejorar_lista)

    # Fase 3: Ensamble
    job_manager.update_progress(job_id, 85, "Ensamblando dataset final...")
    dataset_final = []
    for ev in conservar:
        dataset_final.append({"messages": ev["_conv"]["messages"]})
    for conv in mejoradas:
        dataset_final.append({"messages": conv["messages"]})

    random.shuffle(dataset_final)
    n_eval = max(1, int(len(dataset_final) * eval_split))
    eval_set = dataset_final[:n_eval]
    train_set = dataset_final[n_eval:]

    os.makedirs(os.path.dirname(output_train_path) or ".", exist_ok=True)
    for datos, ruta in [(train_set, output_train_path), (eval_set, output_eval_path)]:
        with open(ruta, "w", encoding="utf-8") as f:
            for item in datos:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    cost = (client.usage.tokens_input / 1_000_000 * 1.25) + (client.usage.tokens_output / 1_000_000 * 10.0)
    result = {
        "conserved": len(conservar),
        "improved": len(mejoradas),
        "discarded": len(descartar),
        "train_count": len(train_set),
        "eval_count": len(eval_set),
        "train_path": output_train_path,
        "eval_path": output_eval_path,
        "cost_usd": round(cost, 4),
        "tokens": {"input": client.usage.tokens_input, "output": client.usage.tokens_output},
    }
    job_manager.complete_job(job_id, result)
    return result
