#!/bin/bash
# ============================================================
# TREFA - Script de Entrada vLLM + MCP Server
# ============================================================

set -e
log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"; }

# 0. Configurar Token de HuggingFace (Usuario)
export HF_TOKEN="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"

log "🚀 Iniciando contenedor TREFA (vLLM + MCP)..."

# 1. Iniciar MCP Server (Segundo plano)
log "🛠️ Iniciando MCP Server en puerto ${PORT:-3001}..."
cd /app/mcp-server
nohup npm run start:http > /var/log/mcp-server.log 2>&1 &
MCP_PID=$!
log "✅ MCP Server iniciado (PID: $MCP_PID)"

cd /app

# 2. Configuración vLLM
# Modelo por defecto: AutosTREFA/trefa-mariana-qwen3-14b
# Archivo GGUF específico
REPO_ID=${REPO_ID:-"AutosTREFA/trefa-mariana-qwen3-14b"}
FILENAME=${FILENAME:-"trefa-mariana-qwen3-14b-q8_0_gguf/qwen3-14b.Q8_0.gguf"}

# Si se usa GGUF, vLLM necesita la ruta local o el repo/filename.
# Para GGUF en vLLM, lo mejor es descargar el archivo primero si no existe.
MODEL_DIR="/app/modelos/qwen3-14b-gguf"
mkdir -p "$MODEL_DIR"
MODEL_PATH="$MODEL_DIR/$(basename "$FILENAME")"

if [ ! -f "$MODEL_PATH" ]; then
    log "📥 Descargando modelo GGUF desde HuggingFace..."
    log "   Repo: $REPO_ID"
    log "   File: $FILENAME"
    huggingface-cli download "$REPO_ID" "$FILENAME" --local-dir "$MODEL_DIR" --local-dir-use-symlinks False
    log "✅ Descarga completada."
else
    log "✅ Modelo GGUF encontrado en $MODEL_PATH"
fi


MAX_MODEL_LEN=${MAX_MODEL_LEN:-8192}
DTYPE=${DTYPE:-"half"} 
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.90} 

log "🔮 Arrancando servidor vLLM con modelo GGUF..."

# Lanzar vLLM apuntando al archivo GGUF descargado
python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --tokenizer "$REPO_ID" \
    --max-model-len "$MAX_MODEL_LEN" \
    --dtype "$DTYPE" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
    --trust-remote-code \
    --host 0.0.0.0 \
    --port 8000 &
VLLM_PID=$!

log "✅ vLLM iniciado (PID: $VLLM_PID)"

# 3. Iniciar FastAPI Orchestrator
log "🌐 Iniciando FastAPI Orchestrator en puerto ${TREFA_FASTAPI_PORT:-8080}..."
pip install -r /app/app/requirements.txt 2>/dev/null
python3 -m uvicorn app.main:app --host 0.0.0.0 --port ${TREFA_FASTAPI_PORT:-8080} &
FASTAPI_PID=$!
log "✅ FastAPI Orchestrator iniciado (PID: $FASTAPI_PID)"

wait -n $VLLM_PID $MCP_PID $FASTAPI_PID

