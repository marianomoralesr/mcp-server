#!/bin/bash
# ============================================================
# TREFA - Vast.ai On-Start Script (Self-Contained)
# Pega este script en el campo "On Start Script" de Vast.ai.
# Imagen base recomendada: nvidia/cuda:12.4.1-devel-ubuntu22.04
#
# Variables de entorno requeridas en Vast.ai:
#   HF_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
# ============================================================

set -euo pipefail

LOG_FILE="/var/log/trefa-setup.log"
mkdir -p /var/log

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# ============================================================
# 0. Validación de variables de entorno
# ============================================================
log "Validando variables de entorno..."

MISSING=""
[ -z "${HF_TOKEN:-}" ] && MISSING="$MISSING HF_TOKEN"
[ -z "${SUPABASE_URL:-}" ] && MISSING="$MISSING SUPABASE_URL"
[ -z "${SUPABASE_SERVICE_ROLE_KEY:-}" ] && MISSING="$MISSING SUPABASE_SERVICE_ROLE_KEY"

if [ -n "$MISSING" ]; then
    log "ERROR: Variables de entorno faltantes:$MISSING"
    log "Configuralas en Vast.ai antes de iniciar."
    exit 1
fi

export HF_TOKEN
export HF_HUB_ENABLE_HF_TRANSFER=1

log "Iniciando setup automatizado de TREFA en Vast.ai..."

# ============================================================
# 1. Auto-detección de GPU
# ============================================================
detect_gpu() {
    if ! command -v nvidia-smi &>/dev/null; then
        log "WARN: nvidia-smi no encontrado"
        echo "unknown"
        return
    fi

    local gpu_name
    gpu_name=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs)

    if [ -z "$gpu_name" ]; then
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
# 2. Dependencias del sistema
# ============================================================
log "Instalando dependencias del sistema..."
apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-dev git wget curl ca-certificates

# Node.js 20
if ! command -v node &>/dev/null; then
    log "Instalando Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi

ln -sf /usr/bin/python3 /usr/bin/python

# ============================================================
# 3. Dependencias de Python
# ============================================================
log "Instalando dependencias de Python..."
pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install vllm>=0.6.0
pip install huggingface_hub hf_transfer datasets accelerate

# ============================================================
# 4. Clonar repositorios
# ============================================================
log "Clonando repositorios..."
mkdir -p /app

# Construir URL con token si está disponible
if [ -n "${GITHUB_TOKEN:-}" ]; then
    GIT_REPO_URL="https://${GITHUB_TOKEN}@github.com/marianomoralesr/mcp-server.git"
    log "Usando GITHUB_TOKEN para autenticación Git."
else
    GIT_REPO_URL="https://github.com/marianomoralesr/mcp-server.git"
fi

if [ ! -d "/app/app" ]; then
    git clone -b inference-server "$GIT_REPO_URL" /tmp/inference-repo
    cp -r /tmp/inference-repo/* /app/
    rm -rf /tmp/inference-repo
fi

if [ ! -d "/app/mcp-server/.git" ]; then
    rm -rf /app/mcp-server
    git clone -b main "$GIT_REPO_URL" /app/mcp-server
fi

# ============================================================
# 5. Setup MCP Server
# ============================================================
log "Configurando MCP Server..."
cd /app/mcp-server
npm install
npm run build

# Generar .env para MCP
cat > /app/mcp-server/.env <<EOF
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}
PORT=${MCP_PORT:-3001}
EOF

if [ -n "${MCP_API_KEY:-}" ]; then
    echo "API_KEY=${MCP_API_KEY}" >> /app/mcp-server/.env
fi

# ============================================================
# 6. Dependencias de FastAPI
# ============================================================
log "Instalando dependencias de FastAPI..."
pip install -r /app/app/requirements.txt

# ============================================================
# 7. Crear directorios necesarios
# ============================================================
mkdir -p /app/datasets /app/generated /app/modelos /var/log

# ============================================================
# 8. Descargar modelo
# ============================================================
REPO_ID=${REPO_ID:-"AutosTREFA/trefa-mariana-qwen3-14b"}
FILENAME=${FILENAME:-"trefa-mariana-qwen3-14b-q8_0_gguf/qwen3-14b.Q8_0.gguf"}
MODEL_DIR="/app/modelos/qwen3-14b-gguf"
mkdir -p "$MODEL_DIR"

MODEL_PATH="$MODEL_DIR/$FILENAME"

if [ ! -f "$MODEL_PATH" ]; then
    log "Descargando modelo GGUF..."
    log "  Repo: $REPO_ID"
    log "  File: $FILENAME"
    huggingface-cli download "$REPO_ID" "$FILENAME" \
        --local-dir "$MODEL_DIR" \
        --local-dir-use-symlinks False
    log "Descarga completada."
else
    log "Modelo encontrado en $MODEL_PATH"
fi

if [ ! -f "$MODEL_PATH" ]; then
    log "ERROR: Modelo no encontrado en $MODEL_PATH"
    exit 1
fi

# ============================================================
# 9. Health check reutilizable
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
# 10. Cleanup trap
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
# 11. Iniciar MCP Server
# ============================================================
log "Iniciando MCP Server en puerto ${MCP_PORT:-3001}..."
cd /app/mcp-server
nohup npm run start:http > /var/log/mcp-server.log 2>&1 &
MCP_PID=$!
cd /app

if ! wait_for_service "MCP Server" "http://localhost:${MCP_PORT:-3001}/health" 30 2; then
    log "WARN: MCP Server no respondió, continuando..."
fi

# ============================================================
# 12. Iniciar vLLM
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
    log "ERROR: vLLM no arrancó. Últimas líneas del log:"
    tail -50 /var/log/vllm.log 2>/dev/null || true
    exit 1
fi

# ============================================================
# 13. Iniciar FastAPI
# ============================================================
log "Iniciando FastAPI en puerto ${TREFA_FASTAPI_PORT:-8080}..."
cd /app
python3 -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${TREFA_FASTAPI_PORT:-8080}" > /var/log/fastapi.log 2>&1 &
FASTAPI_PID=$!

if ! wait_for_service "FastAPI" "http://localhost:${TREFA_FASTAPI_PORT:-8080}/health" 15 2; then
    log "ERROR: FastAPI no arrancó. Últimas líneas del log:"
    tail -30 /var/log/fastapi.log 2>/dev/null || true
    exit 1
fi

# ============================================================
# 14. Monitor
# ============================================================
log "Todos los servicios activos."
log "  MCP Server  PID=$MCP_PID  (puerto ${MCP_PORT:-3001})"
log "  vLLM        PID=$VLLM_PID  (puerto 8000)"
log "  FastAPI     PID=$FASTAPI_PID  (puerto ${TREFA_FASTAPI_PORT:-8080})"
log "Monitoreando procesos..."

wait -n "$VLLM_PID" "$MCP_PID" "$FASTAPI_PID" 2>/dev/null
EXIT_CODE=$?

for proc_info in "VLLM:$VLLM_PID" "MCP:$MCP_PID" "FastAPI:$FASTAPI_PID"; do
    name="${proc_info%%:*}"
    pid="${proc_info##*:}"
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        log "ALERTA: $name (PID $pid) terminó inesperadamente."
    fi
done

log "Proceso terminó con código $EXIT_CODE. Saliendo..."
exit "$EXIT_CODE"
