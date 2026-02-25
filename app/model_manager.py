"""
ModelManager ligero — almacena estado del modelo activo.
El modelo real corre en vLLM; esta clase es un wrapper de metadata.
"""

from typing import List, Optional


class ModelManager:
    def __init__(self, settings=None):
        self.settings = settings
        self.active_model: str = settings.default_model if settings else "qwen3-14b-trefa"
        self.loaded_loras: List[str] = [settings.default_lora] if settings else ["trefa-lora"]

    @property
    def is_ready(self) -> bool:
        return True

    def get_active_model(self) -> str:
        return self.active_model

    def get_loaded_loras(self) -> List[str]:
        return self.loaded_loras

    def add_lora(self, lora_name: str):
        if lora_name not in self.loaded_loras:
            self.loaded_loras.append(lora_name)

    def remove_lora(self, lora_name: str):
        if lora_name in self.loaded_loras:
            self.loaded_loras.remove(lora_name)
