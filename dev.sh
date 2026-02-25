#!/bin/bash
# ============================================================
# TREFA — Script de desarrollo local
# Inicia MCP Server + FastAPI Orchestrator (sin vLLM/GPU)
#
# Uso:
#   chmod +x dev.sh
#   ./dev.sh
#
# Requisitos:
#   - Node.js >= 18
#   - Python 3.10+
#   - mcp-server/.env configurado (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FASTAPI_PORT="${TREFA_FASTAPI_PORT:-8080}"
MCP_PORT="${PORT:-3001}"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[TREFA]${NC} $1"; }
warn() { echo -e "${YELLOW}[TREFA]${NC} $1"; }
err()  { echo -e "${RED}[TREFA]${NC} $1"; }

cleanup() {
    log "Deteniendo servicios..."
    [ -n "$MCP_PID" ]     && kill "$MCP_PID"     2>/dev/null
    [ -n "$FASTAPI_PID" ] && kill "$FASTAPI_PID" 2>/dev/null
    wait 2>/dev/null
    log "Listo."
}
trap cleanup EXIT INT TERM

# -------------------------------------------------------
# 1. Verificar dependencias
# -------------------------------------------------------
log "Verificando dependencias..."

if ! command -v node &>/dev/null; then
    err "Node.js no encontrado. Instálalo: https://nodejs.org"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    err "Python 3 no encontrado."
    exit 1
fi

# -------------------------------------------------------
# 2. Instalar dependencias MCP (si faltan)
# -------------------------------------------------------
if [ ! -d "$SCRIPT_DIR/mcp-server/node_modules" ]; then
    log "Instalando dependencias del MCP Server..."
    cd "$SCRIPT_DIR/mcp-server"
    npm install
    cd "$SCRIPT_DIR"
fi

# -------------------------------------------------------
# 3. Verificar .env del MCP Server
# -------------------------------------------------------
if [ ! -f "$SCRIPT_DIR/mcp-server/.env" ]; then
    err "Falta mcp-server/.env — copia .env.example y configura SUPABASE_SERVICE_ROLE_KEY"
    exit 1
fi

# -------------------------------------------------------
# 4. Instalar dependencias Python (si faltan)
# -------------------------------------------------------
python3 -c "import fastapi" 2>/dev/null || {
    log "Instalando dependencias Python..."
    pip3 install --break-system-packages -r "$SCRIPT_DIR/app/requirements.txt" 2>/dev/null \
        || pip3 install -r "$SCRIPT_DIR/app/requirements.txt"
}

# -------------------------------------------------------
# 5. Iniciar MCP Server
# -------------------------------------------------------
log "Iniciando MCP Server en :$MCP_PORT..."
cd "$SCRIPT_DIR/mcp-server"
npx tsx src/http-server.ts &
MCP_PID=$!
cd "$SCRIPT_DIR"

# Esperar a que MCP esté listo
for i in $(seq 1 15); do
    if curl -s "http://localhost:$MCP_PORT/health" >/dev/null 2>&1; then
        log "MCP Server listo (PID: $MCP_PID)"
        break
    fi
    [ "$i" -eq 15 ] && { err "MCP Server no respondió en 15s"; exit 1; }
    sleep 1
done

# -------------------------------------------------------
# 6. Iniciar FastAPI
# -------------------------------------------------------
log "Iniciando FastAPI Orchestrator en :$FASTAPI_PORT..."
cd "$SCRIPT_DIR"
python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$FASTAPI_PORT" --reload &
FASTAPI_PID=$!

# Esperar a que FastAPI esté listo
for i in $(seq 1 15); do
    if curl -s "http://localhost:$FASTAPI_PORT/health" >/dev/null 2>&1; then
        log "FastAPI Orchestrator listo (PID: $FASTAPI_PID)"
        break
    fi
    [ "$i" -eq 15 ] && { warn "FastAPI tardó en responder — puede seguir cargando"; }
    sleep 1
done

# -------------------------------------------------------
# 7. Resumen
# -------------------------------------------------------
echo ""
log "=========================================="
log "  TREFA Dev Server — todo listo"
log "=========================================="
echo ""
echo "  FastAPI:    http://localhost:$FASTAPI_PORT"
echo "  Swagger:    http://localhost:$FASTAPI_PORT/docs"
echo "  MCP Server: http://localhost:$MCP_PORT"
echo "  Health:     http://localhost:$FASTAPI_PORT/health"
echo "  Metrics:    http://localhost:$FASTAPI_PORT/metrics"
echo ""
echo "  Endpoints principales:"
echo "    POST /v1/chat              — Chat con tool calling"
echo "    GET  /v1/tools             — Listar herramientas"
echo "    POST /v1/tools/{nombre}    — Ejecutar herramienta"
echo "    GET  /v1/sessions/{id}     — Ver historial de sesión"
echo "    POST /v1/feedback          — Enviar calificación"
echo "    GET  /admin/analytics      — Dashboard analítico"
echo ""
warn "vLLM no está activo — /v1/chat devolverá error 502."
warn "Para chat completo, usa Docker con GPU."
echo ""
log "Presiona Ctrl+C para detener."

# Esperar a que terminen
wait
