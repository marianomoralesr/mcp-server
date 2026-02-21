#!/usr/bin/env python3
"""
===============================================================
 Fine-tuning Qwen3-14B con Unsloth — vast.ai (48GB VRAM)
===============================================================

 Script standalone que auto-instala dependencias, descarga el
 modelo, entrena con QLoRA, exporta y testea.

 Modelo base:  Qwen3-14B (4-bit QLoRA)
 Dataset:      JSONL con campo "messages" (ChatML + tool_call)
 Modo:         Non-thinking (sin bloques <think>)
 GPU:          48GB VRAM (RTX A6000 / A40 / etc.)

 Uso:
   python3 train_qwen3_vast.py --help
   python3 train_qwen3_vast.py --dry-run
   python3 train_qwen3_vast.py --train-file datos.jsonl
   python3 train_qwen3_vast.py --epochs 5 --lr 1e-4 --lora-r 64
   python3 train_qwen3_vast.py --resume-from ./qwen3-lora/checkpoint-200
   python3 train_qwen3_vast.py --export-only

===============================================================
"""

# ─── Auto-instalacion de dependencias ─────────────────────────

def install_dependencies():
    """Instala paquetes necesarios si no estan disponibles."""
    import subprocess
    import sys

    packages = [
        ("unsloth", "unsloth"),
        ("torch", "torch"),
        ("transformers", "transformers"),
        ("trl", "trl"),
        ("datasets", "datasets"),
        ("tensorboard", "tensorboard"),
        ("accelerate", "accelerate"),
    ]

    missing = []
    for import_name, pip_name in packages:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return

    print(f"Instalando dependencias faltantes: {', '.join(missing)}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade"] + missing,
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        print("Fallo pip install estandar, intentando unsloth desde GitHub...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install",
             "unsloth[cu124-torch250] @ git+https://github.com/unslothai/unsloth.git"],
            stdout=subprocess.DEVNULL,
        )

install_dependencies()

# ─── Imports ───────────────────────────────────────────────────

import argparse
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

import torch
from transformers import TrainerCallback

# ─── Configuracion por defecto ─────────────────────────────────

MODEL_NAME      = "unsloth/Qwen3-14B"
MAX_SEQ_LENGTH  = 4096

TRAIN_FILE      = "train.jsonl"
EVAL_FILE       = "eval.jsonl"

LORA_R          = 32
LORA_ALPHA      = 64
LORA_DROPOUT    = 0
TARGET_MODULES  = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

EPOCHS          = 3
BATCH_SIZE      = 4
GRAD_ACCUM      = 4          # effective batch = 4 * 4 = 16
LEARNING_RATE   = 2e-4
LR_SCHEDULER    = "cosine"
WARMUP_RATIO    = 0.05       # 5%
WEIGHT_DECAY    = 0.01
MAX_GRAD_NORM   = 1.0
NEFTUNE_NOISE   = 5

OUTPUT_DIR      = "./qwen3-lora"
MERGED_DIR      = "./qwen3-merged"
GGUF_QUANTS     = ["q5_k_m", "q4_k_m"]


# ─── Argumentos CLI ────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Fine-tuning Qwen3-14B — vast.ai (48GB)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    g = p.add_argument_group("Modelo")
    g.add_argument("--model", default=MODEL_NAME, help="Modelo base de Unsloth/HF")
    g.add_argument("--max-seq-length", type=int, default=MAX_SEQ_LENGTH)

    g = p.add_argument_group("Dataset")
    g.add_argument("--train-file", default=TRAIN_FILE, help="JSONL de entrenamiento")
    g.add_argument("--eval-file", default=EVAL_FILE, help="JSONL de evaluacion (opcional)")

    g = p.add_argument_group("LoRA")
    g.add_argument("--lora-r", type=int, default=LORA_R, help="Rank")
    g.add_argument("--lora-alpha", type=int, default=LORA_ALPHA, help="Alpha (2x rank recomendado)")

    g = p.add_argument_group("Entrenamiento")
    g.add_argument("--epochs", type=int, default=EPOCHS)
    g.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    g.add_argument("--grad-accum", type=int, default=GRAD_ACCUM)
    g.add_argument("--lr", type=float, default=LEARNING_RATE)
    g.add_argument("--neftune", type=float, default=NEFTUNE_NOISE, help="NEFTune noise alpha")
    g.add_argument("--no-eval", action="store_true", help="Desactivar evaluacion")

    g = p.add_argument_group("Output")
    g.add_argument("--output-dir", default=OUTPUT_DIR, help="Directorio para LoRA adapters")
    g.add_argument("--merged-dir", default=MERGED_DIR, help="Directorio para modelo merged 16-bit")
    g.add_argument("--no-gguf", action="store_true", help="No exportar GGUFs")

    g = p.add_argument_group("Modos especiales")
    g.add_argument("--resume-from", default=None, help="Reanudar desde checkpoint")
    g.add_argument("--export-only", action="store_true", help="Solo exportar modelo existente")
    g.add_argument("--dry-run", action="store_true", help="Validar todo sin entrenar")

    return p.parse_args()


