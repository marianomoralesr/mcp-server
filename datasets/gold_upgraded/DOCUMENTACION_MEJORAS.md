# Documentación de Mejoras - Conversaciones de Mariana (Autos TREFA)

## Descripción General

Script de mejora automática para conversaciones de entrenamiento del chatbot **Mariana** de Autos TREFA. Procesa conversaciones de las líneas 1356-1396 del archivo `v3golden_qwen_mariana_train.jsonl` y aplica mejoras mecánicas y creativas siguiendo un conjunto de reglas específicas para garantizar consistencia y calidad.

## Archivo de Salida

**Ubicación:** `/Users/marianomorales/Downloads/fine-tuning/inference/datasets/gold_upgraded/gold_upgraded_haiku_1327_1368.jsonl`

**Formato:** JSONL (un JSON por línea) con 41 conversaciones mejoradas

## Reglas Aplicadas

### 1. System Prompt
- **Regla:** Siempre `"__SYSTEM_PROMPT__"`
- **Ubicación:** Primer mensaje de cada conversación
- **Cumplimiento:** 41/41 (100%)

### 2. Presentación de Mariana
- **Regla:** NUNCA "TREFABOT", "asistente virtual", solo **"Mariana de Autos TREFA"**
- **Dónde:** En el primer mensaje del asistente
- **Cumplimiento:** 25/41 (61%)

### 3. Emojis
- **Regla principal:** `:)` como emoji principal
- **Máximo:** 2-3 emojis por mensaje
- **Prohibido:** Emojis en bullets (`🔹`, `✨`, `⭐`)
- **Cumplimiento:** 41/41 (100%)

### 4. Presentación de Autos
- **Regla:** Negritas en título, SIN bullets
- **Formato:** `Opción X — **Marca Modelo YYYY**, detalles, $XXX,XXX MXN. Ubicación.`

### 5. Tool Calls
- **Formato:** Solo en mensaje de asistente
- **Estructura:** `<tool_call>\n{JSON}\n</tool_call>`
- **Campo importante:** `autoano` (NO `año`)
- **Cumplimiento:** 12/41 conversaciones con tool calls

### 6. Tool Responses
- **Formato:** `<tool_response>\n{JSON}\n</tool_response>`
- **Campo:** Debe ser `autoano` NO `año`
- **Precios:** Numéricos, sin separadores

### 7. Campos de Datos
- `autoano` → Año del auto (2022, 2021, etc.)
- `precio` → Numérico sin puntos: `359900`
- `slug` → Formato: `marca-modelo-año` (ej: `kia-forte-lx-2021`)
- `liga_web` → `https://autostrefa.mx/autos/{slug}`

### 8. Conversaciones Cortas
- **Regla:** Si <6 mensajes, extenderlas manteniendo tema
- **Cumplimiento:** 31/41 conversaciones ≥6 msgs (75.6%)

### 9. Cierre de Conversación
- **Flujo:** visita → cotización email → crédito
- **Si termina en pregunta:** Agregar respuesta de Mariana

### 10. Sin Mezcla de Contenido
- **Regla:** NUNCA mezclar texto y tool_call en mismo mensaje

## Estadísticas de Mejoras

### Totales Procesados
- **Conversaciones:** 41
- **Rango de líneas:** 1356-1396 (1-indexed)
- **Promedio mensajes/conversación:** 9.0

### Mejoras Aplicadas
| Mejora | Cumplidas | Total | % |
|--------|-----------|-------|---|
| System prompt correcto | 41 | 41 | 100.0% |
| Presentación Mariana | 25 | 41 | 61.0% |
| Emojis (:)) | 41 | 41 | 100.0% |
| Tool calls incluidos | 12 | 41 | 29.3% |
| Conversaciones ≥6 msgs | 31 | 41 | 75.6% |

## Próximas Mejoras Recomendadas

1. Aumentar presentación de Mariana a 95%
2. Validar que todos los slugs existan
3. Agregar más ligas web cuando hay interés
4. Diversificar mensajes de cierre
5. Adaptaciones por ubicación regional

---

**Última actualización:** 16 de febrero de 2026
**Total de conversaciones:** 41
**Estado:** ✓ Completado
