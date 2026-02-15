#!/bin/bash
# ============================================================
# TREFA - Railway Entrypoint (sin vLLM)
# Inicia MCP Server + FastAPI solamente
# ============================================================

set -euo pipefail

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"; }

# ============================================================
# Cleanup trap
# ============================================================
MCP_PID=""
FASTAPI_PID=""

cleanup() {
    log "Deteniendo servicios..."
    [ -n "$FASTAPI_PID" ] && kill "$FASTAPI_PID" 2>/dev/null || true
    [ -n "$MCP_PID" ] && kill "$MCP_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    log "Servicios detenidos."
}

trap cleanup EXIT INT TERM

# ============================================================
# Health check reutilizable
# ============================================================
wait_for_service() {
    local name="$1" url="$2" max_retries="$3" interval="$4"
    log "Esperando a $name..."
    for i in $(seq 1 "$max_retries"); do
        if curl -sf "$url" > /dev/null 2>&1; then
            log "$name listo."
            return 0
        fi
        sleep "$interval"
    done
    log "WARN: $name no respondió después de $max_retries intentos."
    return 1
}

# ============================================================
# 1. Generar .env para MCP Server
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
else
    log "WARN: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no definidos."
    log "  MCP tools que acceden a Supabase no funcionarán."
fi

# ============================================================
# 2. Indicar modo sin vLLM
# ============================================================
export TREFA_VLLM_DISABLED=true
log "Modo Railway: vLLM deshabilitado, solo UI + tools + datasets"

# ============================================================
# 3. Iniciar MCP Server
# ============================================================
MCP_PORT="${MCP_PORT:-3001}"
log "Iniciando MCP Server en puerto $MCP_PORT..."
cd /app/mcp-server
nohup npm run start:http > /var/log/mcp-server.log 2>&1 &
MCP_PID=$!
cd /app

wait_for_service "MCP Server" "http://localhost:${MCP_PORT}/health" 20 2 || true

# ============================================================
# 4. Iniciar FastAPI
# ============================================================
FASTAPI_PORT="${PORT:-${TREFA_FASTAPI_PORT:-8080}}"
log "Iniciando FastAPI en puerto $FASTAPI_PORT..."
python3 -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$FASTAPI_PORT" > /var/log/fastapi.log 2>&1 &
FASTAPI_PID=$!

if ! wait_for_service "FastAPI" "http://localhost:${FASTAPI_PORT}/health" 15 2; then
    log "ERROR: FastAPI no arrancó."
    tail -30 /var/log/fastapi.log 2>/dev/null || true
    exit 1
fi

# ============================================================
# 5. Monitor
# ============================================================
log "Servicios activos:"
log "  MCP Server  PID=$MCP_PID  (puerto $MCP_PORT)"
log "  FastAPI     PID=$FASTAPI_PID  (puerto $FASTAPI_PORT)"
log "  UI:         http://localhost:${FASTAPI_PORT}/ui"
log "  Docs:       http://localhost:${FASTAPI_PORT}/docs"

wait -n "$MCP_PID" "$FASTAPI_PID" 2>/dev/null
EXIT_CODE=$?

for proc_info in "MCP:$MCP_PID" "FastAPI:$FASTAPI_PID"; do
    name="${proc_info%%:*}"
    pid="${proc_info##*:}"
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        log "ALERTA: $name (PID $pid) terminó."
    fi
done

exit "$EXIT_CODE"