# ─── Verificaciones de entorno ──────────────────────────────────

def check_cuda():
    """Verifica que CUDA esta disponible y devuelve info de GPU."""
    if not torch.cuda.is_available():
        print("ERROR: CUDA no disponible. Se necesita una GPU NVIDIA.")
        print("  torch.cuda.is_available() = False")
        print("  Verifica drivers NVIDIA y version de PyTorch.")
        sys.exit(1)

    gpu_name = torch.cuda.get_device_name(0)
    vram_total = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"GPU detectada: {gpu_name}")
    print(f"VRAM total:    {vram_total:.1f} GB")
    return vram_total


def check_disk_space(path: str, required_gb: float = 50.0):
    """Verifica espacio en disco disponible."""
    usage = shutil.disk_usage(Path(path).resolve().anchor)
    free_gb = usage.free / 1e9
    print(f"Disco libre:   {free_gb:.1f} GB (necesario: ~{required_gb:.0f} GB)")
    if free_gb < required_gb:
        print(f"ADVERTENCIA: Espacio insuficiente. Libre: {free_gb:.1f} GB, recomendado: {required_gb:.0f} GB")
        print("  La exportacion GGUF puede fallar. Considera --no-gguf")
    return free_gb


def auto_adjust_batch_size(vram_gb: float, batch_size: int) -> int:
    """Reduce batch size automaticamente si la VRAM es menor a la esperada."""
    if vram_gb >= 44:
        return batch_size  # 48GB: sin cambios
    if vram_gb >= 22:
        adjusted = min(batch_size, 2)
        if adjusted != batch_size:
            print(f"  Auto-ajuste: batch_size {batch_size} -> {adjusted} (VRAM: {vram_gb:.0f} GB)")
        return adjusted
    # <22GB: minimo
    adjusted = 1
    if adjusted != batch_size:
        print(f"  Auto-ajuste: batch_size {batch_size} -> {adjusted} (VRAM: {vram_gb:.0f} GB)")
    return adjusted


# ─── Validacion de dataset ──────────────────────────────────────

