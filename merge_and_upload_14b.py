#!/usr/bin/env python3
"""
Descarga Qwen3-14B base + LoRA mariana, mergea y sube a HuggingFace.

Uso:
    # Todo por defecto (descarga, mergea, sube)
    python3 merge_and_upload_14b.py

    # Solo merge (si ya tienes base y LoRA descargados)
    python3 merge_and_upload_14b.py --skip-download

    # Solo upload (si ya tienes el merge)
    python3 merge_and_upload_14b.py --skip-download --skip-merge

    # Rutas personalizadas
    python3 merge_and_upload_14b.py --base-dir ./qwen3-14b --lora-dir ./lora --out-dir ./merged

Requisitos:
    pip install torch transformers peft accelerate huggingface_hub hf_transfer
    export HF_TOKEN=hf_xxx

Recursos:
    ~28GB RAM para el merge (bf16 en CPU)
    ~28GB disco para base + ~1GB LoRA + ~28GB merge = ~57GB total
"""

import argparse
import os
import sys


def download_models(base_repo, lora_repo, base_dir, lora_dir):
    """Descarga base y LoRA desde HuggingFace."""
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    from huggingface_hub import snapshot_download

    if not os.path.isfile(os.path.join(base_dir, "config.json")):
        print(f"[download] Descargando base: {base_repo} (~28GB)...")
        snapshot_download(
            base_repo,
            local_dir=base_dir,
            local_dir_use_symlinks=False,
        )
        print(f"[download] Base OK")
    else:
        print(f"[download] Base ya existe en {base_dir}, saltando")

    if not os.path.isfile(os.path.join(lora_dir, "adapter_config.json")):
        print(f"[download] Descargando LoRA: {lora_repo}...")
        snapshot_download(
            lora_repo,
            local_dir=lora_dir,
            local_dir_use_symlinks=False,
        )
        print(f"[download] LoRA OK")
    else:
        print(f"[download] LoRA ya existe en {lora_dir}, saltando")


def merge_model(base_dir, lora_dir, out_dir):
    """Mergea base + LoRA y guarda en out_dir."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    print(f"[merge] Cargando tokenizer desde LoRA ({lora_dir})...")
    tok = AutoTokenizer.from_pretrained(lora_dir, trust_remote_code=True, use_fast=True)

    print(f"[merge] Cargando base Qwen3-14B (bf16, CPU, ~28GB RAM)...")
    base = AutoModelForCausalLM.from_pretrained(
        base_dir,
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        trust_remote_code=True,
    )

    print(f"[merge] Cargando LoRA adapter...")
    model = PeftModel.from_pretrained(base, lora_dir)

    print(f"[merge] Mergeando...")
    model = model.merge_and_unload()

    print(f"[merge] Guardando en {out_dir}...")
    os.makedirs(out_dir, exist_ok=True)
    model.save_pretrained(out_dir, safe_serialization=True)
    tok.save_pretrained(out_dir)

    print(f"[merge] OK")


def upload_model(out_dir, repo, commit_message, private):
    """Sube el modelo mergeado a HuggingFace."""
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    from huggingface_hub import HfApi, create_repo

    api = HfApi()

    print(f"[upload] Repo destino: {repo}")
    try:
        create_repo(repo, repo_type="model", private=private, exist_ok=True)
    except Exception as e:
        print(f"[upload] WARN al crear repo: {e}")

    files = [f for f in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, f))]
    total_size = sum(os.path.getsize(os.path.join(out_dir, f)) for f in files)
    print(f"[upload] Archivos: {len(files)} ({total_size / 1e9:.1f} GB)")
    print(f"[upload] Subiendo a {repo}...")

    api.upload_folder(
        folder_path=out_dir,
        repo_id=repo,
        repo_type="model",
        commit_message=commit_message,
    )

    print(f"[upload] Listo! Modelo en: https://huggingface.co/{repo}")


def main():
    parser = argparse.ArgumentParser(
        description="Descarga Qwen3-14B + LoRA mariana, mergea y sube a HuggingFace"
    )
    parser.add_argument("--base-repo", default="Qwen/Qwen3-14B")
    parser.add_argument("--lora-repo", default="mmoralesf/qwen3-14B-v10-mariana")
    parser.add_argument("--base-dir", default="/app/modelos/qwen3-14b-base")
    parser.add_argument("--lora-dir", default="/app/modelos/qwen3-14b-v10-mariana")
    parser.add_argument("--out-dir", default="/app/modelos/qwen3-14b-v10-merged")
    parser.add_argument(
        "--upload-repo",
        default="mmoralesf/qwen3-14B-v10-mariana-merged",
        help="Repo HF destino para el modelo mergeado",
    )
    parser.add_argument(
        "--commit-message",
        default="Qwen3-14B + LoRA mariana v10 — modelo mergeado listo para vLLM",
    )
    parser.add_argument("--private", action="store_true", help="Crear repo privado")
    parser.add_argument("--skip-download", action="store_true", help="Saltar descarga (usa dirs locales)")
    parser.add_argument("--skip-merge", action="store_true", help="Saltar merge (solo upload)")
    parser.add_argument("--skip-upload", action="store_true", help="Saltar upload (solo merge local)")
    parser.add_argument("--cleanup-base", action="store_true", help="Borrar base después del merge para liberar disco")
    args = parser.parse_args()

    # Validaciones
    if not args.skip_upload and not os.environ.get("HF_TOKEN"):
        print("ERROR: HF_TOKEN no definido. Exporta tu token de HuggingFace.")
        sys.exit(1)

    # 1. Descargar
    if not args.skip_download:
        download_models(args.base_repo, args.lora_repo, args.base_dir, args.lora_dir)

    # 2. Merge
    if not args.skip_merge:
        if not os.path.isfile(os.path.join(args.base_dir, "config.json")):
            print(f"ERROR: Base no encontrada en {args.base_dir}")
            sys.exit(1)
        if not os.path.isfile(os.path.join(args.lora_dir, "adapter_config.json")):
            print(f"ERROR: LoRA no encontrado en {args.lora_dir}")
            sys.exit(1)

        merge_model(args.base_dir, args.lora_dir, args.out_dir)

        # Limpiar base para liberar disco (~28GB)
        if args.cleanup_base:
            import shutil
            print(f"[cleanup] Borrando base ({args.base_dir}) para liberar disco...")
            shutil.rmtree(args.base_dir, ignore_errors=True)

    # 3. Upload
    if not args.skip_upload:
        if not os.path.isfile(os.path.join(args.out_dir, "config.json")):
            print(f"ERROR: Modelo mergeado no encontrado en {args.out_dir}")
            sys.exit(1)

        upload_model(args.out_dir, args.upload_repo, args.commit_message, args.private)


if __name__ == "__main__":
    main()
