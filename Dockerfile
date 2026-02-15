
# ============================================================
# TREFA - Dockerfile para Inferencia con vLLM en RTX 5090
# Base: NVIDIA CUDA 12.4.1 (para compatibilidad con RTX 5090 / Blackwell)
# ============================================================

FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

# Configuración de entorno
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV VLLM_VERSION=0.6.3
# Opcional: Para evitar problemas de memoria compartida en contenedores
ENV SHM_SIZE=16g

# ============================================================
# Instalación de Dependencias del Sistema y Node.js
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

# Crear alias para python
RUN ln -s /usr/bin/python3 /usr/bin/python

# Actualizar pip
RUN pip install --upgrade pip

# ============================================================
# Instalación de vLLM y Dependencias de Inferencia
# ============================================================
# Instalamos PyTorch compatible con CUDA 12.4
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Instalamos vLLM (Versión compatible con CUDA 12.4)
RUN pip install vllm>=0.6.0

# Instalamos otras utilidades útiles para manejo de modelos
RUN pip install huggingface_hub hf_transfer datasets accelerate

# ============================================================
# Configuración del MCP Server (Node.js)
# ============================================================
WORKDIR /app/mcp-server

# Copiamos primero package.json para aprovechar cache de Docker
COPY mcp-server/package*.json ./
RUN npm install

# Copiamos el resto del código y construimos
COPY mcp-server/ .
# Si el proyecto requiere build, descomentar:
RUN npm run build

# ============================================================
# Configuración Final y Directorios
# ============================================================
WORKDIR /app

# Crear directorios esperados
RUN mkdir -p /app/Entrenamiento
RUN mkdir -p /app/Conversaciones
RUN mkdir -p /app/modelos

# Copiar FastAPI app e instalar dependencias Python
COPY app/ /app/app/
RUN pip install -r /app/app/requirements.txt

# Copiar script de entrada
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Variable de entorno para optimizar descargas de HF
ENV HF_HUB_ENABLE_HF_TRANSFER=1

# Exponer puertos: 8080 (FastAPI), 8000 (vLLM interno), 3001 (MCP Server)
EXPOSE 8080 8000 3001

# Punto de entrada (Debe ir al FINAL)
ENTRYPOINT ["/app/entrypoint.sh"]