def load_and_validate_jsonl(path: str, max_seq_length: int) -> list:
    """Carga JSONL, valida formato y reporta estadisticas detalladas."""
    filepath = Path(path)
    if not filepath.exists():
        print(f"ERROR: Archivo no encontrado: {path}")
        sys.exit(1)

    data = []
    errors = 0
    roles_count = {"system": 0, "user": 0, "assistant": 0, "tool": 0}
    tool_call_count = 0
    tool_result_count = 0
    msg_lengths = []

    with open(filepath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                assert "messages" in obj, "Falta campo 'messages'"
                msgs = obj["messages"]
                assert isinstance(msgs, list) and len(msgs) >= 2, "Menos de 2 mensajes"

                for m in msgs:
                    role = m.get("role", "unknown")
                    roles_count[role] = roles_count.get(role, 0) + 1
                    content = m.get("content", "")
                    if role == "assistant" and "<tool_call>" in str(content):
                        tool_call_count += 1
                    if role == "tool":
                        tool_result_count += 1

                # Estimar longitud en chars (aprox 4 chars/token)
                total_chars = sum(len(str(m.get("content", ""))) for m in msgs)
                msg_lengths.append(total_chars)
                data.append(obj)

            except Exception as e:
                print(f"  Linea {i}: {e}")
                errors += 1
                if errors > 20:
                    print("  Demasiados errores, abortando.")
                    sys.exit(1)

    if not data:
        print(f"ERROR: No se cargaron conversaciones de {path}")
        sys.exit(1)

    # Estadisticas
    total_msgs = sum(len(c["messages"]) for c in data)
    avg_msgs = total_msgs / len(data)
    avg_chars = sum(msg_lengths) / len(msg_lengths)
    max_chars = max(msg_lengths)
    approx_max_tokens = max_chars // 4

    long_seqs = sum(1 for c in msg_lengths if c // 4 > max_seq_length)

    print(f"  Archivo:           {path}")
    print(f"  Conversaciones:    {len(data)}")
    print(f"  Errores:           {errors}")
    print(f"  Total turnos:      {total_msgs}")
    print(f"  Promedio turnos:   {avg_msgs:.1f} por conversacion")
    print(f"  Roles:             {dict(roles_count)}")
    print(f"  Tool calls:        {tool_call_count}")
    print(f"  Tool results:      {tool_result_count}")
    print(f"  Largo promedio:    ~{avg_chars:.0f} chars (~{avg_chars/4:.0f} tokens)")
    print(f"  Largo maximo:      ~{max_chars} chars (~{approx_max_tokens} tokens)")
    if long_seqs > 0:
        print(f"  ADVERTENCIA: {long_seqs} secuencias exceden {max_seq_length} tokens (seran truncadas)")

    return data


def prepare_datasets(args):
    """Carga train/eval y convierte a HF Dataset."""
    from datasets import Dataset

    print(f"\nValidando datasets...")
    train_data = load_and_validate_jsonl(args.train_file, args.max_seq_length)

    eval_data = None
    if not args.no_eval and Path(args.eval_file).exists():
        print()
        eval_data = load_and_validate_jsonl(args.eval_file, args.max_seq_length)
    elif args.no_eval:
        print(f"  Eval: desactivado (--no-eval)")
    else:
        print(f"  Eval: archivo no encontrado ({args.eval_file}), entrenando sin eval")

    train_ds = Dataset.from_list(train_data)
    eval_ds = Dataset.from_list(eval_data) if eval_data else None

    return train_ds, eval_ds


# ─── Formateo para Qwen3 ────────────────────────────────────────

def create_formatting_function(tokenizer):
    """
    Aplica el chat template de Qwen3 con enable_thinking=False.
    Mapea role 'tool' al formato soportado por Qwen3.
    """
    def formatting_func(examples):
        texts = []
        for messages in examples["messages"]:
            # Qwen3 soporta role "tool" nativamente en su template
            text = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=False,
                enable_thinking=False,
            )
            texts.append(text)
        return {"text": texts}
    return formatting_func


# ─── Modelo y LoRA ──────────────────────────────────────────────

def load_model(args):
    """Carga modelo base con Unsloth y aplica LoRA."""
    from unsloth import FastLanguageModel

    print(f"\nCargando modelo: {args.model}")
    print(f"  Max seq length: {args.max_seq_length}")
    print(f"  4-bit QLoRA")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.model,
        max_seq_length=args.max_seq_length,
        load_in_4bit=True,
        load_in_8bit=False,
        full_finetuning=False,
    )

    print(f"\nAplicando LoRA (r={args.lora_r}, alpha={args.lora_alpha})")
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        target_modules=TARGET_MODULES,
        lora_alpha=args.lora_alpha,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
        use_rslora=False,
        loftq_config=None,
    )

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  Parametros entrenables: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

    return model, tokenizer


# ─── Callbacks ──────────────────────────────────────────────────

class NaNDetectionCallback(TrainerCallback):
    """Detecta NaN en loss y detiene el entrenamiento."""

    def __init__(self, patience: int = 3):
        self.nan_count = 0
        self.patience = patience

    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs is None:
            return
        loss = logs.get("loss")
        if loss is not None and (math.isnan(loss) or math.isinf(loss)):
            self.nan_count += 1
            print(f"\n  ADVERTENCIA: Loss NaN/Inf detectado (#{self.nan_count}/{self.patience})")
            if self.nan_count >= self.patience:
                print(f"  ABORTANDO: {self.patience} NaN consecutivos. Posibles causas:")
                print(f"    - Learning rate demasiado alto (actual: {args.learning_rate})")
                print(f"    - Dataset corrupto o secuencias muy largas")
                print(f"    - Gradients explotan: intenta reducir --lr o aumentar --grad-accum")
                control.should_training_stop = True
        else:
            self.nan_count = 0


# ─── Entrenamiento ──────────────────────────────────────────────

