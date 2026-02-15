"""
Configuración centralizada para TREFA Inference Server.
Usa pydantic-settings con prefijo TREFA_ para variables de entorno.
"""

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # vLLM backend (interno)
    vllm_host: str = Field(default="localhost", description="Host del servidor vLLM")
    vllm_port: int = Field(default=8000, description="Puerto del servidor vLLM")

    # FastAPI (público)
    fastapi_port: int = Field(default=8080, description="Puerto de la API FastAPI")

    # Autenticación
    api_key: Optional[str] = Field(default=None, description="API key para autenticación")

    # MCP Server
    mcp_server_url: str = Field(default="http://localhost:3001", description="URL del MCP Server")
    mcp_api_key: Optional[str] = Field(default=None, description="API key del MCP Server")

    # Modelo
    default_model: str = Field(default="qwen3-14b-trefa", description="Modelo por defecto")
    default_lora: str = Field(default="trefa-lora", description="LoRA adapter por defecto")

    # Orquestación
    max_tool_iterations: int = Field(default=5, description="Máximo de ciclos tool calling")

    # Logging
    log_level: str = Field(default="INFO", description="Nivel de logging")

    # Datasets
    dataset_dirs: str = Field(
        default="/app/datasets",
        description="Directorios a escanear para datasets JSONL (separados por coma)"
    )
    supabase_url: Optional[str] = Field(default=None, description="URL del proyecto Supabase")
    supabase_key: Optional[str] = Field(default=None, description="Service role key de Supabase")

    # Generación de datasets
    anthropic_api_key: Optional[str] = Field(default=None, description="Anthropic API key para generación sintética")
    gemini_api_key: Optional[str] = Field(default=None, description="Google Gemini API key para curación/pipeline")
    generation_output_dir: str = Field(
        default="/app/generated",
        description="Directorio de salida para datasets generados"
    )

    model_config = {
        "env_prefix": "TREFA_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }
