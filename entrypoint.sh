#!/bin/bash
# ============================================================
# TREFA Inference Server - Docker Entrypoint
# Inicia MCP Server + vLLM + FastAPI con auto-detección de GPU
# ============================================================

set -euo pipefail

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"; }

# ============================================================
# 0. Validación de variables de entorno
# ============================================================
if [ -z "${HF_TOKEN:-}" ]; then
    log "ERROR: HF_TOKEN no está definido. Exportalo antes de iniciar."
    log "  export HF_TOKEN=hf_tu_token_aqui"
    exit 1
fi
export HF_TOKEN

log "Iniciando contenedor TREFA (vLLM + MCP + FastAPI)..."

# ============================================================
# 1. Auto-detección de GPU
# ============================================================
detect_gpu() {
    if ! command -v nvidia-smi &>/dev/null; then
        log "WARN: nvidia-smi no encontrado, usando defaults"
        echo "unknown"
        return
    fi

    local gpu_name
    gpu_name=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs)

    if [ -z "$gpu_name" ]; then
        log "WARN: No se detectó GPU, usando defaults"
        echo "unknown"
        return
    fi

    log "GPU detectada: $gpu_name"
    echo "$gpu_name"
}

configure_gpu() {
    local gpu_name="$1"

    case "$gpu_name" in
        *5090*)
            log "Configurando para RTX 5090 (32GB VRAM)"
            DEFAULT_MAX_MODEL_LEN=8192
            DEFAULT_GPU_MEM=0.88
            ;;
        *A6000*|*a6000*)
            log "Configurando para A6000 (48GB VRAM)"
            DEFAULT_MAX_MODEL_LEN=16384
            DEFAULT_GPU_MEM=0.90
            ;;
        *A100*|*a100*)
            log "Configurando para A100 (40/80GB VRAM)"
            DEFAULT_MAX_MODEL_LEN=32768
            DEFAULT_GPU_MEM=0.92
            ;;
        *H100*|*h100*)
            log "Configurando para H100 (80GB VRAM)"
            DEFAULT_MAX_MODEL_LEN=32768
            DEFAULT_GPU_MEM=0.92
            ;;
        *)
            log "GPU no reconocida, usando defaults conservadores"
            DEFAULT_MAX_MODEL_LEN=8192
            DEFAULT_GPU_MEM=0.85
            ;;
    esac

    # Las env vars siempre tienen prioridad
    MAX_MODEL_LEN=${MAX_MODEL_LEN:-$DEFAULT_MAX_MODEL_LEN}
    GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-$DEFAULT_GPU_MEM}
    DTYPE=${DTYPE:-"half"}

    log "  MAX_MODEL_LEN=$MAX_MODEL_LEN"
    log "  GPU_MEMORY_UTILIZATION=$GPU_MEMORY_UTILIZATION"
    log "  DTYPE=$DTYPE"
}

GPU_NAME=$(detect_gpu)
configure_gpu "$GPU_NAME"

# ============================================================
# 2. Cleanup trap
# ============================================================
MCP_PID=""
VLLM_PID=""
FASTAPI_PID=""

cleanup() {
    log "Deteniendo servicios..."
    [ -n "$FASTAPI_PID" ] && kill "$FASTAPI_PID" 2>/dev/null || true
    [ -n "$VLLM_PID" ] && kill "$VLLM_PID" 2>/dev/null || true
    [ -n "$MCP_PID" ] && kill "$MCP_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    log "Todos los servicios detenidos."
}

trap cleanup EXIT INT TERM

# ============================================================
# 3. Health check reutilizable
# ============================================================
wait_for_service() {
    local name="$1" url="$2" max_retries="$3" interval="$4"
    log "Esperando a $name..."
    for i in $(seq 1 "$max_retries"); do
        if curl -sf "$url" > /dev/null 2>&1; then
            log "$name listo."
            return 0
        fi
        if [ "$((i % 10))" -eq 0 ]; then
            log "  $name: intento $i/$max_retries..."
        fi
        sleep "$interval"
    done
    log "ERROR: $name no respondió después de $max_retries intentos."
    return 1
}

# ============================================================
# 4. Generar .env para MCP Server
# ============================================================
if [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
    log "Generando .env para MCP Server..."
    cat > /app/mcp-server/.env <<EOF
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}
PORT=${MCP_PORT:-3001}
EOF
    if [ -n "${MCP_API_KEY:-}" ]; then
        echo "API_KEY=${MCP_API_KEY}" >> /app/mcp-server/.env
    fi
    log ".env de MCP generado."
