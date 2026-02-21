#!/usr/bin/env python3
"""
Sube el modelo 32B ya mergeado a HuggingFace.
Diseñado para correr en la máquina vast.ai donde ya existe el merge.

Uso:
    # Con el merge en la ruta por defecto (/app/modelos/qwen3-32b-merged)
    python3 upload_merged_32b.py

    # Con ruta personalizada
    python3 upload_merged_32b.py --model-dir /ruta/al/merge

    # Repo destino diferente
    python3 upload_merged_32b.py --repo mmoralesf/otro-repo

Requisitos:
    pip install huggingface_hub hf_transfer
    export HF_TOKEN=hf_xxx
"""

import argparse
import os
import sys

def main():
    parser = argparse.ArgumentParser(description="Sube modelo 32B mergeado a HuggingFace")
    parser.add_argument(
        "--model-dir",
        default="/app/modelos/qwen3-32b-merged",
        help="Directorio del modelo mergeado (default: /app/modelos/qwen3-32b-merged)",
    )
    parser.add_argument(
        "--repo",
        default="mmoralesf/qwen3-32B-mariana",
        help="Repo HF destino (default: mmoralesf/qwen3-32B-mariana)",
    )
    parser.add_argument(
        "--commit-message",
        default="Qwen3-32B + LoRA mariana — modelo mergeado listo para vLLM",
    )
    parser.add_argument("--private", action="store_true", help="Crear repo privado")
    args = parser.parse_args()

    # Validaciones
    if not os.environ.get("HF_TOKEN"):
        print("ERROR: HF_TOKEN no definido. Exporta tu token de HuggingFace.")
        sys.exit(1)

    if not os.path.isfile(os.path.join(args.model_dir, "config.json")):
        print(f"ERROR: No se encontró config.json en {args.model_dir}")
        print("Asegurate de que el modelo ya esté mergeado.")
        sys.exit(1)

    # Activar hf_transfer para uploads rápidos
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"

    from huggingface_hub import HfApi, create_repo

    api = HfApi()

    # Crear repo si no existe
    print(f"[upload] Repo destino: {args.repo}")
    try:
        create_repo(args.repo, repo_type="model", private=args.private, exist_ok=True)
        print(f"[upload] Repo OK")
    except Exception as e:
        print(f"[upload] WARN al crear repo: {e}")

    # Contar archivos para referencia
    files = [f for f in os.listdir(args.model_dir) if os.path.isfile(os.path.join(args.model_dir, f))]
    total_size = sum(os.path.getsize(os.path.join(args.model_dir, f)) for f in files)
    print(f"[upload] Archivos: {len(files)} ({total_size / 1e9:.1f} GB)")
    print(f"[upload] Subiendo a {args.repo}...")

    api.upload_folder(
        folder_path=args.model_dir,
        repo_id=args.repo,
        repo_type="model",
        commit_message=args.commit_message,
    )

    print(f"[upload] Listo! Modelo en: https://huggingface.co/{args.repo}")


if __name__ == "__main__":
    main()
