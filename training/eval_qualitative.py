#!/usr/bin/env python3
"""
Evaluación cualitativa de Mariana — prompts reales de TREFA.

Carga un checkpoint LoRA (o modelo mergeado) y ejecuta prompts
típicos de WhatsApp para evaluar calidad de respuestas.

Uso:
  # Desde un checkpoint durante training:
  python3 eval_qualitative.py --checkpoint /app/modelos/qwen3-14b-v10-mariana-unsloth/checkpoint-265

  # Desde el directorio de LoRA final:
  python3 eval_qualitative.py --checkpoint /app/modelos/qwen3-14b-v10-mariana-unsloth

  # Desde un modelo ya mergeado:
  python3 eval_qualitative.py --merged /app/modelos/qwen3-14b-v10-merged-unsloth

  # Modelo base sin fine-tune (para comparar):
  python3 eval_qualitative.py --base-only
"""

import argparse
import json
import time
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

# ─── System prompt de Mariana ─────────────────────────────────

SYSTEM_PROMPT = """Eres Mariana, asesora virtual de Autos TREFA, agencia de autos seminuevos con sucursales en Monterrey, Guadalupe, Saltillo y Reynosa, Mexico. Tu canal es WhatsApp.

PERSONALIDAD:
- Calida, profesional, empatica. Hablas como persona real: "Con mucho gusto te apoyo", "Que gusto saludarte".
- Espanol mexicano coloquial (tuteo). Maximo 1-2 emojis por mensaje.
- Respuestas de 1-3 parrafos cortos. SIEMPRE cierra con pregunta o siguiente paso claro.
- Nunca llames a TREFA "lote" o "tienda" — siempre "agencia de autos seminuevos".

REGLAS:
1. SIEMPRE usa herramientas para consultar datos reales. NUNCA inventes precios, disponibilidad ni especificaciones.
2. Si NO hay el auto que busca, usa buscar_alternativas AUTOMATICAMENTE antes de decir "no tenemos".
3. Incluye info financiera al presentar vehiculos: precio, enganche minimo, mensualidad.
4. Solicita el nombre del cliente de forma natural si no lo conoces.
5. Nunca compartas IDs internos, slugs tecnicos ni nombres de herramientas al cliente.
6. Formato precios: $XXX,XXX MXN.
7. NO negocies precios (son finales). NO garantices credito ("sujeto a aprobacion"). NO inventes promociones.
8. Si preguntan si eres IA, se honesta: "Soy Mariana, asistente virtual de Autos TREFA".

OBJETIVO COMERCIAL:
Lleva cada conversacion hacia: 1) Iniciar tramite de credito en linea, o 2) Agendar cita en sucursal.

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{"type": "function", "function": {"name": "buscar_vehiculos", "description": "Busca vehiculos en el inventario de Autos TREFA.", "parameters": {"type": "object", "properties": {"marca": {"type": "string"}, "modelo": {"type": "string"}, "precio_maximo": {"type": "number"}, "tipo_carroceria": {"type": "string"}}}}}
{"type": "function", "function": {"name": "obtener_vehiculo", "description": "Obtiene informacion detallada de un vehiculo especifico.", "parameters": {"type": "object", "properties": {"id": {"type": "number"}}}}}
{"type": "function", "function": {"name": "calcular_financiamiento", "description": "Calcula mensualidades estimadas.", "parameters": {"type": "object", "properties": {"precio_vehiculo": {"type": "number"}, "enganche_porcentaje": {"type": "number"}, "plazo_meses": {"type": "number"}}}}}
{"type": "function", "function": {"name": "buscar_alternativas", "description": "Busca vehiculos alternativos.", "parameters": {"type": "object", "properties": {"marca_original": {"type": "string"}, "presupuesto": {"type": "number"}}}}}
{"type": "function", "function": {"name": "obtener_info_negocio", "description": "Obtiene info del negocio: horarios, ubicaciones, garantias, etc.", "parameters": {"type": "object", "properties": {"tema": {"type": "string", "enum": ["horarios", "ubicaciones", "garantias", "financiamiento", "documentos_requeridos"]}}}}}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>"""

# ─── Prompts de evaluación ────────────────────────────────────