def train_model(model, tokenizer, train_ds, eval_ds, args):
    """Configura y ejecuta el entrenamiento con SFTTrainer."""
    from trl import SFTTrainer
    from transformers import TrainingArguments

    formatting_func = create_formatting_function(tokenizer)

    estimated_steps = (len(train_ds) * args.epochs) // (args.batch_size * args.grad_accum)
    save_steps = max(10, estimated_steps // 10)
    logging_steps = max(1, estimated_steps // 100)

    print(f"\nConfiguracion de entrenamiento:")
    print(f"  Epocas:              {args.epochs}")
    print(f"  Batch size:          {args.batch_size}")
    print(f"  Gradient accum:      {args.grad_accum}")
    print(f"  Effective batch:     {args.batch_size * args.grad_accum}")
    print(f"  Learning rate:       {args.lr}")
    print(f"  LR scheduler:       {LR_SCHEDULER}")
    print(f"  Warmup:             {WARMUP_RATIO*100:.0f}%")
    print(f"  NEFTune noise:       {args.neftune}")
    print(f"  Steps estimados:     ~{estimated_steps}")
    print(f"  Save cada:           {save_steps} steps")
    print(f"  Log cada:            {logging_steps} steps")
    print(f"  Evaluacion:          {'Si' if eval_ds else 'No'}")

    training_args = TrainingArguments(
        output_dir=args.output_dir,

        # Epocas
        num_train_epochs=args.epochs,

        # Batch
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,

        # Optimizer
        learning_rate=args.lr,
        lr_scheduler_type=LR_SCHEDULER,
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        max_grad_norm=MAX_GRAD_NORM,
        optim="adamw_8bit",

        # Precision
        fp16=False,
        bf16=True,

        # Evaluacion
        eval_strategy="steps" if eval_ds else "no",
        eval_steps=save_steps if eval_ds else None,

        # Logging y guardado
        logging_dir=os.path.join(args.output_dir, "logs"),
        logging_steps=logging_steps,
        report_to="tensorboard",
        save_strategy="steps",
        save_steps=save_steps,
        save_total_limit=3,

        # Best model
        load_best_model_at_end=True if eval_ds else False,
        metric_for_best_model="eval_loss" if eval_ds else None,

        # Misc
        seed=3407,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        args=training_args,
        formatting_func=formatting_func,
        max_seq_length=args.max_seq_length,
        packing=False,
        neftune_noise_alpha=args.neftune,
        callbacks=[NaNDetectionCallback(patience=3)],
    )

    # VRAM antes de iniciar
    if torch.cuda.is_available():
        mem = torch.cuda.memory_allocated() / 1e9
        mem_total = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"\n  VRAM antes de train: {mem:.1f} / {mem_total:.1f} GB")

    print(f"\nIniciando entrenamiento...")
    print(f"  TensorBoard: tensorboard --logdir {args.output_dir}/logs\n")
    start_time = time.time()

    if args.resume_from:
        print(f"  Reanudando desde: {args.resume_from}")
        trainer.train(resume_from_checkpoint=args.resume_from)
    else:
        trainer.train()

    elapsed = time.time() - start_time
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    print(f"\nEntrenamiento completado en {hours}h {minutes}m")

    # Guardar LoRA adapters
    print(f"\nGuardando LoRA adapters en {args.output_dir}")
    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)

    return model, tokenizer, elapsed


# ─── Exportar modelo ────────────────────────────────────────────

def export_model(model, tokenizer, args):
    """Exporta modelo merged 16-bit y GGUFs."""

    # Verificar espacio antes de exportar
    check_disk_space(".", required_gb=30.0)

    # 1. Modelo merged 16-bit
    print(f"\nExportando modelo merged 16-bit a {args.merged_dir}")
    model.save_pretrained_merged(
        args.merged_dir,
        tokenizer,
        save_method="merged_16bit",
    )

    # 2. GGUFs
    if not args.no_gguf:
        for quant in GGUF_QUANTS:
            print(f"\nExportando GGUF {quant}...")
            model.save_pretrained_gguf(
                f"qwen3-14b-{quant}",
                tokenizer,
                quantization_method=quant,
            )
    else:
        print("\n  Exportacion GGUF omitida (--no-gguf)")


# ─── Test de inferencia ─────────────────────────────────────────

