# ============================================================
# TREFA Inference Server - Dockerfile
# Base: NVIDIA CUDA 12.4.1 (RTX 5090 / A6000 / A100)
# ============================================================

FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ============================================================
# Dependencias del sistema + Node.js
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    git \
    curl \
    wget \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/bin/python3 /usr/bin/python
RUN pip install --upgrade pip

# ============================================================
# PyTorch + vLLM + utilidades HF
# ============================================================
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
RUN pip install vllm>=0.6.0
RUN pip install huggingface_hub hf_transfer datasets accelerate

# ============================================================
# MCP Server (Node.js)
# ============================================================
WORKDIR /app/mcp-server

COPY mcp-server/package*.json ./
RUN npm install

COPY mcp-server/ .
RUN npm run build
RUN npm prune --production

# ============================================================
# FastAPI app — requirements primero (cache de capas)
# ============================================================
WORKDIR /app

COPY app/requirements.txt /app/app/requirements.txt
RUN pip install -r /app/app/requirements.txt

COPY app/ /app/app/

# ============================================================
# Directorios y entrypoint
# ============================================================
RUN mkdir -p /app/datasets /app/generated /app/modelos /var/log

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# ============================================================
# Configuración de entorno
# ============================================================
ENV HF_HUB_ENABLE_HF_TRANSFER=1

EXPOSE 8080 8000 3001

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
