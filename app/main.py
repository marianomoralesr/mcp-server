"""
FastAPI Application for Qwen3-14B Fine-Tuned Model Inference
Supports both direct GGUF inference and LoRA adapters via vLLM
+ Tool calling orchestration via MCP Server
+ Session management multi-turno
+ Feedback y monitoreo
"""

import os
import json
import time
import asyncio
import hashlib
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

import csv
import io
import pathlib
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import httpx
import structlog
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

from app.config import Settings
from app.model_manager import ModelManager
from app.mcp_client import MCPClient
from app.llm_service import LLMService
from app.tool_orchestrator import ToolOrchestrator
from app.session_manager import InMemorySessionManager
from app.system_prompts import build_system_prompt, build_system_prompt_from_text, MARIANA_SYSTEM_PROMPT
from app.feedback import FeedbackManager
from app import dataset_manager
from app import prompt_manager
from app.generation_routes import router as generation_router
from app.job_manager import JobManager

# Configure logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()

# Prometheus metrics
REQUEST_COUNT = Counter('inference_requests_total', 'Total requests', ['endpoint', 'status'])
REQUEST_LATENCY = Histogram('inference_request_duration_seconds', 'Request latency', ['endpoint'])
TOKENS_GENERATED = Counter('tokens_generated_total', 'Total tokens generated')
TOOL_CALLS_TOTAL = Counter('tool_calls_total', 'Total tool calls', ['tool_name', 'status'])
TOOL_CALL_DURATION = Histogram('tool_call_duration_seconds', 'Tool call latency', ['tool_name'])
ORCHESTRATOR_ITERATIONS = Histogram('orchestrator_iterations', 'Iterations per chat request', buckets=[1, 2, 3, 4, 5])
ACTIVE_SESSIONS = Gauge('active_sessions', 'Currently active sessions')
HALLUCINATION_FLAGS = Counter('hallucination_flags_total', 'Potential hallucinations detected')

# Settings
settings = Settings()
security = HTTPBearer(auto_error=False)

# Modo sin vLLM (Railway / testing)
VLLM_DISABLED = os.environ.get("TREFA_VLLM_DISABLED", "").lower() in ("true", "1", "yes")


# Pydantic models
class ChatMessage(BaseModel):
    role: str = Field(..., description="Role: system, user, or assistant")
    content: str = Field(..., description="Message content")


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="qwen3-14b-trefa", description="Model name")
    messages: List[ChatMessage] = Field(..., description="Conversation messages")
    max_tokens: int = Field(default=4096, ge=1, le=32768)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    top_k: int = Field(default=50, ge=1, le=100)
    repetition_penalty: float = Field(default=1.0, ge=1.0, le=2.0)
    stream: bool = Field(default=False)
    use_lora: bool = Field(default=True, description="Use LoRA adapter")
    lora_name: Optional[str] = Field(default="trefa-lora", description="LoRA adapter name")


class CompletionRequest(BaseModel):
    model: str = Field(default="qwen3-14b-trefa")
    prompt: str = Field(..., description="Input prompt")
    max_tokens: int = Field(default=4096, ge=1, le=32768)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    stream: bool = Field(default=False)
    use_lora: bool = Field(default=True)
    lora_name: Optional[str] = Field(default="trefa-lora", description="LoRA adapter name")


class EmbeddingRequest(BaseModel):
    model: str = Field(default="qwen3-14b-trefa")
    input: str | List[str] = Field(..., description="Text to embed")


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str
    permission: List[Dict] = []


class ModelList(BaseModel):
    object: str = "list"
    data: List[ModelInfo]


class ChatRequest(BaseModel):
    message: str = Field(..., description="Mensaje del usuario")
    session_id: Optional[str] = Field(default=None, description="ID de sesión existente")
    max_tokens: int = Field(default=4096, ge=1, le=32768)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class FeedbackRequest(BaseModel):
    session_id: str = Field(..., description="ID de la sesión")
    rating: int = Field(..., ge=1, le=5, description="Calificación 1-5")
    comment: Optional[str] = Field(default=None, description="Comentario opcional")


