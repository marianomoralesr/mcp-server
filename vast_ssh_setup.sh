#!/bin/bash
# ============================================================
# TREFA — Provisionar máquina vast.ai via SSH
#
# Corre este script DESDE TU MAC. Se conecta por SSH a la
# máquina vast.ai, instala todo y deja los 3 servicios corriendo.
#
# Uso:
#   # 14B (1 GPU, ~48GB VRAM)
#   ./vast_ssh_setup.sh -h <vast-host> -p <ssh-port> -m 14b
#
#   # 32B (2+ GPUs, ~90GB VRAM)
#   ./vast_ssh_setup.sh -h <vast-host> -p <ssh-port> -m 32b
#
#   # Con .env local (se copian las vars automáticamente)
#   ./vast_ssh_setup.sh -h <vast-host> -p <ssh-port> -m 14b -e .env
#
#   # Usando el merged model de HF (salta descarga base + merge)
#   ./vast_ssh_setup.sh -h <vast-host> -p <ssh-port> -m 32b --merged
#
# Requisitos:
#   - SSH key configurada para vast.ai (o ssh-agent activo)
#   - Variables de entorno definidas (HF_TOKEN mínimo):
#       export HF_TOKEN=hf_xxx
#       export SUPABASE_URL=https://xxx.supabase.co
#       export SUPABASE_SERVICE_ROLE_KEY=eyJ...
#       export CF_TUNNEL_CRED='{"AccountTag":"...","TunnelSecret":"...","TunnelID":"..."}'
#     O usar -e para apuntar a un .env local
#
#   CF_TUNNEL_CRED activa el Cloudflare Tunnel (api.trefa.mx).
#   Sin ella, el tunnel se salta y solo se usan puertos locales.
# ============================================================

set -euo pipefail

# --- Defaults ---
SSH_HOST=""
SSH_PORT="22"
SSH_USER="root"
SSH_KEY=""
MODEL_SIZE="14b"
ENV_FILE=""
USE_MERGED=false
VLLM_PORT=8001
FASTAPI_PORT=8081
MCP_PORT=3001
GIT_REPO="https://github.com/marianomoralesr/mcp-server.git"

usage() {
    cat <<EOF
Uso: $0 -h <host> [opciones]

Opciones:
  -h, --host HOST        Host SSH de vast.ai (obligatorio)
  -p, --port PORT        Puerto SSH (default: 22)
  -u, --user USER        Usuario SSH (default: root)
  -k, --key KEY          Ruta a SSH key (default: usa ssh-agent)
  -m, --model SIZE       Tamaño del modelo: 14b o 32b (default: 14b)
  -e, --env FILE         Archivo .env local para copiar variables
  --merged               Descargar modelo pre-mergeado de HF (recomendado)
  --help                 Mostrar esta ayuda
EOF
    exit 1
}

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--host) SSH_HOST="$2"; shift 2 ;;
        -p|--port) SSH_PORT="$2"; shift 2 ;;
        -u|--user) SSH_USER="$2"; shift 2 ;;
        -k|--key)  SSH_KEY="$2"; shift 2 ;;
        -m|--model) MODEL_SIZE="$2"; shift 2 ;;
        -e|--env)  ENV_FILE="$2"; shift 2 ;;
        --merged)  USE_MERGED=true; shift ;;
        --help)    usage ;;
        *) echo "Opción desconocida: $1"; usage ;;
    esac
done

if [ -z "$SSH_HOST" ]; then
    echo "ERROR: -h/--host es obligatorio"
    usage
fi

# --- Cargar .env si se especificó ---
if [ -n "$ENV_FILE" ]; then
    if [ ! -f "$ENV_FILE" ]; then
        echo "ERROR: Archivo .env no encontrado: $ENV_FILE"
        exit 1
    fi
    echo "[local] Cargando variables desde $ENV_FILE"
    set -a
    source "$ENV_FILE"
    set +a
fi

# --- Validar HF_TOKEN ---
if [ -z "${HF_TOKEN:-}" ]; then
    echo "ERROR: HF_TOKEN no definido."
    echo "  export HF_TOKEN=hf_xxx   o   usa -e .env"
    exit 1
fi