def test_inference(model, tokenizer):
    """Prueba rapida de inferencia post-entrenamiento."""
    from unsloth import FastLanguageModel

    FastLanguageModel.for_inference(model)

    test_messages = [
        {"role": "system", "content": "Eres un asistente util."},
        {"role": "user", "content": "Hola, busco una SUV para mi familia con presupuesto de 400 mil pesos."},
    ]

    text = tokenizer.apply_chat_template(
        test_messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )

    inputs = tokenizer(text, return_tensors="pt").to("cuda")

    print(f"\nTest de inferencia:")
    print(f"  User: {test_messages[-1]['content']}")
    print(f"  Modelo: ", end="", flush=True)

    from transformers import TextStreamer
    streamer = TextStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)

    _ = model.generate(
        **inputs,
        max_new_tokens=512,
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        streamer=streamer,
    )
    print()


# ─── Main ───────────────────────────────────────────────────────

def main():
    args = parse_args()
    total_start = time.time()

    print("=" * 60)
    print("  Fine-tuning Qwen3-14B — vast.ai (48GB)")
    print("=" * 60)

    # ── Verificar entorno ──
    print("\n--- Verificacion de entorno ---")
    vram_gb = check_cuda()
    disk_gb = check_disk_space(".")
    args.batch_size = auto_adjust_batch_size(vram_gb, args.batch_size)

    # ── Dry-run: solo validar ──
    if args.dry_run:
        print("\n--- Modo dry-run: validando sin entrenar ---")
        print(f"\n--- Dataset ---")
        train_data = load_and_validate_jsonl(args.train_file, args.max_seq_length)
        if not args.no_eval and Path(args.eval_file).exists():
            print()
            eval_data = load_and_validate_jsonl(args.eval_file, args.max_seq_length)

        estimated_steps = (len(train_data) * args.epochs) // (args.batch_size * args.grad_accum)
        print(f"\n--- Resumen dry-run ---")
        print(f"  Modelo:            {args.model}")
        print(f"  Max seq length:    {args.max_seq_length}")
        print(f"  LoRA r/alpha:      {args.lora_r}/{args.lora_alpha}")
        print(f"  Epocas:            {args.epochs}")
        print(f"  Batch size:        {args.batch_size}")
        print(f"  Grad accum:        {args.grad_accum}")
        print(f"  Effective batch:   {args.batch_size * args.grad_accum}")
        print(f"  LR:                {args.lr}")
        print(f"  NEFTune:           {args.neftune}")
        print(f"  Steps estimados:   ~{estimated_steps}")
        print(f"  GPU:               {torch.cuda.get_device_name(0)}")
        print(f"  VRAM:              {vram_gb:.1f} GB")
        print(f"  Disco libre:       {disk_gb:.1f} GB")
        print(f"\n  Todo OK. Ejecuta sin --dry-run para entrenar.")
        return

    # ── Export-only ──
    if args.export_only:
        print(f"\nModo export-only: cargando modelo desde {args.output_dir}...")
        from unsloth import FastLanguageModel
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.output_dir,
            max_seq_length=args.max_seq_length,
            load_in_4bit=True,
        )
        export_model(model, tokenizer, args)
        print("\nExportacion completada.")
        return

    # ── Cargar modelo ──
    model, tokenizer = load_model(args)

    # ── Preparar datasets ──
    train_ds, eval_ds = prepare_datasets(args)

    # ── Entrenar ──
    model, tokenizer, train_elapsed = train_model(model, tokenizer, train_ds, eval_ds, args)

    # ── Test de inferencia ──
    test_inference(model, tokenizer)

    # ── Exportar ──
    export_model(model, tokenizer, args)

    # ── Resumen final ──
    total_elapsed = time.time() - total_start
    hours = int(total_elapsed // 3600)
    minutes = int((total_elapsed % 3600) // 60)

    print("\n" + "=" * 60)
    print("  COMPLETADO")
    print("=" * 60)
    print(f"\n  Tiempo total: {hours}h {minutes}m")
    print(f"\n  Archivos generados:")
    print(f"    LoRA adapters:     {args.output_dir}/")
    print(f"    Modelo merged:     {args.merged_dir}/")
    if not args.no_gguf:
        for q in GGUF_QUANTS:
            print(f"    GGUF {q}:       qwen3-14b-{q}/")
    print(f"    TensorBoard logs:  {args.output_dir}/logs/")
    print(f"\n  Para ver metricas:")
    print(f"    tensorboard --logdir {args.output_dir}/logs")
    print(f"\n  Para servir con vLLM:")
    print(f"    vllm serve {args.merged_dir} \\")
    print(f"      --enable-auto-tool-choice \\")
    print(f"      --tool-call-parser hermes \\")
    print(f"      --max-model-len {args.max_seq_length}")
    print()


if __name__ == "__main__":
    main()