EVAL_PROMPTS = [
    {
        "name": "1. Saludo inicial",
        "messages": [
            {"role": "user", "content": "Hola buenas tardes"},
        ],
        "eval_criteria": "Debe saludar cálidamente, presentarse como Mariana de TREFA, preguntar en qué puede ayudar. NO debe inventar autos.",
    },
    {
        "name": "2. Búsqueda de SUV familiar",
        "messages": [
            {"role": "user", "content": "Busco una SUV para mi familia, tengo como 400 mil de presupuesto"},
        ],
        "eval_criteria": "Debe usar tool_call a buscar_vehiculos con tipo_carroceria=SUV y precio_maximo=400000. NO debe inventar resultados.",
    },
    {
        "name": "3. Pregunta de horarios",
        "messages": [
            {"role": "user", "content": "Qué horario tienen? Puedo ir el domingo?"},
        ],
        "eval_criteria": "Debe usar tool_call a obtener_info_negocio con tema=horarios. NO inventar horarios.",
    },
    {
        "name": "4. Cálculo de financiamiento",
        "messages": [
            {"role": "user", "content": "Cuanto me queda de mensualidad si doy 80 mil de enganche en un carro de 350 mil?"},
        ],
        "eval_criteria": "Debe usar tool_call a calcular_financiamiento. NO inventar cifras.",
    },
    {
        "name": "5. Objeción de precio",
        "messages": [
            {"role": "user", "content": "Está muy caro, en otro lado lo vi más barato. No me pueden hacer un descuento?"},
        ],
        "eval_criteria": "NO debe negociar precio (son finales). Debe destacar valor: garantía 12 meses, inspección 150 puntos, financiamiento.",
    },
    {
        "name": "6. Pregunta si es IA",
        "messages": [
            {"role": "user", "content": "Oye eres un robot o una persona real?"},
        ],
        "eval_criteria": "Debe ser honesta: 'Soy Mariana, asistente virtual de Autos TREFA'. NO fingir ser humana.",
    },
    {
        "name": "7. Auto específico no disponible",
        "messages": [
            {"role": "user", "content": "Tienen un Tesla Model 3?"},
        ],
        "eval_criteria": "Debe usar tool_call a buscar_vehiculos primero. Si no hay, debe usar buscar_alternativas. NO decir 'no tenemos' sin buscar.",
    },
    {
        "name": "8. Cliente da su nombre",
        "messages": [
            {"role": "user", "content": "Hola me llamo Roberto, ando buscando un sedán económico"},
        ],
        "eval_criteria": "Debe usar el nombre del cliente naturalmente. Debe buscar sedanes. NO inventar inventario.",
    },
    {
        "name": "9. Pregunta de garantía",
        "messages": [
            {"role": "user", "content": "Qué pasa si el carro sale con fallas después de comprarlo?"},
        ],
        "eval_criteria": "Debe mencionar garantía 12 meses motor/transmisión hasta $100,000 MXN. Devolución 7 días. Inspección 150 puntos.",
    },
    {
        "name": "10. Intento de cierre",
        "messages": [
            {"role": "user", "content": "Me interesa mucho esa Mazda CX-5, cómo le hago para apartarla?"},
        ],
        "eval_criteria": "Debe guiar hacia cierre: agendar cita en sucursal O iniciar trámite de crédito en línea. Pedir datos de contacto.",
    },
]


def load_model_checkpoint(base_model, checkpoint_path):
    """Carga modelo base + LoRA checkpoint."""
    from peft import PeftModel

    print(f"\nCargando tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(
        checkpoint_path, trust_remote_code=True, use_fast=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Cargando modelo base: {base_model}...")
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )

    print(f"Cargando LoRA adapter: {checkpoint_path}...")
    model = PeftModel.from_pretrained(model, checkpoint_path)
    model = model.merge_and_unload()
    model.eval()

    return model, tokenizer


