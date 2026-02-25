import torch, os
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
BASE = "/app/modelos/qwen3-32b-base"
LORA = "/app/modelos/qwen3-32b-mariana"
OUT = "/app/modelos/qwen3-32b-merged"
print("[merge] Cargando tokenizer (del LoRA adapter)...")
tok = AutoTokenizer.from_pretrained(LORA, trust_remote_code=True, use_fast=True)
print("[merge] Cargando modelo base (~64GB RAM)...")
base = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16, device_map="cpu", trust_remote_code=True)
print("[merge] Cargando LoRA adapter...")
model = PeftModel.from_pretrained(base, LORA)
print("[merge] Mergeando...")
model = model.merge_and_unload()
print("[merge] Guardando en", OUT)
os.makedirs(OUT, exist_ok=True)
model.save_pretrained(OUT, safe_serialization=True)
tok.save_pretrained(OUT)
print("[merge] Listo!")
