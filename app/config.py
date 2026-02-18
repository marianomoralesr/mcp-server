"""
Configuración centralizada para TREFA Inference Server.
Usa pydantic-settings con prefijo TREFA_ para variables de entorno.
"""

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # vLLM backend (local o remoto)
    vllm_host: str = Field(default="localhost", description="Host del servidor vLLM")
    vllm_port: int = Field(default=8000, description="Puerto del servidor vLLM")
    vllm_base_url: Optional[str] = Field(default="https://api.trefa.mx", description="URL completa del backend LLM. Default: tunnel Cloudflare a GPU remota. En GPU local, override con TREFA_VLLM_BASE_URL=http://localhost:8001")
    vllm_api_key: Optional[str] = Field(default="EMPTY", description="API key para el backend LLM. 'EMPTY' para vLLM local, clave real para Together AI, etc.")

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

    # LiteLLM
    litellm_provider: Optional[str] = Field(
        default=None,
        description="Provider LiteLLM (together_ai, openai, anthropic). Auto-detecta si no se define."
    )

    # Logging
    log_level: str = Field(default="INFO", description="Nivel de logging")

    # Datasets
    dataset_dirs: str = Field(
        default="/app/datasets",
        description="Directorios a escanear para datasets JSONL (separados por coma)"
    )
    database_url: Optional[str] = Field(default=None, description="PostgreSQL connection string para persistencia de conversaciones")

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