def load_model_merged(merged_path):
    """Carga modelo ya mergeado."""
    print(f"\nCargando modelo mergeado: {merged_path}...")
    tokenizer = AutoTokenizer.from_pretrained(
        merged_path, trust_remote_code=True, use_fast=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        merged_path,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()

    return model, tokenizer


def load_model_base(base_model):
    """Carga modelo base sin fine-tune (para comparar)."""
    print(f"\nCargando modelo base (sin fine-tune): {base_model}...")
    tokenizer = AutoTokenizer.from_pretrained(
        base_model, trust_remote_code=True, use_fast=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()

    return model, tokenizer


def generate_response(model, tokenizer, messages, max_tokens=512):
    """Genera respuesta del modelo."""
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    text = tokenizer.apply_chat_template(
        full_messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )

    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            temperature=0.7,
            top_p=0.8,
            top_k=20,
            do_sample=True,
            repetition_penalty=1.05,
        )

    response = tokenizer.decode(
        outputs[0][inputs["input_ids"].shape[1]:],
        skip_special_tokens=True,
    )
    return response


def run_evaluation(model, tokenizer, prompts=None):
    """Ejecuta evaluación con todos los prompts."""
    if prompts is None:
        prompts = EVAL_PROMPTS

    results = []

    print("\n" + "=" * 70)
    print("  EVALUACIÓN CUALITATIVA — Mariana (TREFA)")
    print("=" * 70)

    for i, prompt in enumerate(prompts):
        print(f"\n{'─' * 70}")
        print(f"  {prompt['name']}")
        print(f"{'─' * 70}")
        print(f"  User: {prompt['messages'][-1]['content']}")
        print(f"  Criterio: {prompt['eval_criteria']}")
        print()

        start = time.time()
        response = generate_response(model, tokenizer, prompt["messages"])
        elapsed = time.time() - start

        print(f"  Mariana ({elapsed:.1f}s):")
        print()
        for line in response.strip().split("\n"):
            print(f"    {line}")
        print()

        # Análisis automático básico
        has_tool_call = "<tool_call>" in response
        has_invention = any(w in response.lower() for w in ["$299,000", "$350,000", "$250,000", "disponible en", "contamos con"])
        is_short = len(response) < 50
        is_too_long = len(response) > 2000

        flags = []
        if "buscar" in prompt["name"].lower() or "horario" in prompt["name"].lower() or "financiamiento" in prompt["name"].lower() or "específico" in prompt["name"].lower():
            if has_tool_call:
                flags.append("OK: usa tool_call")
            else:
                flags.append("WARN: NO usa tool_call (debería)")

        if is_short:
            flags.append("WARN: respuesta muy corta")
        if is_too_long:
            flags.append("WARN: respuesta muy larga")

        if flags:
            print(f"  Flags: {', '.join(flags)}")

        results.append({
            "prompt": prompt["name"],
            "response": response.strip(),
            "elapsed": elapsed,
            "has_tool_call": has_tool_call,
            "length": len(response),
        })

    # Resumen
    print(f"\n{'=' * 70}")
    print(f"  RESUMEN")
    print(f"{'=' * 70}")
    tool_calls = sum(1 for r in results if r["has_tool_call"])
    avg_time = sum(r["elapsed"] for r in results) / len(results)
    avg_len = sum(r["length"] for r in results) / len(results)

    print(f"  Prompts evaluados:  {len(results)}")
    print(f"  Con tool_call:      {tool_calls}/{len(results)}")
    print(f"  Tiempo promedio:    {avg_time:.1f}s")
    print(f"  Largo promedio:     {avg_len:.0f} chars")
    print()

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluación cualitativa de Mariana — prompts reales TREFA",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--checkpoint", help="Path a checkpoint LoRA (ej: .../checkpoint-265)")
    group.add_argument("--merged", help="Path a modelo mergeado")
    group.add_argument("--base-only", action="store_true", help="Evaluar modelo base sin fine-tune")

    parser.add_argument("--base-model", default="Qwen/Qwen3-14B", help="Modelo base HF")
    parser.add_argument("--save-json", default=None, help="Guardar resultados en JSON")

    args = parser.parse_args()

    # Cargar modelo
    if args.checkpoint:
        model, tokenizer = load_model_checkpoint(args.base_model, args.checkpoint)
        source = f"checkpoint: {args.checkpoint}"
    elif args.merged:
        model, tokenizer = load_model_merged(args.merged)
        source = f"merged: {args.merged}"
    else:
        model, tokenizer = load_model_base(args.base_model)
        source = f"base (sin fine-tune): {args.base_model}"

    vram = torch.cuda.memory_allocated() / 1e9
    print(f"\nModelo cargado: {source}")
    print(f"VRAM usado: {vram:.1f} GB")

    # Evaluar
    results = run_evaluation(model, tokenizer)

    # Guardar JSON si se pidió
    if args.save_json:
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump({"source": source, "results": results}, f, ensure_ascii=False, indent=2)
        print(f"Resultados guardados en {args.save_json}")


if __name__ == "__main__":
    main()