class ToolCallRequest(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Argumentos de la herramienta")


class VLLMTestConnectionRequest(BaseModel):
    url: str = Field(..., description="URL del backend LLM (ej: http://1.2.3.4:8000 o https://abc.ngrok.io)")


class LoginRequest(BaseModel):
    username: str
    password: str


# Auth token determinista (sobrevive reinicios)
AUTH_TOKEN = hashlib.sha256("admin:AutosTREFA2026!".encode()).hexdigest()

# Rutas publicas (no requieren auth)
PUBLIC_PATHS = {"/", "/ui", "/health", "/metrics", "/api/login", "/api/info",
                "/docs", "/redoc", "/openapi.json",
                "/v1/chat/completions", "/v1/completions", "/v1/models"}


# Global state
model_manager: Optional[ModelManager] = None
http_client: Optional[httpx.AsyncClient] = None
llm_service: Optional[LLMService] = None
mcp_client: Optional[MCPClient] = None
tool_orchestrator: Optional[ToolOrchestrator] = None
session_manager: Optional[InMemorySessionManager] = None
feedback_manager: Optional[FeedbackManager] = None
system_prompt: str = ""
tools_definitions: List[Dict] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global model_manager, http_client, llm_service, mcp_client, tool_orchestrator
    global session_manager, feedback_manager, system_prompt, tools_definitions

    logger.info("Starting up Qwen3-14B Inference Server...")

    # Initialize HTTP client for LLM backend (local or remote, skip if disabled)
    if not VLLM_DISABLED:
        vllm_url = settings.vllm_base_url or f"http://{settings.vllm_host}:{settings.vllm_port}"
        headers = {}
        if settings.vllm_api_key:
            headers["Authorization"] = f"Bearer {settings.vllm_api_key}"
        http_client = httpx.AsyncClient(
            base_url=vllm_url,
            headers=headers,
            timeout=300.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )
        logger.info("vllm_client_configured", base_url=vllm_url, has_api_key=bool(settings.vllm_api_key))
    else:
        http_client = None
        logger.info("vllm_disabled", reason="TREFA_VLLM_DISABLED=true")

    # Initialize LLM service (OpenAI SDK wrapper con tracing)
    if not VLLM_DISABLED:
        llm_service = LLMService(settings)
    else:
        llm_service = None
        logger.info("llm_service_disabled", reason="TREFA_VLLM_DISABLED=true")

    # Initialize model manager
    model_manager = ModelManager(settings)

    # Initialize MCP client
    mcp_client = MCPClient(
        base_url=settings.mcp_server_url,
        api_key=settings.mcp_api_key,
    )
    await mcp_client.initialize()

    # Load tool definitions from MCP and build system prompt
    try:
        tools_definitions = await mcp_client.list_tools()
        system_prompt = build_system_prompt(tools_definitions)
        known_tools = [t["name"] for t in tools_definitions]
        logger.info("mcp_tools_loaded", count=len(tools_definitions), tools=known_tools)
    except Exception as e:
        logger.error("mcp_tools_load_failed", error=str(e))
        tools_definitions = []
        known_tools = []
        system_prompt = build_system_prompt([])

    # Initialize tool orchestrator
    tool_orchestrator = ToolOrchestrator(
        mcp_client=mcp_client,
        llm_service=llm_service,
        known_tools=known_tools,
        max_iterations=settings.max_tool_iterations,
    )

    # Initialize session manager
    session_manager = InMemorySessionManager()

    # Initialize feedback manager
    feedback_manager = FeedbackManager()

    # Verify vLLM is accessible (skip if disabled)
    if not VLLM_DISABLED:
        try:
            response = await http_client.get("/health")
            if response.status_code == 200:
                logger.info("vllm_connected")
            else:
                logger.warning("vllm_health_non_200")
        except Exception as e:
            logger.error("vllm_connect_failed", error=str(e))
    else:
        logger.info("vllm_skipped", mode="no-inference")

    # Verify MCP is accessible
    try:
        mcp_health = await mcp_client.health_check()
        logger.info("mcp_connected", status=mcp_health.get("status"))
    except Exception as e:
        logger.error("mcp_connect_failed", error=str(e))

    # Initialize JobManager for dataset generation
    app.state.job_manager = JobManager(max_concurrent=2)
    app.state.settings = settings
    logger.info("job_manager_initialized")

    # Seed master_prompts y cargar prompt activo de DB
    if settings.database_url:
        try:
            prompt_manager.seed_default_prompt(settings.database_url, MARIANA_SYSTEM_PROMPT)
            active = prompt_manager.get_active_prompt(settings.database_url)
            if active:
                system_prompt = build_system_prompt_from_text(active["prompt_text"], tools_definitions)
                logger.info("active_prompt_loaded", name=active["name"], version=active["version"])
        except Exception as e:
            logger.error("prompt_manager_init_failed", error=str(e))

    yield

    # Shutdown
    logger.info("Shutting down...")
    if http_client:
        await http_client.aclose()
    if mcp_client:
        await mcp_client.close()


# Create FastAPI app
app = FastAPI(
    title="Qwen3-14B TRefA Inference API",
    description="""
    Production inference API for fine-tuned Qwen3-14B model with LoRA adapters.

    Features:
    - OpenAI-compatible chat completions API
    - Tool calling orchestration via MCP Server
    - Multi-turn session management
    - Streaming support
    - LoRA adapter hot-swapping
    - Feedback and monitoring
    """,
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Include generation router
app.include_router(generation_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Auth middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # OPTIONS (CORS preflight) pasan sin auth
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    # Rutas publicas exactas
    if path in PUBLIC_PATHS:
        return await call_next(request)
    # OpenAI-compatible API (con y sin prefijo /v1, para proxies externos)
    openai_paths = ("/chat/completions", "/completions", "/models", "/embeddings")
    if path in openai_paths or path.rstrip("/") in openai_paths:
        return await call_next(request)
    # Bearer token: cualquier request con Authorization header pasa
    # (nuestro auth custom usa X-Auth-Token, no Bearer)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return await call_next(request)
    # Static files
    if path.startswith("/static"):
        return await call_next(request)

    # Todas las demas rutas requieren X-Auth-Token
    token = request.headers.get("X-Auth-Token")
    if token != AUTH_TOKEN:
        return JSONResponse(status_code=401, content={"detail": "No autorizado"})

    return await call_next(request)


# Serve static files
_STATIC_DIR = pathlib.Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/ui", response_class=HTMLResponse)
async def chat_ui():
    """Chat UI + Dashboard"""
    html_path = _STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# Authentication dependency
async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify API token if configured"""
    if settings.api_key and credentials:
        if credentials.credentials != settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials


# ============================================================================
# Core endpoints
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Dashboard UI (root)"""
    html_path = _STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/api/login")
async def login(request: LoginRequest):
    """Autenticacion — retorna token si credenciales validas"""
    if request.username == "admin" and request.password == "AutosTREFA2026!":
        return {"token": AUTH_TOKEN}
    raise HTTPException(status_code=401, detail="Credenciales invalidas")


@app.get("/api/info")
async def api_info():
    """API info endpoint"""
    return {
        "name": "Qwen3-14B TRefA Inference API",
        "version": "2.0.0",
        "mode": "no-inference" if VLLM_DISABLED else "full",
        "status": "healthy",
        "endpoints": {
            "ui": "/ui",
            "chat": "/v1/chat",
            "chat_completions": "/v1/chat/completions",
            "completions": "/v1/completions",
            "sessions": "/v1/sessions/{id}",
            "tools": "/v1/tools",
            "feedback": "/v1/feedback",
            "models": "/v1/models",
            "health": "/health",
            "metrics": "/metrics",
            "analytics": "/admin/analytics",
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        mcp_status = "unknown"

        if VLLM_DISABLED:
            vllm_status = "disabled"
        elif http_client:
            try:
                vllm_health = await http_client.get("/health", timeout=5.0)
                vllm_status = "healthy" if vllm_health.status_code == 200 else "unhealthy"
            except Exception:
                vllm_status = "unreachable"
        else:
            vllm_status = "not_configured"

        if mcp_client:
            try:
                await mcp_client.health_check()
                mcp_status = "healthy"
            except Exception:
                mcp_status = "unreachable"

        vllm_url = None
        if not VLLM_DISABLED and http_client:
            vllm_url = str(http_client.base_url).rstrip("/")

        return {
            "status": "healthy",
            "mode": "no-inference" if VLLM_DISABLED else "full",
            "vllm_backend": vllm_status,
            "vllm_url": vllm_url,
            "mcp_server": mcp_status,
            "model_loaded": model_manager is not None and not VLLM_DISABLED,
            "tools_loaded": len(tools_definitions),
            "active_sessions": session_manager.active_count if session_manager else 0,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    if session_manager:
        ACTIVE_SESSIONS.set(session_manager.active_count)
    from starlette.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================================
# Chat endpoint with tool orchestration (Phase 2 — core feature)
# ============================================================================

@app.post("/v1/chat")
async def chat(
    request: ChatRequest,
    token: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """
    Endpoint principal del chatbot TREFA.
    Recibe mensaje, orquesta tool calling, retorna respuesta.
    """
    if VLLM_DISABLED:
        raise HTTPException(
            status_code=503,
            detail="Inferencia no disponible: servidor en modo UI/tools (sin vLLM). "
                   "Usa /v1/tools, /v1/datasets, o /ui."
        )

    with REQUEST_LATENCY.labels(endpoint="chat").time():
        try:
            # Resolver system prompt activo (DB > fallback hardcodeado)
            active_system_prompt = system_prompt
            active_prompt_id = None
            if settings.database_url:
                try:
                    active = prompt_manager.get_active_prompt(settings.database_url)
                    if active:
                        active_system_prompt = build_system_prompt_from_text(
                            active["prompt_text"], tools_definitions
                        )
                        active_prompt_id = active["id"]
                except Exception:
                    pass  # fallback al system_prompt global

            # Get or create session
            session = None
            if request.session_id:
                session = session_manager.get_session(request.session_id)
            if session is None:
                session = session_manager.create_session(
                    metadata={"master_prompt_id": active_prompt_id}
                )

            # Add user message to session
            session.add_message("user", request.message)

            # Build messages with system prompt + session history
            messages = session.get_messages(active_system_prompt)

            # Execute with tool orchestration
            result = await tool_orchestrator.execute_with_tools(
                messages=messages,
                model=settings.default_lora,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )

            # Save assistant response to session
            session.add_message("assistant", result["response"])

            # Log tool calls in session and metrics
            for tc in result["tool_calls_executed"]:
                session.add_tool_call(tc)
                status = "error" if "error" in tc.get("result", {}) else "success"
                TOOL_CALLS_TOTAL.labels(tool_name=tc["name"], status=status).inc()
                TOOL_CALL_DURATION.labels(tool_name=tc["name"]).observe(tc["duration_ms"] / 1000.0)

            ORCHESTRATOR_ITERATIONS.observe(result["iterations"])

            # Hallucination detection
            if feedback_manager:
                is_suspect = feedback_manager.detect_potential_hallucination(
                    result["response"], result["tool_calls_executed"]
                )
                if is_suspect:
                    HALLUCINATION_FLAGS.inc()

            # Log conversation for review
            if feedback_manager:
                feedback_manager.log_conversation(session, result["tool_calls_executed"])

            REQUEST_COUNT.labels(endpoint="chat", status="success").inc()

            # Persistir conversación en DB
            if settings.database_url:
                try:
                    prompt_id = session.metadata.get("master_prompt_id") or active_prompt_id
                    is_suspect = False
                    if feedback_manager:
                        is_suspect = feedback_manager.detect_potential_hallucination(
                            result["response"], result["tool_calls_executed"]
                        )
                    prompt_manager.save_or_update_conversation(
                        database_url=settings.database_url,
                        session_id=session.id,
                        master_prompt_id=prompt_id,
                        messages=session.messages,
                        tool_calls=result["tool_calls_executed"],
                        has_hallucination=is_suspect,
                    )
                except Exception as exc:
                    logger.warning("conversation_persist_failed", error=str(exc))

            return {
                "session_id": session.id,
                "response": result["response"],
                "tool_calls_executed": [
                    {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                        "result": tc.get("result"),
                        "duration_ms": tc.get("duration_ms"),
                    }
                    for tc in result["tool_calls_executed"]
                ],
                "iterations": result["iterations"],
                "usage": result.get("usage", {}),
            }

        except Exception as e:
            REQUEST_COUNT.labels(endpoint="chat", status="error").inc()
            logger.error("chat_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Session endpoints
# ============================================================================

@app.get("/v1/sessions/{session_id}")
async def get_session(
    session_id: str,
    token: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Obtener historial completo de una sesión"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return session.to_dict()


@app.delete("/v1/sessions/{session_id}")
async def delete_session(
    session_id: str,
    token: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Eliminar sesión"""
    deleted = session_manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return {"status": "deleted", "session_id": session_id}


# ============================================================================
# Tool proxy endpoints
# ============================================================================

@app.get("/v1/tools")
async def list_tools(
    token: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Lista herramientas disponibles del MCP Server"""
    try:
        tools = await mcp_client.list_tools()
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MCP Server error: {e}")


@app.post("/v1/tools/{tool_name}")
async def call_tool(
    tool_name: str,
    request: ToolCallRequest,
    token: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Ejecutar herramienta directamente via MCP Server"""
    try:
        start = time.monotonic()
        result = await mcp_client.call_tool(tool_name, request.arguments)
        duration = time.monotonic() - start
        TOOL_CALLS_TOTAL.labels(tool_name=tool_name, status="success").inc()
        TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(duration)
        return result
    except httpx.HTTPStatusError as e:
        TOOL_CALLS_TOTAL.labels(tool_name=tool_name, status="error").inc()
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        TOOL_CALLS_TOTAL.labels(tool_name=tool_name, status="error").inc()
        raise HTTPException(status_code=502, detail=f"MCP Server error: {e}")


# ============================================================================
# Feedback endpoint (Phase 3)
# ============================================================================

@app.post("/v1/feedback")
async def submit_feedback(
    request: FeedbackRequest,
    token: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Enviar feedback sobre una sesión"""
    session = session_manager.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    if feedback_manager:
        feedback_manager.submit_rating(
            session_id=request.session_id,
            rating=request.rating,
            comment=request.comment,
        )

    # Persistir rating en chat_conversations
    if settings.database_url:
        try:
            prompt_manager.update_conversation_rating(
                settings.database_url, request.session_id, request.rating, request.comment
            )
        except Exception as exc:
            logger.warning("conversation_rating_persist_failed", error=str(exc))

    return {
        "status": "received",
        "session_id": request.session_id,
        "rating": request.rating,
    }


# ============================================================================
# Admin analytics (Phase 3)
# ============================================================================

@app.get("/admin/analytics")
async def analytics(
    token: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Dashboard de analíticas: herramientas más usadas, sesiones, tasa de tool calling"""
    stats = {}
    if feedback_manager:
        stats = feedback_manager.get_analytics()
    stats["active_sessions"] = session_manager.active_count if session_manager else 0
    stats["tools_loaded"] = len(tools_definitions)
    return stats


@app.post("/admin/vllm/test-connection")
async def test_vllm_connection(request: VLLMTestConnectionRequest):
    """Probar conexión a un servidor vLLM remoto sin modificar el estado global"""
    # Normalizar URL
    url = request.url.strip().rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url

    result = {
        "connected": False,
        "url": url,
        "latency_ms": None,
        "health_status": None,
        "models": [],
        "error": None,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Probar /health
            t0 = time.monotonic()
            health_resp = await client.get(f"{url}/health")
            latency = (time.monotonic() - t0) * 1000
            result["latency_ms"] = round(latency, 1)
            result["health_status"] = "healthy" if health_resp.status_code == 200 else f"status {health_resp.status_code}"

            # Obtener modelos
            try:
                models_resp = await client.get(f"{url}/v1/models")
                if models_resp.status_code == 200:
                    data = models_resp.json()
                    result["models"] = [m["id"] for m in data.get("data", [])]
            except Exception:
                pass  # modelos no disponibles, pero health sí respondió

            result["connected"] = True
    except httpx.ConnectError:
        result["error"] = "Connection refused"
    except httpx.ConnectTimeout:
        result["error"] = "Connection timeout (10s)"
    except httpx.ReadTimeout:
        result["error"] = "Read timeout (10s)"
    except Exception as e:
        result["error"] = str(e)

    return result


# ============================================================================
# OpenAI-compatible endpoints (existing, preserved)
# ============================================================================

@app.get("/v1/models", response_model=ModelList)
async def list_models():
    """List available models"""
    models = [
        ModelInfo(
            id="qwen3-14b-trefa",
            created=1700000000,
            owned_by="trefa"
        ),
        ModelInfo(
            id="qwen3-14b-trefa-lora",
            created=1700000000,
            owned_by="trefa"
        )
    ]

    for quant in ["q4", "q5", "q8"]:
        models.append(ModelInfo(
            id=f"qwen3-14b-trefa-{quant}",
            created=1700000000,
            owned_by="trefa"
        ))

    return ModelList(object="list", data=models)


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    token: HTTPAuthorizationCredentials = Depends(verify_token)
):
    """
    OpenAI-compatible chat completions endpoint

    Supports streaming and non-streaming responses.
    Can toggle LoRA adapter with use_lora parameter.
    """
    if VLLM_DISABLED:
        raise HTTPException(status_code=503, detail="Inferencia no disponible (modo sin vLLM)")

    with REQUEST_LATENCY.labels(endpoint="chat_completions").time():
        try:
            model = request.lora_name if request.use_lora else "qwen3-14b"
            messages = [{"role": m.role, "content": m.content} for m in request.messages]
            extra = {
                "top_p": request.top_p,
                "extra_body": {
                    "top_k": request.top_k,
                    "repetition_penalty": request.repetition_penalty,
                },
            }

            if request.stream:
                return StreamingResponse(
                    llm_service.chat_completion_stream(
                        messages=messages,
                        model=model,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        **extra,
                    ),
                    media_type="text/event-stream",
                )

            result = await llm_service.chat_completion(
                messages=messages,
                model=model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **extra,
            )
            REQUEST_COUNT.labels(endpoint="chat_completions", status="success").inc()

            usage = result.get("usage") or {}
            if usage.get("completion_tokens"):
                TOKENS_GENERATED.inc(usage["completion_tokens"])

            return result

        except Exception as e:
            REQUEST_COUNT.labels(endpoint="chat_completions", status="error").inc()
            logger.error("chat_completion_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/completions")
async def completions(
    request: CompletionRequest,
    token: HTTPAuthorizationCredentials = Depends(verify_token)
):
    """Legacy completions endpoint"""
    if VLLM_DISABLED:
        raise HTTPException(status_code=503, detail="Inferencia no disponible (modo sin vLLM)")

    with REQUEST_LATENCY.labels(endpoint="completions").time():
        try:
            model = request.lora_name if request.use_lora else "qwen3-14b"
            extra = {"top_p": request.top_p}

            if request.stream:
                return StreamingResponse(
                    llm_service.text_completion_stream(
                        prompt=request.prompt,
                        model=model,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        **extra,
                    ),
                    media_type="text/event-stream",
                )

            result = await llm_service.text_completion(
                prompt=request.prompt,
                model=model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **extra,
            )
            REQUEST_COUNT.labels(endpoint="completions", status="success").inc()
            return result

        except Exception as e:
            REQUEST_COUNT.labels(endpoint="completions", status="error").inc()
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/embeddings")
async def embeddings(
    request: EmbeddingRequest,
    token: HTTPAuthorizationCredentials = Depends(verify_token)
):
    """Generate embeddings (if supported by model)"""
    raise HTTPException(status_code=501, detail="Embeddings not yet implemented for this model")


@app.post("/v1/load_lora")
async def load_lora_adapter(
    adapter_name: str,
    adapter_path: Optional[str] = None,
    token: HTTPAuthorizationCredentials = Depends(verify_token)
):
    """Dynamically load a LoRA adapter"""
    try:
        load_request = {
            "lora_name": adapter_name,
            "lora_path": adapter_path or f"/app/adapters/{adapter_name}"
        }

        response = await http_client.post(
            "/v1/load_lora_adapter",
            json=load_request,
            timeout=60.0
        )

        if response.status_code == 200:
            return {"status": "success", "message": f"LoRA adapter '{adapter_name}' loaded"}
        else:
            raise HTTPException(
                status_code=response.status_code,
                detail=response.text
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/loaded_loras")
async def list_loaded_loras(
    token: HTTPAuthorizationCredentials = Depends(verify_token)
):
    """List currently loaded LoRA adapters"""
    try:
        response = await http_client.get("/v1/models")
        data = response.json()

        loras = [
            model for model in data.get("data", [])
            if "lora" in model.get("id", "").lower()
        ]

        return {"loaded_loras": loras}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DatasetConversationSave(BaseModel):
    source_file: str
    line_number: int
    messages: List[Dict[str, str]]
    tags: List[str] = []
    quality_rating: Optional[int] = Field(None, ge=1, le=5)
    review_notes: Optional[str] = None
    target_format: str = "qwen"


class SwitchQuantizationRequest(BaseModel):
    quant_level: str = Field(..., pattern="^(Q4|Q5|Q8|q4|q5|q8)$")


@app.post("/admin/switch_quantization")
async def switch_quantization(
    request: SwitchQuantizationRequest,
    token: HTTPAuthorizationCredentials = Depends(verify_token)
):
    """
    Admin endpoint to switch between quantization levels
    Note: This requires restarting the vLLM server
    """
    quant_level = request.quant_level.upper()
    model_file = f"/app/models/qwen3-14b-{quant_level}.gguf"

    if not os.path.exists(model_file):
        raise HTTPException(
            status_code=404,
            detail=f"Quantization level {quant_level} not found at {model_file}"
        )

    return {
        "message": f"To switch to {quant_level}, restart container with DEFAULT_QUANT={quant_level}",
        "model_file": model_file,
        "required_action": "container_restart"
    }


# ============================================================================
# Prompt Management endpoints (Prompts tab)
# ============================================================================


class PromptCreateRequest(BaseModel):
    name: str = Field(..., description="Nombre único del prompt")
    prompt_text: str = Field(..., description="Texto del prompt")
    description: str = Field(default="", description="Descripción opcional")


class PromptUpdateRequest(BaseModel):
    name: Optional[str] = None
    prompt_text: Optional[str] = None
    description: Optional[str] = None


@app.get("/v1/prompts")
async def list_prompts_endpoint():
    """Lista todos los master prompts"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    return {"prompts": prompt_manager.list_prompts(settings.database_url)}


@app.post("/v1/prompts")
async def create_prompt_endpoint(request: PromptCreateRequest):
    """Crea un nuevo master prompt"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    try:
        result = prompt_manager.create_prompt(
            settings.database_url, request.name, request.prompt_text, request.description
        )
        return {"status": "created", "prompt": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/v1/prompts/active")
async def get_active_prompt_endpoint():
    """Retorna el prompt activo actual"""
    if not settings.database_url:
        return {"prompt": None, "fallback": True}
    active = prompt_manager.get_active_prompt(settings.database_url)
    return {"prompt": active, "fallback": active is None}


@app.get("/v1/prompts/stats")
async def get_all_prompts_stats():
    """Estadísticas de todos los prompts"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    return {"stats": prompt_manager.get_all_prompt_stats(settings.database_url)}


@app.get("/v1/prompts/{prompt_id}")
async def get_prompt_endpoint(prompt_id: int):
    """Obtiene un prompt por ID"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    result = prompt_manager.get_prompt(settings.database_url, prompt_id)
    if not result:
        raise HTTPException(status_code=404, detail="Prompt no encontrado")
    return {"prompt": result}


@app.put("/v1/prompts/{prompt_id}")
async def update_prompt_endpoint(prompt_id: int, request: PromptUpdateRequest):
    """Actualiza un prompt existente"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    data = {k: v for k, v in request.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="No hay campos para actualizar")
    result = prompt_manager.update_prompt(settings.database_url, prompt_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="Prompt no encontrado")
    return {"status": "updated", "prompt": result}


@app.delete("/v1/prompts/{prompt_id}")
async def delete_prompt_endpoint(prompt_id: int):
    """Elimina un prompt (no se puede eliminar el activo)"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    try:
        deleted = prompt_manager.delete_prompt(settings.database_url, prompt_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Prompt no encontrado")
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/v1/prompts/{prompt_id}/activate")
async def activate_prompt_endpoint(prompt_id: int):
    """Activa un prompt y desactiva todos los demás"""
    global system_prompt
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    result = prompt_manager.activate_prompt(settings.database_url, prompt_id)
    if not result:
        raise HTTPException(status_code=404, detail="Prompt no encontrado")
    # Actualizar system_prompt global
    system_prompt = build_system_prompt_from_text(result["prompt_text"], tools_definitions)
    logger.info("prompt_activated", name=result["name"], version=result["version"])
    return {"status": "activated", "prompt": result}


@app.get("/v1/prompts/{prompt_id}/stats")
async def get_prompt_stats_endpoint(prompt_id: int):
    """Estadísticas de un prompt específico"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    return {"stats": prompt_manager.get_prompt_stats(settings.database_url, prompt_id)}


@app.get("/v1/prompts/{prompt_id}/conversations")
async def get_prompt_conversations_endpoint(prompt_id: int, offset: int = 0, limit: int = 20):
    """Conversaciones paginadas de un prompt"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    return prompt_manager.get_conversations_by_prompt(settings.database_url, prompt_id, offset, limit)


# ============================================================================
# Dataset endpoints (Datasets tab)
# ============================================================================

@app.get("/v1/datasets/files")
async def dataset_list_files(rescan: bool = False):
    """Lista archivos JSONL escaneados en los directorios configurados"""
    dirs = [d.strip() for d in settings.dataset_dirs.split(",") if d.strip()]
    gen_dir = settings.generation_output_dir
    if gen_dir and gen_dir not in dirs:
        dirs.append(gen_dir)
    files = dataset_manager.scan_jsonl_files(dirs, force_rescan=rescan)
    return {"files": files, "count": len(files), "directories": dirs}


@app.get("/v1/datasets/files/{file_path:path}")
async def dataset_read_file(file_path: str, offset: int = 0, limit: int = 50):
    """Lee conversaciones paginadas de un archivo JSONL"""
    # Validar que el path esté dentro de los directorios configurados
    dirs = [d.strip() for d in settings.dataset_dirs.split(",") if d.strip()]
    gen_dir = settings.generation_output_dir
    if gen_dir and gen_dir not in dirs:
        dirs.append(gen_dir)
    allowed = any(file_path.startswith(d) for d in dirs)
    if not allowed:
        raise HTTPException(status_code=403, detail="Ruta fuera de directorios permitidos")
    result = dataset_manager.read_jsonl_page(file_path, offset=offset, limit=limit)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/v1/datasets/validate")
async def dataset_validate(request: Request):
    """Valida un array de mensajes contra formato Qwen ChatML"""
    body = await request.json()
    messages = body.get("messages", [])
    return dataset_manager.validate_qwen_format(messages)


@app.post("/v1/datasets/conversations")
async def dataset_save_conversation(request: DatasetConversationSave):
    """Guarda/actualiza conversacion en PostgreSQL"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    try:
        return dataset_manager.save_conversation(
            settings.database_url, request.model_dump()
        )
    except Exception as e:
        logger.error("dataset_save_error", error=str(e), source_file=request.source_file, line=request.line_number)
        raise HTTPException(status_code=502, detail=f"Error al guardar en PostgreSQL: {str(e)}")


@app.get("/v1/datasets/conversations")
async def dataset_get_conversations(
    source_file: Optional[str] = None,
    rating: Optional[int] = None,
    tag: Optional[str] = None,
):
    """Obtiene conversaciones guardadas con filtros opcionales"""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="PostgreSQL no configurado (TREFA_DATABASE_URL)")
    try:
        data = dataset_manager.get_saved_conversations(
            settings.database_url,
            source_file=source_file, rating=rating, tag=tag
        )
        return {"conversations": data, "count": len(data)}
    except Exception as e:
        logger.error("dataset_get_error", error=str(e))
        raise HTTPException(status_code=502, detail=f"Error al consultar PostgreSQL: {str(e)}")


@app.get("/v1/datasets/stats")
async def dataset_stats():
    """Estadísticas de revisión de datasets"""
    if not settings.database_url:
        return {"total": 0, "reviewed": 0, "pending": 0, "review_rate": 0, "by_rating": {}, "by_tag": {}}
    return dataset_manager.get_review_stats(settings.database_url)


class RenameRequest(BaseModel):
    old_path: str
    new_name: str


class FileMetadataRequest(BaseModel):
    file_path: str
    version: Optional[str] = None
    file_tags: Optional[List[str]] = None
    notes: Optional[str] = None


class MergeRequest(BaseModel):
    file_paths: List[str]
    output_filename: str = ""


@app.post("/v1/datasets/upload")
async def upload_dataset(file: UploadFile = File(...)):
    """Sube un archivo JSONL (o CSV que se convierte) al directorio de generación"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo requerido")

    is_csv = file.filename.lower().endswith(".csv")
    is_jsonl = file.filename.lower().endswith(".jsonl")

    if not is_csv and not is_jsonl:
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .jsonl o .csv")

    output_dir = Path(settings.generation_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    text = content.decode("utf-8")

    if is_csv:
        # Convertir CSV a JSONL
        jsonl_lines = _convert_csv_to_jsonl(text)
        dest_name = file.filename.rsplit(".", 1)[0] + ".jsonl"
        dest = output_dir / dest_name
        out_content = "\n".join(jsonl_lines) + "\n"
        dest.write_text(out_content, encoding="utf-8")
        dataset_manager._file_cache.clear()
        return {
            "filename": dest_name,
            "lines": len(jsonl_lines),
            "size_bytes": len(out_content.encode("utf-8")),
            "path": str(dest),
            "converted_from": "csv",
        }

    # JSONL directo
    lines = text.splitlines()
    valid_lines = 0
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
            valid_lines += 1
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail=f"Linea {i} no es JSON valido"
            )

    dest = output_dir / file.filename
    dest.write_bytes(content)
    dataset_manager._file_cache.clear()

    return {
        "filename": file.filename,
        "lines": valid_lines,
        "size_bytes": len(content),
        "path": str(dest),
    }


def _convert_csv_to_jsonl(csv_text: str) -> list:
    """Convierte CSV a líneas JSONL. Auto-detecta formato."""
    reader = csv.DictReader(io.StringIO(csv_text))
    cols = [c.lower().strip() for c in (reader.fieldnames or [])]

    jsonl_lines = []

    # Formato 1: columnas system, user, assistant
    if "system" in cols and "user" in cols and "assistant" in cols:
        for row in reader:
            messages = []
            sys_val = (row.get("system") or row.get("System") or "").strip()
            usr_val = (row.get("user") or row.get("User") or "").strip()
            ast_val = (row.get("assistant") or row.get("Assistant") or "").strip()
            if sys_val:
                messages.append({"role": "system", "content": sys_val})
            if usr_val:
                messages.append({"role": "user", "content": usr_val})
            if ast_val:
                messages.append({"role": "assistant", "content": ast_val})
            if messages:
                jsonl_lines.append(json.dumps({"messages": messages}, ensure_ascii=False))
        return jsonl_lines

    # Formato 2: columna messages con JSON string
    if "messages" in cols:
        for row in reader:
            msgs_raw = (row.get("messages") or row.get("Messages") or "").strip()
            if not msgs_raw:
                continue
            try:
                msgs = json.loads(msgs_raw)
                jsonl_lines.append(json.dumps({"messages": msgs}, ensure_ascii=False))
            except json.JSONDecodeError:
                continue
        return jsonl_lines

    # Formato 3: columnas role + content (+ opcional conversation_id)
    if "role" in cols and "content" in cols:
        convs = {}
        for row in reader:
            cid = (row.get("conversation_id") or row.get("Conversation_id") or "default").strip()
            role = (row.get("role") or row.get("Role") or "").strip()
            content_val = (row.get("content") or row.get("Content") or "").strip()
            if not role or not content_val:
                continue
            convs.setdefault(cid, []).append({"role": role, "content": content_val})
        for msgs in convs.values():
            if msgs:
                jsonl_lines.append(json.dumps({"messages": msgs}, ensure_ascii=False))
        return jsonl_lines

    raise HTTPException(
        status_code=400,
        detail="Formato CSV no reconocido. Se esperan columnas: (role,content), (system,user,assistant), o (messages)"
    )


@app.post("/v1/datasets/merge")
async def merge_datasets(body: MergeRequest):
    """Combina múltiples archivos JSONL en uno"""
    if len(body.file_paths) < 2:
        raise HTTPException(status_code=400, detail="Se necesitan al menos 2 archivos para merge")

    # Validar que los archivos existan
    for fp in body.file_paths:
        if not Path(fp).exists():
            raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {fp}")

    output_dir = Path(settings.generation_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_name = body.output_filename.strip()
    if not output_name:
        from datetime import datetime
        output_name = f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    if not output_name.endswith(".jsonl"):
        output_name += ".jsonl"

    output_path = output_dir / output_name
    result = dataset_manager.merge_jsonl_files(body.file_paths, str(output_path))

    # Invalidar cache
    dataset_manager._file_cache.clear()

    return result


@app.post("/v1/datasets/auto-tag")
async def auto_tag_datasets():
    """Detecta Tool Calling en todos los archivos JSONL"""
    dirs = [d.strip() for d in settings.dataset_dirs.split(",") if d.strip()]
    # Incluir también el directorio de generación
    gen_dir = settings.generation_output_dir
    if gen_dir and gen_dir not in dirs:
        dirs.append(gen_dir)
    return dataset_manager.auto_tag_files(dirs)


@app.delete("/v1/datasets/files/{file_path:path}")
async def dataset_delete_file(file_path: str):
    """Elimina un archivo JSONL y su sidecar .meta.json"""
    dirs = [d.strip() for d in settings.dataset_dirs.split(",") if d.strip()]
    gen_dir = settings.generation_output_dir
    if gen_dir:
        dirs.append(gen_dir)
    allowed = any(file_path.startswith(d) for d in dirs)
    if not allowed:
        raise HTTPException(status_code=403, detail="Ruta fuera de directorios permitidos")

    fp = Path(file_path)
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    fp.unlink()
    # Borrar sidecar si existe
    meta_fp = dataset_manager._meta_path(file_path)
    if meta_fp.exists():
        meta_fp.unlink()

    dataset_manager._file_cache.clear()
    return {"status": "deleted", "path": file_path}


@app.post("/v1/datasets/files/rename")
async def dataset_rename_file(body: RenameRequest):
    """Renombra un archivo JSONL y su sidecar .meta.json"""
    dirs = [d.strip() for d in settings.dataset_dirs.split(",") if d.strip()]
    gen_dir = settings.generation_output_dir
    if gen_dir:
        dirs.append(gen_dir)
    allowed = any(body.old_path.startswith(d) for d in dirs)
    if not allowed:
        raise HTTPException(status_code=403, detail="Ruta fuera de directorios permitidos")

    old_fp = Path(body.old_path)
    if not old_fp.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    new_name = body.new_name.strip()
    if not new_name.endswith(".jsonl"):
        new_name += ".jsonl"

    new_fp = old_fp.parent / new_name
    if new_fp.exists():
        raise HTTPException(status_code=409, detail=f"Ya existe un archivo con ese nombre: {new_name}")

    old_fp.rename(new_fp)

    # Renombrar sidecar si existe
    old_meta = dataset_manager._meta_path(body.old_path)
    if old_meta.exists():
        new_meta = dataset_manager._meta_path(str(new_fp))
        old_meta.rename(new_meta)

    dataset_manager._file_cache.clear()
    return {"status": "renamed", "old_path": body.old_path, "new_path": str(new_fp)}


@app.get("/v1/datasets/files/{file_path:path}/metadata")
async def dataset_get_metadata(file_path: str):
    """Obtiene metadatos de un archivo JSONL desde su sidecar"""
    dirs = [d.strip() for d in settings.dataset_dirs.split(",") if d.strip()]
    gen_dir = settings.generation_output_dir
    if gen_dir:
        dirs.append(gen_dir)
    allowed = any(file_path.startswith(d) for d in dirs)
    if not allowed:
        raise HTTPException(status_code=403, detail="Ruta fuera de directorios permitidos")

    return dataset_manager.get_file_metadata(file_path)


@app.post("/v1/datasets/files/metadata")
async def dataset_save_metadata(body: FileMetadataRequest):
    """Guarda metadatos de un archivo JSONL en su sidecar"""
    dirs = [d.strip() for d in settings.dataset_dirs.split(",") if d.strip()]
    gen_dir = settings.generation_output_dir
    if gen_dir:
        dirs.append(gen_dir)
    allowed = any(body.file_path.startswith(d) for d in dirs)
    if not allowed:
        raise HTTPException(status_code=403, detail="Ruta fuera de directorios permitidos")

    if not Path(body.file_path).exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    data = {}
    if body.version is not None:
        data["version"] = body.version
    if body.file_tags is not None:
        data["file_tags"] = body.file_tags
    if body.notes is not None:
        data["notes"] = body.notes

    result = dataset_manager.save_file_metadata(body.file_path, data)
    dataset_manager._file_cache.clear()
    return result


@app.post("/v1/datasets/convert-csv")
async def convert_csv_dataset(file: UploadFile = File(...)):
    """Convierte un archivo CSV a JSONL y lo guarda"""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .csv")

    output_dir = Path(settings.generation_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    text = content.decode("utf-8")
    jsonl_lines = _convert_csv_to_jsonl(text)

    dest_name = file.filename.rsplit(".", 1)[0] + ".jsonl"
    dest = output_dir / dest_name
    out_content = "\n".join(jsonl_lines) + "\n"
    dest.write_text(out_content, encoding="utf-8")

    dataset_manager._file_cache.clear()
    return {
        "filename": dest_name,
        "lines": len(jsonl_lines),
        "size_bytes": len(out_content.encode("utf-8")),
        "path": str(dest),
        "converted_from": "csv",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.fastapi_port)
