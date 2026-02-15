#!/bin/bash
# ============================================================
# TREFA - Vast.ai On-Start Script (Automated Setup)
# Copy this content into the "On Start Script" field in Vast.ai
# Base Image recommended: nvidia/cuda:12.4.1-devel-ubuntu22.04
# ============================================================

set -e
LOG_FILE="/root/on_start.log"

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "🚀 Starting Trefa Automated Setup on Boot..."

# 1. System Dependencies and Node.js
log "📦 Installing system dependencies and Node.js..."
apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-dev git wget curl ca-certificates

# Install Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
ln -sf /usr/bin/python3 /usr/bin/python

# 2. Python Dependencies (vLLM for RTX 5090 / CUDA 12.4)
log "🐍 Installing Python libraries (this may take a few minutes)..."
pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install vllm>=0.6.0
pip install huggingface_hub hf_transfer datasets accelerate

# 3. Setup Directories
log "📂 Creating directory structure..."
mkdir -p /app/Entrenamiento
mkdir -p /app/Conversaciones
mkdir -p /app/modelos
mkdir -p /app/mcp-server

# 4. MCP Server Setup (Manual Step if not in Docker Image)
log "⚠️ To run MCP Server, you must copy/clone the code to /app/mcp-server manually or via git clone hereunder"
# Uncomment and replace with your repo if available:
# git clone https://github.com/your/repo.git /app/mcp-server
# cd /app/mcp-server && npm install

# 5. Environment Variables
export HF_HUB_ENABLE_HF_TRANSFER=1
echo "export HF_HUB_ENABLE_HF_TRANSFER=1" >> ~/.bashrc
export HF_TOKEN="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"
echo "export HF_TOKEN=\"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\"" >> ~/.bashrc

# 6. Download Model GGUF (AutosTREFA/trefa-mariana-qwen3-14b)
log "📥 Downloading GGUF Model..."
REPO_ID="AutosTREFA/trefa-mariana-qwen3-14b"
FILENAME="trefa-mariana-qwen3-14b-q8_0_gguf/qwen3-14b.Q8_0.gguf"
MODEL_DIR="/app/modelos/qwen3-14b-gguf"
mkdir -p "$MODEL_DIR"

# Note: Requires HF_TOKEN if repo is private
huggingface-cli download "$REPO_ID" "$FILENAME" --local-dir "$MODEL_DIR" --local-dir-use-symlinks False || log "⚠️ Failed to download model. Check HF_TOKEN."

log "✅ Setup Complete! Ready for inference."
log "💡 Run vLLM: python3 -m vllm.entrypoints.openai.api_server --model $MODEL_DIR/$(basename "$FILENAME") --tokenizer $REPO_ID --dtype half ..."
log "💡 Run MCP: cd /app/mcp-server && npm run start:http"

