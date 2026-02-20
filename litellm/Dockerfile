# ============================================================
# LiteLLM Proxy — Dockerfile para Railway
# Basado en imagen oficial + config personalizado
# ============================================================

FROM ghcr.io/berriai/litellm:main-v1.81.0-stable

# Copiar config personalizado
COPY config.yaml /app/config.yaml

# Puerto por defecto de LiteLLM (Railway inyecta $PORT)
EXPOSE 4000
