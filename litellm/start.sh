#!/bin/sh
exec litellm --config /app/config.yaml --port ${PORT:-4000} --host 0.0.0.0
