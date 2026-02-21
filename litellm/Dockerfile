FROM ghcr.io/berriai/litellm:main-stable

COPY config.yaml /app/config.yaml
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENTRYPOINT ["/app/start.sh"]
