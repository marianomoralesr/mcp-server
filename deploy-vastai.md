# Guia de Despliegue — TREFA Inference Server en Vast.ai

## Requisitos previos

- Cuenta en [Vast.ai](https://vast.ai) con credito cargado
- Token de HuggingFace con acceso al repo `AutosTREFA/trefa-mariana-qwen3-14b`
- Credenciales de Supabase (URL + Service Role Key)
- GPU con al menos 32GB de VRAM (RTX 5090, A6000, A100 o H100)

## Variables de entorno

Configura estas variables en la seccion "Environment Variables" de Vast.ai:

| Variable | Requerida | Descripcion |
|----------|-----------|-------------|
| `HF_TOKEN` | Si | Token de HuggingFace |
| `SUPABASE_URL` | Si | URL de tu proyecto Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Si | Service role key de Supabase |
| `TREFA_API_KEY` | No | API key para autenticar requests a FastAPI |
| `MCP_API_KEY` | No | API key para el MCP Server |
| `MAX_MODEL_LEN` | No | Override de contexto maximo (auto-detectado) |
| `GPU_MEMORY_UTILIZATION` | No | Override de uso de VRAM (auto-detectado) |
| `DTYPE` | No | Tipo de dato, default: `half` |

## Metodo 1: Script on-start (recomendado para empezar)

Este metodo instala todo desde cero en la instancia. Tarda ~15-20 min la primera vez pero no requiere imagen Docker previa.

### Pasos

1. En Vast.ai, busca una instancia con la imagen `nvidia/cuda:12.4.1-devel-ubuntu22.04`
2. Selecciona una GPU compatible (ver tabla abajo)
3. En **Environment Variables**, agrega:
   ```
   HF_TOKEN=hf_tu_token
   SUPABASE_URL=https://tu-proyecto.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=eyJ...
   ```
4. En **On Start Script**, pega el contenido completo de `vast_on_start.sh`
5. Lanza la instancia

### Que hace el script

1. Valida que las variables de entorno esten definidas
2. Detecta la GPU y configura parametros optimos
3. Instala dependencias del sistema (git, Node.js 20, Python)
4. Instala PyTorch + vLLM + dependencias
5. Clona los repositorios necesarios
6. Configura y compila el MCP Server
7. Descarga el modelo GGUF (~14GB)
8. Inicia los 3 servicios con health checks entre cada uno
9. Monitorea los procesos y limpia al salir

## Metodo 2: Docker pre-construido (mas rapido)

Construye la imagen una vez y reutilizala. El arranque tarda solo ~5 min (descarga de modelo).

### Construir la imagen

```bash
docker build -t trefa-inference .
```

### Subir a un registry (opcional)

```bash
docker tag trefa-inference tu-usuario/trefa-inference:latest
docker push tu-usuario/trefa-inference:latest
```

### Usar en Vast.ai

1. En **Docker Image**, pon `tu-usuario/trefa-inference:latest`
2. En **Environment Variables**, agrega las 3 variables requeridas
3. Lanza la instancia

El `entrypoint.sh` se ejecuta automaticamente y maneja todo.

## Comparativa de GPUs

| GPU | VRAM | MAX_MODEL_LEN | GPU_MEM | Precio aprox. | Nota |
|-----|------|---------------|---------|---------------|------|
| RTX 5090 | 32GB | 8192 | 0.88 | ~$0.40-0.60/hr | Buena relacion costo/rendimiento |
| A6000 | 48GB | 16384 | 0.90 | ~$0.30-0.50/hr | Mas contexto, buena para produccion |
| A100 40GB | 40GB | 32768 | 0.92 | ~$0.80-1.20/hr | Alto rendimiento |
| A100 80GB | 80GB | 32768 | 0.92 | ~$1.50-2.00/hr | Maximo contexto |
| H100 | 80GB | 32768 | 0.92 | ~$2.00-3.50/hr | Maxima velocidad |

Todos los valores se auto-detectan. Puedes sobreescribirlos con `MAX_MODEL_LEN` y `GPU_MEMORY_UTILIZATION`.

## Verificacion

Una vez que la instancia este corriendo, verifica los 3 servicios:

### Health check general
```bash
curl http://<IP>:8080/health
```

### Listar herramientas disponibles (MCP)
```bash
curl http://<IP>:8080/v1/tools
```

### Hacer una consulta de chat
```bash
curl -X POST http://<IP>:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hola, que puedes hacer?"}]
  }'
```

### Ver logs desde SSH
```bash
# Log del setup (solo vast_on_start.sh)
cat /var/log/trefa-setup.log

# Logs individuales
tail -f /var/log/mcp-server.log
tail -f /var/log/vllm.log
tail -f /var/log/fastapi.log
```

## Problemas comunes

### CUDA Out of Memory (OOM)
**Sintoma:** vLLM no arranca o muere al cargar el modelo.
**Solucion:** Reduce `MAX_MODEL_LEN` y `GPU_MEMORY_UTILIZATION`:
```
MAX_MODEL_LEN=4096
GPU_MEMORY_UTILIZATION=0.80
```

### El modelo no se descarga
**Sintoma:** Error "401 Unauthorized" o "Repository not found".
**Solucion:**
- Verifica que `HF_TOKEN` este bien definido
- Confirma que el token tiene acceso al repo `AutosTREFA/trefa-mariana-qwen3-14b`
- Si el repo es privado, el token necesita permisos de lectura

### MCP Server no conecta
**Sintoma:** `/v1/tools` devuelve error o lista vacia.
**Solucion:**
- Revisa que `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` esten definidos
- Verifica el log: `cat /var/log/mcp-server.log`
- Confirma que el MCP Server esta corriendo: `curl http://localhost:3001/health`

### vLLM tarda mucho en arrancar
**Sintoma:** Health check falla despues de 6 minutos.
**Solucion:** Es normal que vLLM tarde 2-5 minutos en cargar un modelo de 14B. Si supera los 6 min:
- Verifica que hay suficiente VRAM con `nvidia-smi`
- Revisa `/var/log/vllm.log` para errores especificos

### Puertos no accesibles
**Sintoma:** No puedes conectar desde fuera.
**Solucion:**
- En Vast.ai, asegurate de exponer el puerto 8080 (o el que uses)
- Usa la IP publica y el puerto mapeado que muestra Vast.ai

## Costos estimados

| Uso | GPU recomendada | Costo aprox. |
|-----|----------------|--------------|
| Desarrollo/pruebas | RTX 5090 | $0.40-0.60/hr |
| Produccion ligera | A6000 | $0.30-0.50/hr |
| Produccion alta demanda | A100 | $0.80-1.50/hr |

Recuerda apagar la instancia cuando no la uses para evitar cargos innecesarios.