# --- Construir SSH command ---
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
if [ -n "$SSH_KEY" ]; then
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi
SSH_CMD="ssh $SSH_OPTS -p $SSH_PORT $SSH_USER@$SSH_HOST"

echo "============================================"
echo "  TREFA vast.ai SSH Setup"
echo "  Host:   $SSH_USER@$SSH_HOST:$SSH_PORT"
echo "  Modelo: Qwen3-${MODEL_SIZE^^}"
echo "  Merged: $USE_MERGED"
echo "============================================"

# --- Configuración según modelo ---
if [ "$MODEL_SIZE" = "32b" ]; then
    BASE_REPO="Qwen/Qwen3-32B"
    LORA_REPO="mmoralesf/qwen3-32B-mariana"
    MERGED_REPO="mmoralesf/qwen3-32B-mariana"
    BASE_DIR="/app/modelos/qwen3-32b-base"
    LORA_DIR="/app/modelos/qwen3-32b-mariana"
    MERGED_DIR="/app/modelos/qwen3-32b-merged"
    SAFETENSOR_MIN_COUNT=10
    RAM_NOTE="~64GB RAM para merge"
    DISK_NOTE="~130GB disco (base 65GB + merge 65GB)"
else
    BASE_REPO="Qwen/Qwen3-14B"
    LORA_REPO="mmoralesf/qwen3-14B-mariana"
    MERGED_REPO="mmoralesf/qwen3-14B-mariana"
    BASE_DIR="/app/modelos/qwen3-14b-base"
    LORA_DIR="/app/modelos/qwen3-14b-mariana"
    MERGED_DIR="/app/modelos/qwen3-14b-merged"
    SAFETENSOR_MIN_COUNT=5
    RAM_NOTE="~28GB RAM para merge"
    DISK_NOTE="~56GB disco (base 28GB + merge 28GB)"
fi

# --- Test SSH connection ---
echo "[local] Probando conexión SSH..."
if ! $SSH_CMD "echo 'SSH OK'" 2>/dev/null; then
    echo "ERROR: No se pudo conectar a $SSH_USER@$SSH_HOST:$SSH_PORT"
    exit 1
fi
echo "[local] Conexión OK"

# --- Enviar variables de entorno ---
echo "[local] Enviando variables de entorno..."

ENV_VARS="export HF_TOKEN='${HF_TOKEN}'
export HF_HUB_ENABLE_HF_TRANSFER=1
export OPENBLAS_NUM_THREADS=1"

[ -n "${SUPABASE_URL:-}" ] && ENV_VARS="$ENV_VARS
export SUPABASE_URL='${SUPABASE_URL}'"
[ -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ] && ENV_VARS="$ENV_VARS
export SUPABASE_SERVICE_ROLE_KEY='${SUPABASE_SERVICE_ROLE_KEY}'"
[ -n "${GITHUB_TOKEN:-}" ] && ENV_VARS="$ENV_VARS
export GITHUB_TOKEN='${GITHUB_TOKEN}'"
[ -n "${CF_TUNNEL_CRED:-}" ] && ENV_VARS="$ENV_VARS
export CF_TUNNEL_CRED='${CF_TUNNEL_CRED}'"

$SSH_CMD "cat > /tmp/trefa_env.sh << 'ENVEOF'
${ENV_VARS}
ENVEOF
chmod 600 /tmp/trefa_env.sh"

# ============================================================
# Fase 1: Dependencias del sistema
# ============================================================
echo "[local] Fase 1/6: Instalando dependencias..."
$SSH_CMD "bash -s" << 'PHASE1'
set -euo pipefail
source /tmp/trefa_env.sh

log() { echo "[$(date +'%H:%M:%S')] $1"; }
log "=== Fase 1: Dependencias ==="

# Python deps
pip install -q vllm peft accelerate transformers huggingface_hub hf_transfer 2>&1 | tail -5
pip install -q httpx structlog pydantic-settings uvicorn fastapi python-multipart python-dotenv 2>&1 | tail -3