else
    log "WARN: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no definidos, MCP puede fallar."
fi

# ============================================================
# 5. Iniciar MCP Server
# ============================================================
log "Iniciando MCP Server en puerto ${MCP_PORT:-3001}..."
cd /app/mcp-server
nohup npm run start:http > /var/log/mcp-server.log 2>&1 &
MCP_PID=$!
cd /app

if ! wait_for_service "MCP Server" "http://localhost:${MCP_PORT:-3001}/health" 30 2; then
    log "WARN: MCP Server no respondió, continuando de todos modos..."
fi

# ============================================================
# 6. Descargar modelo si no existe
# ============================================================
REPO_ID=${REPO_ID:-"AutosTREFA/trefa-mariana-qwen3-14b"}
FILENAME=${FILENAME:-"trefa-mariana-qwen3-14b-q8_0_gguf/qwen3-14b.Q8_0.gguf"}
MODEL_DIR="/app/modelos/qwen3-14b-gguf"
mkdir -p "$MODEL_DIR"

# Path correcto: MODEL_DIR + FILENAME (incluye subdirectorio)
MODEL_PATH="$MODEL_DIR/$FILENAME"

if [ ! -f "$MODEL_PATH" ]; then
    log "Descargando modelo GGUF desde HuggingFace..."
    log "  Repo: $REPO_ID"
    log "  File: $FILENAME"
    huggingface-cli download "$REPO_ID" "$FILENAME" \
        --local-dir "$MODEL_DIR" \
        --local-dir-use-symlinks False
    log "Descarga completada."
else
    log "Modelo GGUF encontrado en $MODEL_PATH"
fi

# Verificar que el archivo existe
if [ ! -f "$MODEL_PATH" ]; then
    log "ERROR: Modelo no encontrado en $MODEL_PATH después de descarga."
    exit 1
fi

# ============================================================
# 7. Iniciar vLLM
# ============================================================
log "Arrancando vLLM (MAX_MODEL_LEN=$MAX_MODEL_LEN, GPU_MEM=$GPU_MEMORY_UTILIZATION)..."
python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --tokenizer "$REPO_ID" \
    --max-model-len "$MAX_MODEL_LEN" \
    --dtype "$DTYPE" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
    --trust-remote-code \
    --host 0.0.0.0 \
    --port 8000 > /var/log/vllm.log 2>&1 &
VLLM_PID=$!

if ! wait_for_service "vLLM" "http://localhost:8000/health" 90 4; then
    log "ERROR: vLLM no arrancó. Revisa /var/log/vllm.log"
    tail -50 /var/log/vllm.log 2>/dev/null || true
    exit 1
fi

# ============================================================
# 8. Iniciar FastAPI Orchestrator
# ============================================================
log "Iniciando FastAPI Orchestrator en puerto ${TREFA_FASTAPI_PORT:-8080}..."
python3 -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${TREFA_FASTAPI_PORT:-8080}" > /var/log/fastapi.log 2>&1 &
FASTAPI_PID=$!

if ! wait_for_service "FastAPI" "http://localhost:${TREFA_FASTAPI_PORT:-8080}/health" 15 2; then
    log "ERROR: FastAPI no arrancó. Revisa /var/log/fastapi.log"
    tail -30 /var/log/fastapi.log 2>/dev/null || true
    exit 1
fi

# ============================================================
# 9. Monitor — esperar a que algún proceso muera
# ============================================================
log "Todos los servicios activos."
log "  MCP Server  PID=$MCP_PID  (puerto ${MCP_PORT:-3001})"
log "  vLLM        PID=$VLLM_PID  (puerto 8000)"
log "  FastAPI     PID=$FASTAPI_PID  (puerto ${TREFA_FASTAPI_PORT:-8080})"
log "Monitoreando procesos..."

wait -n "$VLLM_PID" "$MCP_PID" "$FASTAPI_PID" 2>/dev/null
EXIT_CODE=$?

# Identificar qué proceso murió
for proc_info in "VLLM:$VLLM_PID" "MCP:$MCP_PID" "FastAPI:$FASTAPI_PID"; do
    name="${proc_info%%:*}"
    pid="${proc_info##*:}"
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        log "ALERTA: $name (PID $pid) terminó inesperadamente."
    fi
done

log "Proceso terminó con código $EXIT_CODE. Saliendo..."
exit "$EXIT_CODE"
