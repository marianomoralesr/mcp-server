# ÍNDICE DE ARCHIVOS - DATASET MEJORADO MARIANA

## Archivo Principal de Salida ⭐

**gold_upgraded_haiku_600_649.jsonl**
- Conversaciones: 50 (índices 600-649, líneas 629-678 originales)
- Tamaño: 256 KB
- Líneas: 50 (una por conversación)
- Formato: JSONL
- Estado: LISTO PARA FINE-TUNING

## Documentación de Referencia

### README.md (3.9 KB)
- Descripción técnica completa
- Reglas aplicadas con ejemplos
- Estadísticas de calidad
- Instrucciones de uso
- Validaciones realizadas

### RESUMEN_MEJORAS.txt (5.0 KB)
- Resumen ejecutivo
- Mejoras principales
- Estadísticas de calidad
- Validaciones completadas
- Archivos generados

### EJEMPLOS.md (2.3 KB)
- Ejemplos de conversaciones mejoradas
- Estructura JSON de muestras
- Patrones de diálogo

## Otros Archivos en el Directorio

(Anteriores procesadas)
- gold_upgraded_batch_001.jsonl (50 convs)
- gold_upgraded_batch_002.jsonl (50 convs)
- gold_upgraded_batch_003.jsonl (50 convs)
- gold_upgraded_batch_004.jsonl (50 convs)
- gold_upgraded_batch_005.jsonl (50 convs)
- gold_upgraded_batch_015.jsonl (50 convs)
- gold_upgraded_haiku_500_549.jsonl (50 convs)
- gold_upgraded_haiku_550_599.jsonl (50 convs)
- gold_upgraded_haiku_650_699.jsonl (50 convs)

## Instrucciones de Uso

### Para Fine-Tuning
```bash
# Usar directamente con el dataset
python fine_tune.py --data gold_upgraded_haiku_600_649.jsonl --model claude-haiku-4.5
```

### Para Validación
```bash
# Verificar integridad del JSON
python -m json.tool gold_upgraded_haiku_600_649.jsonl > /dev/null && echo "✓ JSON válido"

# Contar conversaciones
wc -l gold_upgraded_haiku_600_649.jsonl
```

### Para Análisis
```python
import json
with open('gold_upgraded_haiku_600_649.jsonl') as f:
    for line in f:
        conv = json.loads(line)
        # Procesar conversación
```

## Métricas Finales

| Métrica | Valor |
|---------|-------|
| **Conversaciones procesadas** | 50 |
| **JSON válido** | 100% (50/50) |
| **System prompts correctos** | 100% (50/50) |
| **Identificaciones "Mariana"** | 51+ |
| **Tool calls** | 147 |
| **Tamaño archivo** | 256 KB |
| **Promedio msgs/conv** | 17.9 |

## Cambios Principales

✓ System prompt → `__SYSTEM_PROMPT__`
✓ TREFABOT → "Mariana de Autos TREFA"
✓ Emojis normalizados (máx 3, predomina :))
✓ Precios → $XXX,XXX MXN
✓ Campos normalizados (autoano)
✓ URLs formateadas
✓ JSON validado
✓ 100% compatible con fine-tuning

## Próximos Pasos

1. Validar con suite de benchmarks
2. Fine-tuning con Claude o modelo similar
3. A/B testing contra baseline
4. Deployment en producción
5. Monitoreo de calidad

---
Ruta: /Users/marianomorales/Downloads/fine-tuning/inference/datasets/gold_upgraded/
Creado: 2026-02-16
Procesado por: Claude Haiku 4.5