# Node.js 20+ (para MCP server)
NODE_VER=$(node -v 2>/dev/null | grep -oP '\d+' | head -1 || echo "0")
if [ "$NODE_VER" -lt 18 ]; then
    log "Instalando Node.js 20..."
    apt-get update -qq 2>&1 | tail -1
    apt-get remove -y nodejs npm libnode-dev libnode72 2>/dev/null || true
    dpkg --configure -a 2>/dev/null || true
    curl -fsSL https://deb.nodesource.com/setup_20.x 2>/dev/null | bash - 2>&1 | tail -3
    apt-get install -y nodejs 2>&1 | tail -3
    hash -r
fi

log "Python: $(python3 --version 2>/dev/null)"
log "Node:   $(node -v 2>/dev/null || echo N/A)"
log "vLLM:   $(pip show vllm 2>/dev/null | grep Version | cut -d' ' -f2 || echo N/A)"
log "Fase 1 OK"
PHASE1

# ============================================================
# Fase 2: Descargar modelo
# ============================================================
echo "[local] Fase 2/6: Descargando modelo (esto puede tardar)..."

if [ "$USE_MERGED" = true ]; then
    # Descargar modelo pre-mergeado directamente
    $SSH_CMD "bash -s" << PHASE2_MERGED
set -euo pipefail
source /tmp/trefa_env.sh

log() { echo "[\$(date +'%H:%M:%S')] \$1"; }
log "=== Fase 2: Descargar modelo mergeado ==="

mkdir -p /app/modelos

if [ ! -f "${MERGED_DIR}/config.json" ]; then
    log "Descargando modelo mergeado: ${MERGED_REPO}..."
    huggingface-cli download "${MERGED_REPO}" \\
        --local-dir "${MERGED_DIR}" \\
        --local-dir-use-symlinks False
    log "Descarga OK: \$(du -sh ${MERGED_DIR} | cut -f1)"
else
    log "Modelo mergeado ya existe: \$(du -sh ${MERGED_DIR} | cut -f1)"
fi

[ ! -f "${MERGED_DIR}/config.json" ] && { log "ERROR: modelo no descargado"; exit 1; }
log "Fase 2 OK"
PHASE2_MERGED

else
    # Descargar base + LoRA y mergear
    $SSH_CMD "bash -s" << PHASE2_MERGE
set -euo pipefail
source /tmp/trefa_env.sh

log() { echo "[\$(date +'%H:%M:%S')] \$1"; }
log "=== Fase 2: Descargar y mergear ==="
log "  ${DISK_NOTE}"
log "  ${RAM_NOTE}"

mkdir -p /app/modelos

if [ -f "${MERGED_DIR}/config.json" ]; then
    log "Modelo mergeado ya existe: \$(du -sh ${MERGED_DIR} | cut -f1)"
    log "Fase 2 OK (skip)"
    exit 0
fi

# Descargar base
if [ ! -f "${BASE_DIR}/config.json" ] || [ "\$(ls ${BASE_DIR}/model-*.safetensors 2>/dev/null | wc -l)" -lt ${SAFETENSOR_MIN_COUNT} ]; then
    log "Descargando base: ${BASE_REPO}..."
    rm -rf "${BASE_DIR}"
    huggingface-cli download "${BASE_REPO}" \\
        --local-dir "${BASE_DIR}" \\
        --local-dir-use-symlinks False
fi
log "Base: \$(du -sh ${BASE_DIR} | cut -f1)"

# Descargar LoRA
if [ ! -f "${LORA_DIR}/adapter_config.json" ]; then
    log "Descargando LoRA: ${LORA_REPO}..."
    huggingface-cli download "${LORA_REPO}" \\
        --local-dir "${LORA_DIR}" \\
        --local-dir-use-symlinks False
fi
log "LoRA: \$(du -sh ${LORA_DIR} | cut -f1)"

# Merge
log "Mergeando (${RAM_NOTE})..."
python3 -u << 'PYMERGE'
import torch, os
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
BASE = "${BASE_DIR}"
LORA = "${LORA_DIR}"
OUT  = "${MERGED_DIR}"
print("[merge] Tokenizer...")
tok = AutoTokenizer.from_pretrained(LORA, trust_remote_code=True, use_fast=True)
print("[merge] Base model (bf16, CPU)...")
base = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16, device_map="cpu", trust_remote_code=True)
print("[merge] LoRA adapter...")
model = PeftModel.from_pretrained(base, LORA)
print("[merge] Merge...")
model = model.merge_and_unload()
os.makedirs(OUT, exist_ok=True)
model.save_pretrained(OUT, safe_serialization=True)
tok.save_pretrained(OUT)
print("[merge] OK")
PYMERGE

[ ! -f "${MERGED_DIR}/config.json" ] && { log "ERROR: Merge falló"; exit 1; }
log "Merge OK: \$(du -sh ${MERGED_DIR} | cut -f1)"

# Liberar espacio borrando base
log "Liberando disco (borrando base)..."
rm -rf "${BASE_DIR}"

log "Fase 2 OK"
PHASE2_MERGE

fi

# ============================================================
# Fase 3: Clonar repos
# ============================================================
echo "[local] Fase 3/6: Clonando repositorios..."
$SSH_CMD "bash -s" << PHASE3
set -euo pipefail
source /tmp/trefa_env.sh

log() { echo "[\$(date +'%H:%M:%S')] \$1"; }
log "=== Fase 3: Repositorios ==="

if [ -n "\${GITHUB_TOKEN:-}" ]; then
    GIT_URL="https://\${GITHUB_TOKEN}@github.com/marianomoralesr/mcp-server.git"
else
    GIT_URL="${GIT_REPO}"
fi

# Inference server (branch inference-server)
if [ ! -d "/app/app" ]; then
    log "Clonando inference-server..."
    rm -rf /tmp/ir
    git clone -q -b inference-server "\$GIT_URL" /tmp/ir
    cp -r /tmp/ir/* /app/
    rm -rf /tmp/ir
else
    log "inference-server ya existe"
fi

# MCP server (branch main)
if [ ! -d "/app/mcp-server/.git" ]; then
    log "Clonando mcp-server..."
    rm -rf /app/mcp-server
    git clone -q -b main "\$GIT_URL" /app/mcp-server
else
    log "mcp-server ya existe"
fi

log "Fase 3 OK"
PHASE3

# ============================================================
# Fase 4: Build MCP + FastAPI deps
# ============================================================
echo "[local] Fase 4/6: Compilando MCP server e instalando deps..."
$SSH_CMD "bash -s" << PHASE4
set -euo pipefail
source /tmp/trefa_env.sh

log() { echo "[\$(date +'%H:%M:%S')] \$1"; }
log "=== Fase 4: Build ==="

# MCP Server
cd /app/mcp-server
npm install --silent 2>&1 | tail -3
npm run build 2>&1 | tail -3

# .env para MCP
if [ -n "\${SUPABASE_URL:-}" ] && [ -n "\${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
    printf "SUPABASE_URL=%s\nSUPABASE_SERVICE_ROLE_KEY=%s\nPORT=${MCP_PORT}\n" \\
        "\$SUPABASE_URL" "\$SUPABASE_SERVICE_ROLE_KEY" > /app/mcp-server/.env
    log "MCP .env generado"
else
    log "WARN: Supabase vars no definidas, MCP puede fallar"
fi

# FastAPI deps
pip install -q -r /app/app/requirements.txt 2>&1 | tail -3
mkdir -p /app/datasets /app/generated

log "Fase 4 OK"
PHASE4

# ============================================================
# Fase 5: Iniciar servicios
# ============================================================
echo "[local] Fase 5/6: Iniciando servicios..."
$SSH_CMD "bash -s" << PHASE5
set -euo pipefail
source /tmp/trefa_env.sh

log() { echo "[\$(date +'%H:%M:%S')] \$1"; }
log "=== Fase 5: Servicios ==="

# Matar procesos previos de TREFA (si los hay)
pkill -f "vllm.entrypoints" 2>/dev/null || true
pkill -f "uvicorn app.main" 2>/dev/null || true
pkill -f "npm run start:http" 2>/dev/null || true
sleep 2

# Desactivar vLLM de vast.ai (si existe)
[ -f /opt/supervisor-scripts/vllm.sh ] && {
    chmod -x /opt/supervisor-scripts/vllm.sh 2>/dev/null || true
    supervisorctl stop vllm 2>/dev/null || true
}

# Auto-detectar GPUs
GPU_COUNT=\$(python3 -c "import torch; print(torch.cuda.device_count())" 2>/dev/null || echo "1")
TENSOR_PARALLEL=\${TENSOR_PARALLEL:-\$GPU_COUNT}
log "GPUs: \$GPU_COUNT (TP=\$TENSOR_PARALLEL)"

# 1. MCP Server
log "Iniciando MCP Server (puerto ${MCP_PORT})..."
cd /app/mcp-server
nohup npm run start:http > /tmp/mcp-server.log 2>&1 &
MCP_PID=\$!
cd /app

# Esperar MCP
for i in \$(seq 1 15); do
    curl -sf "http://localhost:${MCP_PORT}/health" > /dev/null 2>&1 && { log "MCP Server listo"; break; }
    sleep 2
done

# 2. vLLM
log "Iniciando vLLM (puerto ${VLLM_PORT}, TP=\$TENSOR_PARALLEL)..."
python3 -m vllm.entrypoints.openai.api_server \
    --model "${MERGED_DIR}" \
    --served-model-name trefa-lora \
    --max-model-len 8192 \
    --dtype bfloat16 \
    --gpu-memory-utilization 0.90 \
    --tensor-parallel-size "\$TENSOR_PARALLEL" \
    --enforce-eager \
    --host 0.0.0.0 \
    --port ${VLLM_PORT} > /tmp/vllm.log 2>&1 &
VLLM_PID=\$!

log "Esperando vLLM (puede tardar 3-10 min)..."
for i in \$(seq 1 180); do
    curl -sf "http://localhost:${VLLM_PORT}/health" > /dev/null 2>&1 && { log "vLLM listo!"; break; }
    kill -0 "\$VLLM_PID" 2>/dev/null || { log "ERROR: vLLM crasheó"; tail -30 /tmp/vllm.log; exit 1; }
    [ \$((i % 12)) -eq 0 ] && log "  cargando... (\$((i*5))s)"
    sleep 5
done

# 3. FastAPI
log "Iniciando FastAPI (puerto ${FASTAPI_PORT})..."
TREFA_VLLM_PORT=${VLLM_PORT} \
TREFA_MCP_SERVER_URL="http://localhost:${MCP_PORT}" \
PYTHONPATH=/app \
python3 -m uvicorn app.main:app \
    --host 0.0.0.0 --port ${FASTAPI_PORT} > /tmp/fastapi.log 2>&1 &
FASTAPI_PID=\$!
sleep 3

# Guardar PIDs para referencia
echo "\$MCP_PID" > /tmp/trefa_mcp.pid
echo "\$VLLM_PID" > /tmp/trefa_vllm.pid
echo "\$FASTAPI_PID" > /tmp/trefa_fastapi.pid

log "=========================================="
log "  TREFA ACTIVO"
log "  MCP=\$MCP_PID :${MCP_PORT}"
log "  vLLM=\$VLLM_PID :${VLLM_PORT}"
log "  FastAPI=\$FASTAPI_PID :${FASTAPI_PORT}"
log "=========================================="
PHASE5

# ============================================================
# Fase 6: Cloudflare Tunnel (api.trefa.mx)
# ============================================================
echo "[local] Fase 6/7: Configurando Cloudflare Tunnel..."
$SSH_CMD "bash -s" << 'PHASE6'
set -euo pipefail
source /tmp/trefa_env.sh

log() { echo "[$(date +'%H:%M:%S')] $1"; }
log "=== Fase 6: Cloudflare Tunnel ==="

CF_TUNNEL_ID="d15177d1-cb8c-4ed9-b8de-ff52c8f3d749"
CF_TUNNEL_DOMAIN="api.trefa.mx"
CF_CRED_FILE="/root/.cloudflared/${CF_TUNNEL_ID}.json"

if [ -z "${CF_TUNNEL_CRED:-}" ]; then
    log "WARN: CF_TUNNEL_CRED no definido, saltando tunnel."
    log "  Para activar: export CF_TUNNEL_CRED='{...json...}'"
    exit 0
fi

# Instalar cloudflared
if ! command -v cloudflared &>/dev/null; then
    log "Instalando cloudflared..."
    curl -sL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
        -o /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
    log "cloudflared $(cloudflared --version 2>&1 | head -1)"
else
    log "cloudflared ya instalado: $(cloudflared --version 2>&1 | head -1)"
fi

# Limpiar config anterior
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1
rm -rf /root/.cloudflared
mkdir -p /root/.cloudflared

# Escribir credenciales
echo "$CF_TUNNEL_CRED" > "$CF_CRED_FILE"
chmod 600 "$CF_CRED_FILE"

# Escribir config — apunta a FastAPI (UI + OpenAI-compatible endpoints)
cat > /root/.cloudflared/config.yml << CFEOF
tunnel: ${CF_TUNNEL_ID}
credentials-file: ${CF_CRED_FILE}

ingress:
  - hostname: ${CF_TUNNEL_DOMAIN}
    service: http://localhost:${FASTAPI_PORT:-8081}
    originRequest:
      noTLSVerify: true
  - service: http_status:404
CFEOF

log "Config escrita: ${CF_TUNNEL_DOMAIN} → localhost:${FASTAPI_PORT:-8081}"

# Iniciar tunnel
nohup cloudflared tunnel run trefa-vllm > /tmp/cloudflared.log 2>&1 &
CF_PID=$!
echo "$CF_PID" > /tmp/trefa_cloudflared.pid

# Verificar que arrancó
sleep 3
if kill -0 "$CF_PID" 2>/dev/null; then
    log "Cloudflare Tunnel activo (PID=$CF_PID)"
    log "  https://${CF_TUNNEL_DOMAIN} → localhost:${FASTAPI_PORT:-8081}"
else
    log "ERROR: Cloudflare Tunnel no arrancó. Log:"
    tail -20 /tmp/cloudflared.log 2>/dev/null || true
fi

log "Fase 6 OK"
PHASE6

# ============================================================
# Fase 7: Verificación desde local
# ============================================================
echo "[local] Fase 7/7: Verificando servicios..."
sleep 2

echo "[local] Health check via SSH..."
HEALTH=$($SSH_CMD "curl -sf http://localhost:${FASTAPI_PORT}/health 2>/dev/null" || echo '{"status":"unreachable"}')
echo "[local] /health: $HEALTH"

MODEL_CHECK=$($SSH_CMD "curl -sf http://localhost:${VLLM_PORT}/v1/models 2>/dev/null | python3 -c \"import sys,json; print(json.load(sys.stdin)['data'][0]['id'])\"" 2>/dev/null || echo "N/A")
echo "[local] Modelo cargado: $MODEL_CHECK"

echo ""
echo "============================================"
echo "  TREFA desplegado en $SSH_HOST"
echo ""
echo "  Tunnel fijo:"
echo "    https://api.trefa.mx/ui      (Chat UI)"
echo "    https://api.trefa.mx/health   (Health check)"
echo ""
echo "  Para conectarte:"
echo "    ssh $SSH_OPTS -p $SSH_PORT $SSH_USER@$SSH_HOST"
echo ""
echo "  Port forwarding (alternativa sin tunnel):"
echo "    ssh $SSH_OPTS -p $SSH_PORT -L ${FASTAPI_PORT}:localhost:${FASTAPI_PORT} -L ${VLLM_PORT}:localhost:${VLLM_PORT} $SSH_USER@$SSH_HOST"
echo ""
echo "  Endpoints locales:"
echo "    FastAPI:  http://localhost:${FASTAPI_PORT}/health"
echo "    vLLM:     http://localhost:${VLLM_PORT}/v1/models"
echo "    UI:       http://localhost:${FASTAPI_PORT}/ui"
echo ""
echo "  Logs remotos:"
echo "    ssh ... 'tail -f /tmp/vllm.log'"
echo "    ssh ... 'tail -f /tmp/fastapi.log'"
echo "    ssh ... 'tail -f /tmp/mcp-server.log'"
echo "    ssh ... 'tail -f /tmp/cloudflared.log'"
echo ""
echo "  Detener servicios:"
echo "    ssh ... 'kill \$(cat /tmp/trefa_vllm.pid) \$(cat /tmp/trefa_fastapi.pid) \$(cat /tmp/trefa_mcp.pid) \$(cat /tmp/trefa_cloudflared.pid 2>/dev/null)'"
echo "============================================"
