# Reporte de Evaluación Exhaustiva: merged_v10_together_train.jsonl

**Fecha:** 2026-02-19
**Archivo:** `merged_v10_together_train.jsonl`
**Tamaño:** 39 MB | **Conversaciones:** 2,543 | **Mensajes totales:** ~22,000
**Propósito:** Fine-tuning para Qwen 3 (vía Together AI)

---

## Resumen Ejecutivo

El dataset tiene **problemas estructurales bloqueantes** que impiden su uso directo para fine-tuning en Together AI con formato Qwen 3. El 100% de las conversaciones usan un formato de tool calling incompatible (`<tool_call>` markup en content) en lugar del formato estándar OpenAI (`tool_calls` field). Además hay problemas serios de consistencia de datos, alucinaciones y problemas de calidad conversacional.

### Tabla de Hallazgos por Prioridad

| # | Hallazgo | Impacto | Conversaciones Afectadas | Prioridad |
|---|----------|---------|--------------------------|-----------|
| F1 | Formato tool_call incorrecto (markup vs JSON) | El modelo NO aprenderá tool calling | 2,543 (100%) | **BLOQUEANTE** |
| F2 | Campo `tools` ausente en cada conversación | Together no sabe qué herramientas hay | 2,543 (100%) | **BLOQUEANTE** |
| F3 | `tool_call_id` faltante en tool messages | Rompe la cadena call→response | 4,335 msgs (100%) | **BLOQUEANTE** |
| F4 | `<tool_response>` wrapper en tool content | JSON malformado dentro del content | 4,298 msgs (99%) | **BLOQUEANTE** |
| F5 | System prompt contiene `<tool_call>` ejemplo | Se fine-tunea con template placeholder | 2,543 (100%) | **BLOQUEANTE** |
| F6 | Dos versiones de system prompt / esquema tools | Argumentos incompatibles entre versiones | 2,543 (100%) | **CRÍTICO** |
| F7 | Texto mezclado con `<tool_call>` en content | El modelo mezclaría prosa con JSON | 443 msgs | **CRÍTICO** |
| F8 | Sin saludo antes de tool call | Modelo ejecutaría tools sin presentarse | 835 convs (33%) | **CRÍTICO** |
| F9 | URLs faltantes donde se prometieron | "Aquí puedes verlo:" sin enlace | 550 msgs | **CRÍTICO** |
| F10 | Datos de financiamiento sin tool call | Modelo aprendería a inventar números | 692 msgs | **CRÍTICO** |
| F11 | `calcular_financiamiento` args incorrectos | 100% usa args no definidos en schema v1 | 260 calls (100%) | **CRÍTICO** |
| F12 | `buscar_alternativas` args incorrectos | 99% usa args no definidos en schema v1 | 74/75 calls | **CRÍTICO** |
| F13 | Alucinación de nombres | Usa nombre del cliente antes de que lo diga | ~162 convs | **ALTO** |
| F14 | `texto_original` y leaks de agente en tool responses | "Agent stopped due to max iterations" | 91 msgs | **ALTO** |
| F15 | Tool response JSON malformado | Caracteres de control (\u000b) en JSON | 32 msgs | **ALTO** |
| F16 | Respuestas robóticas/template | "Se encontraron los siguientes..." | por auditar | **MEDIO** |
| F17 | Nombre incorrecto de TREFA ("lote", "tienda") | Viola instrucciones del system prompt | 2 convs | **BAJO** |
| F18 | Unicode escapes sin decodificar (`\u00e9` etc.) | Caracteres acentuados como escapes en vez de UTF-8 | 40 msgs (conv 362+) | **ALTO** |
| F19 | URLs placeholder/externas (`ejemplo.com`, `airtableusercontent.com`) | URLs falsas o internas filtradas | 9 msgs | **ALTO** |
| F20 | Caracteres de control vertical tab (`\u000b`) en contenido de garantías | Corrompe parsing de JSON en tool responses | 29 msgs | **MEDIO** |
| F21 | Conversaciones duplicadas masivas | Sesgo severo: 66 copias de una misma conv | 37 grupos, ~200+ convs duplicadas | **CRÍTICO** |
| F22 | Leaks de razonamiento interno | "Déjame buscar", "Voy a usar la herramienta" | 130 msgs | **ALTO** |
| F23 | Datos vehiculares sin tool call previo | Precios y años sin respaldo de herramienta | 88 msgs | **ALTO** |

---

## Sección 1: Problemas de Formato Estructural (BLOQUEANTES)

### F1. Formato de Tool Calling Completamente Incorrecto

**Formato actual (INCORRECTO para Together/Qwen3):**
```json
{
  "role": "assistant",
  "content": "<tool_call>\n{\"name\": \"buscar_vehiculos\", \"arguments\": {\"marca\": \"Toyota\"}}\n</tool_call>"
}
```

**Formato requerido por Together AI / Qwen 3:**
```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "buscar_vehiculos",
        "arguments": "{\"marca\": \"Toyota\"}"
      }
    }
  ]
}
```

- **Afectados:** 2,543/2,543 conversaciones (100%)
- **Total tool calls encontrados en markup:** 4,335
- **Tool calls en formato estándar:** 0

### F2. Campo `tools` Ausente

Ninguna conversación tiene el campo `tools` al nivel raíz del JSON. Las definiciones están embebidas dentro del system prompt como XML `<tools>...</tools>`. Together AI requiere:

```json
{
  "tools": [
    {"type": "function", "function": {"name": "buscar_vehiculos", ...}}
  ],
  "messages": [...]
}
```

- **Afectados:** 2,543/2,543 (100%)

### F3. `tool_call_id` Faltante en Mensajes Tool

Los mensajes de rol `tool` no tienen `tool_call_id`, lo cual es obligatorio para vincular respuestas con sus llamadas. Sin esto, el modelo no puede aprender la relación call→response.

```json
// ACTUAL (incorrecto)
{"role": "tool", "content": "<tool_response>{...}</tool_response>"}

// REQUERIDO
{"role": "tool", "tool_call_id": "call_abc123", "content": "{...}"}
```

- **Mensajes afectados:** 4,335/4,335 (100%)

### F4. Wrapper `<tool_response>` en Content de Tool Messages

El 99% de los mensajes con rol `tool` tienen su contenido envuelto en `<tool_response>...</tool_response>`. El content debe ser JSON limpio.

- **Con wrapper:** 4,298
- **Sin wrapper:** 37

### F5. System Prompt Contiene Template de Tool Call

TODOS los system prompts terminan con una sección de herramientas que incluye un placeholder literal:

```
For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>
```

Esto causa que el modelo aprenda a imitar este patrón en vez de usar el mecanismo nativo de tool calling. Esta sección DEBE eliminarse cuando las tools se mueven al campo `tools`.

---

## Sección 2: Inconsistencia de Schema de Herramientas (CRÍTICO)

### F6. Dos Versiones Incompatibles de System Prompt

Existen **dos variantes** de system prompt en el dataset:

| Variante | Longitud | Conversaciones | % |
|----------|----------|----------------|---|
| Schema v1 (original) | 12,241 chars | 986 | 38.8% |
| Schema v2 (expandido) | 12,093 chars | 1,557 | 61.2% |

**Diferencias críticas encontradas:**

#### `buscar_vehiculos`
- v1: `{marca, modelo, anio_min, anio_max, precio_min, precio_max, tipo, transmision, ubicacion, limit}`
- v2: v1 + `{combustible, kilometraje_max, garantia}` (3 args extra)

#### `buscar_alternativas`
- v1: `{vehiculo_id, precio_max, limit}`
- v2: `{marca_original, modelo_original, presupuesto, tipo_uso, carroceria, ubicacion}` (schema COMPLETAMENTE diferente)

#### `calcular_financiamiento`
- v1: `{vehiculo_id, enganche, plazo}`
- v2: `{vehiculo_id, precio_vehiculo, enganche_porcentaje, plazo_meses, tasa_anual}` (schema COMPLETAMENTE diferente)

#### Herramientas eliminadas en v2
- `solicitar_datos_contacto` — removida
- `enviar_cotizacion_email` — removida

**Impacto:** El modelo aprenderá argumentos contradictorios. Cuando use `calcular_financiamiento`, a veces usará `{enganche, plazo}` y otras veces `{enganche_porcentaje, plazo_meses}`.

### F11. Argumentos de `calcular_financiamiento` — 100% Usan Schema v2

| Argumento | Calls | En v1? | En v2? |
|-----------|-------|--------|--------|
| `enganche_porcentaje` | 260/260 (100%) | NO | SÍ |
| `plazo_meses` | 225/260 (86%) | NO | SÍ |
| `precio_vehiculo` | 138/260 (53%) | NO | SÍ |
| `vehiculo_id` | 135/260 (52%) | SÍ | SÍ |
| `tasa_anual` | 2/260 (0.8%) | NO | SÍ |
| `enganche` | 0/260 (0%) | SÍ | NO |
| `plazo` | 0/260 (0%) | SÍ | NO |

**TODOS** los tool calls usan el schema v2, pero el 39% de las conversaciones definen el schema v1 en su system prompt. Esto es una contradicción directa.

### F12. Argumentos de `buscar_alternativas` — 99% Usan Schema v2

| Argumento | Calls | En v1? | En v2? |
|-----------|-------|--------|--------|
| `presupuesto` | 72/75 (96%) | NO | SÍ |
| `marca_original` | 56/75 (75%) | NO | SÍ |
| `carroceria` | 42/75 (56%) | NO | SÍ |
| `modelo_original` | 39/75 (52%) | NO | SÍ |
| `ubicacion` | 18/75 (24%) | NO | SÍ |
| `vehiculo_id` | 0/75 (0%) | SÍ | NO |
| `precio_max` | 0/75 (0%) | SÍ | NO |

---

## Sección 3: Problemas de Calidad Conversacional (CRÍTICO/ALTO)

### F7. Texto Mezclado con `<tool_call>` en el Mismo Content

443 mensajes de assistant tienen texto legible mezclado con markup de tool call:

```
"content": "Perfecto, Ricardo. Vamos a calcular el financiamiento.\n\n<tool_call>\n{\"name\": \"calcular_financiamiento\", \"arguments\": {\"precio_vehiculo\": 285000}}\n</tool_call>"
```

**Impacto:** Cuando se convierta al formato correcto, habrá que decidir si el texto va en `content` y el tool call en `tool_calls`, o si se divide en dos mensajes.

### F8. Sin Saludo Antes del Primer Tool Call — 835 Conversaciones (33%)

El system prompt dice: "SIEMPRE preséntate como Mariana en tu primer mensaje". Sin embargo, en 835 conversaciones el primer mensaje del assistant es directamente un `<tool_call>` sin presentación.

**Ejemplos:**
- Conv 1: Primer msg assistant es `<tool_call>{"name": "obtener_info_negocio"}`
- Conv 35: Primer msg assistant es `<tool_call>{"name": "buscar_vehiculos"}`
- Conv 1500: Primer msg assistant es `<tool_call>{"name": "buscar_vehiculos"}`

### F9. URLs Faltantes Donde Se Prometieron — 550 Mensajes

El assistant dice frases como "Aquí puedes verlo con todas sus fotos:" o "Puedes ver todos los detalles aquí:" pero **no incluye ninguna URL**. El espacio queda vacío.

**Ejemplos:**
```
Conv 1, Msg 14: "Aquí puedes verlo con todas sus fotos: \n\n¿Te gustaría que agendemos..."
Conv 11, Msg 12: "Aquí puedes verla con todas sus fotos: \n\nCon un 20% de enganche..."
Conv 14, Msg 10: "Aquí puedes ver todas sus fotos y detalles: \n\nTiene solo 35,000 km..."
```

### F10. Datos de Financiamiento Sin Usar `calcular_financiamiento` — 692 Mensajes

El assistant presenta montos de enganche y mensualidades que no provienen de ninguna herramienta, violando la regla de veracidad del system prompt.

**Ejemplos:**
```
Conv 54: "el enganche mínimo (20%) sería de $55,980 MXN"  — sin tool call previo
Conv 63: "el enganche mínimo sería de $29,000 MXN (20%)" — sin tool call previo
Conv 91: "La mensualidad desde $6,500 MXN" — sin tool call previo
```

### F13. Alucinación de Nombres — ~162 Conversaciones

El assistant utiliza el nombre del cliente en su primer mensaje cuando el cliente AÚN no lo ha proporcionado.

**Ejemplos:**
```
Conv 3: "¡Hola Alberto! 😊 Soy Mariana..." — el usuario NO dijo su nombre
Conv 15: "¡Hola Paola! 😊 Soy Mariana..." — el usuario NO dijo su nombre
Conv 44: "¡Hola, José! 😊 Soy Mariana..." — el usuario NO dijo su nombre
```

**Nombres más frecuentes en alucinaciones:** Ricardo (309x), Carlos (282x), Roberto (172x), Juan (74x), Jorge (62x)

### F14. Leaks de "texto_original" y Mensajes de Agente — 91 Tool Responses

Algunos tool responses contienen un campo `texto_original` que leak texto generado por un agente, incluyendo mensajes de error:

```json
{"texto_original": "Agent stopped due to max iterations."}
{"texto_original": "Perfecto — gracias por la pregunta. Sí encontré el vehículo..."}
```

### F15. Tool Response JSON Malformado — 32 Mensajes

Caracteres de control `\u000b` (vertical tab) dentro del JSON causan errores de parsing. Encontrados principalmente en respuestas de `buscar_informacion` sobre garantías.

### F18. Unicode Escapes Sin Decodificar — 40 Mensajes

En conversaciones a partir de conv 362, se encontraron caracteres acentuados como unicode escapes (`\u00e9` para é, `\u00f3` para ó) en vez de caracteres UTF-8 nativos. Esto indica que estos datos pasaron por un proceso de serialización que no decodificó correctamente.

**Impacto:** El modelo podría aprender a generar escapes en vez de caracteres reales.

### F19. URLs Placeholder y Externas Filtradas — 9 Mensajes

Se encontraron URLs que no deberían estar en el dataset:

- `https://ejemplo.com/...` en convs 17, 113, 775, 891 — URLs placeholder inventadas
- `https://v5.airtableusercontent.com/...` en conv 1733 — URL interna de Airtable filtrada
- `https://youtu.be/ejemplo-video-sienna` en conv 1733 — URL de YouTube inventada
- `https://www.google.com/maps/search/...` en conv 106 — Link de Maps (potencialmente ok)

### F20. Caracteres Vertical Tab en Contenido de Garantías — 29 Mensajes

Contenido de "Garantías TREFA" contiene caracteres `\t` y `\u000b` (vertical tab) que probablemente son artefactos de la fuente de datos (Airtable/Notion).

### F21. Conversaciones Duplicadas Masivas — 37 Grupos (CRÍTICO)

Se identificaron **37 grupos de conversaciones con fingerprints idénticos**. Los peores casos:

| Conversación (primer mensaje user) | Copias |
|-------------------------------------|--------|
| "Hola, quiero un credito para un auto y tengo cuenta Banorte" | **66x** |
| "Hola! me gustaría recibir más información para comprar el auto:" | **46x** |
| "Hola! Quiero más información." | **29x** |
| "Hola! Me interesa la promoción del mes: Placas y Trámite GRATIS" | **23x** |
| Otros 33 grupos | 2-15x cada uno |

**Impacto:** Sesgo severo en el fine-tuning. El modelo memorizará estas conversaciones y sus patrones de respuesta dominarán. Estimación conservadora: ~200+ conversaciones son duplicados que deben reducirse a 1 por grupo.

### F22. Leaks de Razonamiento Interno — 130 Mensajes

El assistant expone su proceso de razonamiento al usuario:

| Patrón | Ocurrencias |
|--------|-------------|
| "Déjame buscar..." | 93 |
| "Voy a buscar..." | 36 |
| "Voy a usar la herramienta..." | 1 |

**Ejemplo:** "Déjame buscar las Suzuki Ertiga que tenemos disponibles en Reynosa"

**Impacto:** El modelo aprenderá a anunciar que va a usar herramientas en vez de hacerlo silenciosamente. En producción, el tool call debe ser invisible al usuario — el assistant debería decir "Dame un momentito" y ejecutar el tool, no "Voy a usar la herramienta buscar_vehiculos".

### F23. Datos Vehiculares Sin Tool Call Previo — 88 Mensajes

El assistant presenta datos específicos de vehículos (precios + años) sin que exista un tool response previo que los respalde.

**Impacto:** Similar a F10 pero para datos de vehículos en general, no solo financiamiento. El modelo aprenderá a inventar listados de autos.

---

## Sección 4: Distribución de Tool Calls

### Herramientas Usadas (de markup parsing)

| Herramienta | Calls | % del Total |
|-------------|-------|-------------|
| `buscar_vehiculos` | 2,930 | 67.6% |
| `obtener_vehiculo` | 458 | 10.6% |
| `obtener_info_negocio` | 392 | 9.0% |
| `calcular_financiamiento` | 260 | 6.0% |
| `estadisticas_inventario` | 138 | 3.2% |
| `buscar_alternativas` | 75 | 1.7% |
| `comparar_vehiculos` | 46 | 1.1% |
| `buscar_informacion` | 32 | 0.7% |
| `obtener_faqs` | 4 | 0.1% |
| **Total** | **4,335** | **100%** |

### Herramientas NO Usadas (definidas en v1)
- `solicitar_datos_contacto` — 0 calls
- `enviar_cotizacion_email` — 0 calls

---

## Sección 5: Framework de Evaluación por Conversación

### Objetivo

Este framework permite que un LLM más pequeño (Qwen 14B, Llama 8B) evalúe cada conversación individualmente, produciendo un score objetivo y flags de problemas específicos.

### Criterios de Evaluación (15 dimensiones)

```json
{
  "evaluacion": {
    "id_conversacion": 0,
    "total_mensajes": 13,
    "score_total": 0,
    "max_score": 100,
    "aprobada": false,
    "dimensiones": {
      "D01_formato_tool_call": {
        "peso": 15,
        "score": 0,
        "checks": [
          "¿Los tool calls usan campo tool_calls (no markup <tool_call>)?",
          "¿Cada tool_call tiene id, type='function', function.name y function.arguments?",
          "¿arguments es un string JSON válido?"
        ]
      },
      "D02_campo_tools": {
        "peso": 10,
        "score": 0,
        "checks": [
          "¿La conversación tiene campo 'tools' al nivel raíz?",
          "¿Cada tool tiene type='function' y function con name+parameters?",
          "¿No hay definiciones de tools dentro del system prompt?"
        ]
      },
      "D03_tool_call_id_chain": {
        "peso": 10,
        "score": 0,
        "checks": [
          "¿Cada tool message tiene tool_call_id?",
          "¿Cada tool_call_id corresponde a un id en un tool_call previo?",
          "¿No hay tool calls huérfanos (sin response)?",
          "¿No hay tool responses huérfanos (sin call)?"
        ]
      },
      "D04_tool_response_format": {
        "peso": 5,
        "score": 0,
        "checks": [
          "¿El content de tool messages es JSON limpio (sin <tool_response> wrapper)?",
          "¿El JSON parsea correctamente sin caracteres de control?"
        ]
      },
      "D05_saludo_inicial": {
        "peso": 10,
        "score": 0,
        "checks": [
          "¿El primer mensaje assistant contiene saludo (Hola/Buenos)?",
          "¿Incluye 'Soy Mariana' o 'me llamo Mariana'?",
          "¿Incluye al menos un emoji?",
          "¿Pregunta el nombre del cliente (si no lo ha dado)?",
          "¿NO se identifica como Mariana más de una vez en toda la conversación?"
        ]
      },
      "D06_discovery_antes_de_tool": {
        "peso": 8,
        "score": 0,
        "checks": [
          "¿El primer tool call ocurre DESPUÉS de que el usuario especificó qué busca?",
          "¿No hay tool call en el primer mensaje assistant sin discovery previa?",
          "Si el usuario ya especificó marca/modelo/presupuesto, el tool call es válido"
        ]
      },
      "D07_argumentos_tool_correctos": {
        "peso": 10,
        "score": 0,
        "checks": [
          "¿Todos los argumentos usados existen en el schema de la herramienta?",
          "¿Los tipos de datos son correctos (int vs string vs number)?",
          "¿No se inventan argumentos como 'ubicacion' en buscar_vehiculos v2?",
          "¿Los argumentos de calcular_financiamiento coinciden con el schema definido?"
        ]
      },
      "D08_datos_de_tools_no_inventados": {
        "peso": 10,
        "score": 0,
        "checks": [
          "¿Todo dato vehicular (precio, km, año) proviene de un tool response previo?",
          "¿No se inventan precios, kilometrajes o especificaciones?",
          "¿Los datos de financiamiento vienen de calcular_financiamiento?",
          "¿Las direcciones/horarios vienen de obtener_info_negocio?"
        ]
      },
      "D09_urls_completas": {
        "peso": 5,
        "score": 0,
        "checks": [
          "Si el assistant dice 'aquí puedes verlo', ¿hay una URL real?",
          "¿Las URLs son del dominio autostrefa.com o trefa.mx?",
          "¿No hay URLs placeholder, rotas o inventadas?"
        ]
      },
      "D10_no_alucinacion_nombres": {
        "peso": 5,
        "score": 0,
        "checks": [
          "¿El assistant NO usa el nombre del cliente antes de que éste lo proporcione?",
          "¿El assistant pregunta el nombre si no lo tiene?"
        ]
      },
      "D11_tono_y_naturalidad": {
        "peso": 5,
        "score": 0,
        "checks": [
          "¿No hay frases robóticas como 'se encontraron los siguientes'?",
          "¿Habla en primera persona?",
          "¿No se refiere a TREFA como lote/tienda/concesionario?",
          "¿No hay leaks de 'como modelo de lenguaje' o 'como asistente'?"
        ]
      },
      "D12_cta_y_cierre": {
        "peso": 3,
        "score": 0,
        "checks": [
          "¿Cada mensaje del assistant termina con pregunta o llamado a acción?",
          "¿El último mensaje guía hacia cierre (cita o financiamiento)?"
        ]
      },
      "D13_system_prompt_limpio": {
        "peso": 5,
        "score": 0,
        "checks": [
          "¿El system prompt NO contiene definiciones de tools en XML?",
          "¿No hay template <tool_call>{name: <function-name>} en el prompt?",
          "¿El system prompt es consistente (una sola versión)?"
        ]
      },
      "D14_secuencia_roles": {
        "peso": 4,
        "score": 0,
        "checks": [
          "¿La secuencia es system→user→assistant→[tool→]*→assistant...?",
          "¿No hay dos assistant consecutivos (sin user/tool entre ellos)?",
          "¿No hay user después de user sin assistant intermedio?"
        ]
      },
      "D15_sin_leaks_de_agente": {
        "peso": 5,
        "score": 0,
        "checks": [
          "¿No hay 'Agent stopped due to max iterations' en tool responses?",
          "¿No hay campo texto_original con texto de agente?",
          "¿No hay 'Let me think', 'Voy a buscar' u otros leaks de razonamiento?"
        ]
      }
    }
  }
}
```

### Scoring

- Cada dimensión tiene un peso (sumando 100)
- Score = peso × (checks_pasados / total_checks)
- **Aprobada:** score_total >= 75 Y D01+D02+D03 = máximo (formato correcto)
- **Rechazada:** cualquier check BLOQUEANTE falla

---

## Sección 6: Ejemplos de Evaluación para el LLM Evaluador

### Ejemplo 1: Conversación MALA (Conv 0 — estado actual)

```
EVALUACIÓN Conv 0:
- D01_formato_tool_call: 0/15 — Usa <tool_call> markup en content
- D02_campo_tools: 0/10 — No tiene campo tools, tools en system prompt XML
- D03_tool_call_id_chain: 0/10 — Ningún tool message tiene tool_call_id
- D04_tool_response_format: 0/5 — Tiene <tool_response> wrapper
- D05_saludo_inicial: 4/10 — Tiene saludo y Soy Mariana, pero usa nombre "Cecilia" sin que lo diera
- D06_discovery_antes_de_tool: 8/8 — Correcto, primero pregunta y luego busca
- D07_argumentos_tool_correctos: 5/10 — buscar_vehiculos usa "ubicacion" (v1 ok), obtener_info_negocio ok
- D08_datos_de_tools_no_inventados: 10/10 — Datos provienen de tools
- D09_urls_completas: N/A — No promete URLs
- D10_no_alucinacion_nombres: 0/5 — Usa "Cecilia" sin que el usuario lo haya dicho
- D11_tono_y_naturalidad: 5/5 — Tono natural y cálido
- D12_cta_y_cierre: 3/3 — Termina con pregunta
- D13_system_prompt_limpio: 0/5 — Contiene tools XML y template
- D14_secuencia_roles: 4/4 — Secuencia correcta
- D15_sin_leaks_de_agente: 5/5 — Limpio

SCORE: 44/100 — RECHAZADA (formato bloqueante)
```

### Ejemplo 2: Cómo Debería Verse una Conversación CORREGIDA

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "buscar_vehiculos",
        "description": "Busca vehículos en el inventario de Autos TREFA.",
        "parameters": {
          "type": "object",
          "properties": {
            "marca": {"type": "string"},
            "modelo": {"type": "string"},
            "anio_min": {"type": "integer"},
            "anio_max": {"type": "integer"},
            "precio_min": {"type": "number"},
            "precio_max": {"type": "number"},
            "tipo": {"type": "string"},
            "transmision": {"type": "string"},
            "ubicacion": {"type": "string"},
            "limit": {"type": "integer", "default": 5}
          }
        }
      }
    }
  ],
  "messages": [
    {
      "role": "system",
      "content": "Eres Mariana, asesora de Autos TREFA... [SIN sección de tools XML al final]"
    },
    {
      "role": "user",
      "content": "Hola busco una camioneta"
    },
    {
      "role": "assistant",
      "content": "¡Hola! 😊 Soy Mariana de Autos TREFA y me da mucho gusto que nos escribas. ¿Me compartes tu nombre para atenderte mejor?\n\nPara ayudarte a encontrar la camioneta ideal, ¿tienes alguna marca o modelo en mente? ¿Y un presupuesto aproximado?"
    },
    {
      "role": "user",
      "content": "Soy Pedro, busco una RAV4 como de 400 mil"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_001",
          "type": "function",
          "function": {
            "name": "buscar_vehiculos",
            "arguments": "{\"marca\": \"Toyota\", \"modelo\": \"RAV4\", \"precio_max\": 400000}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_001",
      "content": "{\"vehiculos\": [{\"id\": 12345, \"titulo\": \"Toyota RAV4 XLE 2021\", \"precio\": 389900, \"kilometraje\": 35000, \"ubicacion\": \"Monterrey\"}]}"
    },
    {
      "role": "assistant",
      "content": "¡Encontré una opción que creo te va a encantar, Pedro! 🚗\n\n• **Toyota RAV4 XLE 2021** — $389,900 MXN | 35,000 km | Sucursal Monterrey\n\n¿Te gustaría que te dé más detalles de esta RAV4 o que calcule opciones de financiamiento?"
    }
  ]
}
```

### Ejemplo 3: Evaluación de Conversación Conv 50 (formato incorrecto, contenido bueno)

```
EVALUACIÓN Conv 50:
- D01_formato_tool_call: 0/15 — N/A (no hay tool calls), pero formato incorrecto en general
- D02_campo_tools: 0/10 — Falta campo tools
- D05_saludo_inicial: 10/10 — "¡Hola, qué tal 😊. Soy Mariana de Autos TREFA... ¿Cómo te llamas?"
- D10_no_alucinacion_nombres: 5/5 — No inventa nombres
- D11_tono_y_naturalidad: 5/5 — Natural y cálida
- D12_cta_y_cierre: 3/3 — Pregunta el nombre

SCORE: Parcial — requiere corrección de formato aunque el contenido es bueno
```

---

## Sección 7: Prompt para el LLM Evaluador

```
Eres un evaluador de calidad para un dataset de fine-tuning. Cada conversación es de "Mariana",
una asesora de ventas de Autos TREFA (agencia de autos seminuevos en México).

Evalúa cada conversación en las siguientes 15 dimensiones. Para cada una, indica:
- PASS / FAIL / N/A
- Evidencia específica (cita el texto o la estructura que causa el fail)
- Score numérico (0 a peso_max)

DIMENSIONES:
D01 (15pts) FORMATO TOOL CALL: Los tool calls deben usar el campo tool_calls del mensaje,
    NO markup <tool_call> dentro del content. Cada call necesita id, type="function",
    function.name y function.arguments (string JSON).
D02 (10pts) CAMPO TOOLS: Debe existir un campo "tools" al nivel raíz con las definiciones.
D03 (10pts) CADENA TOOL_CALL_ID: Cada mensaje tool debe tener tool_call_id que corresponda
    a un id de un tool_call del assistant previo.
D04 (5pts) FORMAT TOOL RESPONSE: El content del tool message debe ser JSON limpio,
    sin wrapper <tool_response>.
D05 (10pts) SALUDO INICIAL: El primer mensaje del assistant debe incluir saludo,
    "Soy Mariana", emoji, y preguntar el nombre si no lo tienen.
D06 (8pts) DISCOVERY ANTES DE TOOL: No ejecutar buscar_vehiculos sin que el usuario
    haya especificado qué busca.
D07 (10pts) ARGUMENTOS CORRECTOS: Los argumentos de cada tool call deben existir
    en el schema definido.
D08 (10pts) DATOS NO INVENTADOS: Precios, km, financiamiento deben venir de tool responses.
D09 (5pts) URLs COMPLETAS: Si dice "puedes verlo aquí", debe haber URL real.
D10 (5pts) SIN ALUCINACIÓN DE NOMBRES: No usar el nombre antes de que lo den.
D11 (5pts) TONO NATURAL: Sin frases robóticas, hablar en primera persona.
D12 (3pts) CTA EN CADA MENSAJE: Cada respuesta debe terminar con pregunta o acción.
D13 (5pts) SYSTEM PROMPT LIMPIO: Sin definiciones XML de tools ni templates.
D14 (4pts) SECUENCIA DE ROLES: system→user→assistant→[tool]*→assistant válida.
D15 (5pts) SIN LEAKS DE AGENTE: Sin "Agent stopped", "texto_original", ni razonamiento visible.

Responde con JSON:
{
  "conv_id": X,
  "scores": {"D01": X, "D02": X, ...},
  "total": X,
  "aprobada": true/false,
  "issues": ["descripción breve de cada problema encontrado"],
  "recomendacion": "aprobar|corregir|descartar"
}
```

---

## Sección 8: Plan de Remediación Priorizado

### Fase 1: Correcciones Bloqueantes (OBLIGATORIAS antes de fine-tuning)

1. **Convertir `<tool_call>` markup → campo `tool_calls` estándar**
   - Parsear cada `<tool_call>{...}</tool_call>` del content
   - Mover a `msg["tool_calls"] = [{"id": "call_XXX", "type": "function", "function": {...}}]`
   - Si hay texto antes del tool_call → mover a `msg["content"]`
   - Si solo es tool_call → `msg["content"] = null`

2. **Convertir `<tool_response>` → content limpio**
   - Parsear cada `<tool_response>{...}</tool_response>` del content de tool messages
   - Reemplazar con JSON limpio: `msg["content"] = json_str`

3. **Generar `tool_call_id`**
   - Asignar IDs únicos: `call_conv{ci}_msg{mi}_tc{tci}`
   - Vincular con `msg["tool_call_id"]` en los tool messages

4. **Extraer tools del system prompt → campo `tools` raíz**
   - Parsear las definiciones de `<tools>...</tools>` del system prompt
   - Moverlas a `conv["tools"] = [...]`
   - Eliminar TODA la sección `# Tools` + `<tools>` + template del system prompt

5. **Unificar schema de herramientas**
   - Decidir una sola versión (recomendado: v2)
   - Asegurar que TODOS los tool calls usen los argumentos correctos
   - Actualizar las 986 conversaciones v1 al schema v2

### Fase 2: Correcciones Críticas (necesarias para calidad)

6. **Agregar saludo a conversaciones que inician con tool call** (835 convs)
   - Insertar mensaje assistant con saludo ANTES del tool call
   - O mover el tool call a después de un saludo

7. **Completar URLs faltantes** (550 msgs)
   - Si hay datos del vehículo, construir URL: `https://autostrefa.com/vehiculo/{slug}`
   - Si no hay slug, eliminar la promesa de URL

8. **Remover datos de financiamiento inventados** (692 msgs)
   - Agregar tool call a `calcular_financiamiento` antes del mensaje
   - O eliminar los datos financieros del mensaje

### Fase 3: Correcciones de Calidad (recomendadas)

9. **Corregir alucinación de nombres** (~162 convs)
10. **Limpiar leaks de agente en tool responses** (91 msgs)
11. **Corregir JSON malformado en tool responses** (32 msgs)
12. **Auditar respuestas robóticas** (por cuantificar)
13. **Verificar que las 2 tools no usadas (solicitar_datos_contacto, enviar_cotizacion_email) se incluyan con ejemplos o se remuevan del schema**

---

## Sección 9: Script de Conversión (Esqueleto)

```python
import json
import re
import uuid

def convert_conversation(conv):
    """Convierte una conversación del formato markup al formato Together AI."""
    msgs = conv['messages']
    new_msgs = []
    tools_extracted = []

    # 1. Procesar system prompt
    system = msgs[0]
    system_content = system['content']

    # Extraer tools del system prompt
    tools_match = re.search(r'<tools>(.*?)</tools>', system_content, re.DOTALL)
    if tools_match:
        tools_raw = tools_match.group(1)
        for tool_json in re.findall(r'\{.*?"function".*?\}\}', tools_raw):
            try:
                tool = json.loads(tool_json)
                tools_extracted.append(tool)
            except:
                pass

    # Limpiar system prompt (remover sección # Tools hasta el final)
    clean_system = re.sub(r'\n# Tools\n.*$', '', system_content, flags=re.DOTALL)
    new_msgs.append({"role": "system", "content": clean_system.strip()})

    # 2. Procesar resto de mensajes
    call_counter = 0
    pending_call_ids = []

    for mi, msg in enumerate(msgs[1:], 1):
        role = msg['role']
        content = msg.get('content', '') or ''

        if role == 'assistant':
            # Extraer tool calls del content
            tc_matches = re.findall(r'<tool_call>\s*(.*?)\s*</tool_call>', content, re.DOTALL)
            text_before = re.split(r'<tool_call>', content)[0].strip()

            if tc_matches:
                tool_calls = []
                for tc_raw in tc_matches:
                    try:
                        tc_data = json.loads(tc_raw)
                        call_id = f"call_{call_counter:04d}"
                        call_counter += 1
                        pending_call_ids.append(call_id)
                        tool_calls.append({
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tc_data["name"],
                                "arguments": json.dumps(tc_data["arguments"], ensure_ascii=False)
                            }
                        })
                    except:
                        pass

                new_msg = {
                    "role": "assistant",
                    "content": text_before if text_before else None,
                    "tool_calls": tool_calls
                }
                new_msgs.append(new_msg)
            else:
                new_msgs.append({"role": "assistant", "content": content})

        elif role == 'tool':
            # Extraer JSON de <tool_response>
            tr_match = re.search(r'<tool_response>\s*(.*?)\s*</tool_response>', content, re.DOTALL)
            clean_content = tr_match.group(1) if tr_match else content

            call_id = pending_call_ids.pop(0) if pending_call_ids else f"call_orphan_{mi}"
            new_msgs.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": clean_content
            })

        else:
            new_msgs.append({"role": role, "content": content})

    return {
        "tools": tools_extracted,
        "messages": new_msgs
    }
```

---

## Sección 10: Escenarios de Tool Use que Necesitan Más Ejemplos

Basado en la distribución actual, estas herramientas y escenarios necesitan más conversaciones de ejemplo:

| Escenario | Calls Actuales | Recomendado | Prioridad |
|-----------|---------------|-------------|-----------|
| `obtener_faqs` | 4 | 30+ | ALTA |
| `buscar_informacion` | 32 | 50+ | ALTA |
| `comparar_vehiculos` | 46 | 80+ | MEDIA |
| `buscar_alternativas` (cuando no hay resultados) | 75 | 150+ | ALTA |
| `estadisticas_inventario` | 138 | OK | BAJA |
| Tool call fallido → manejo de error | ~0 | 50+ | ALTA |
| Múltiples tool calls en una conversación | presente | más variedad | MEDIA |
| Cliente que cambia de opinión a mitad de conversación | bajo | 50+ | MEDIA |
| Cliente que pregunta algo no relacionado con autos | presente | verificar calidad | MEDIA |
| Solicitud de financiamiento completa (end-to-end) | bajo | 100+ | ALTA |

---

## Conclusión

El dataset contiene **conversaciones de buena calidad en cuanto a tono y flujo** pero su formato es **completamente incompatible** con Together AI / Qwen 3 para fine-tuning con tool calling. Sin la conversión de formato (Fase 1), el fine-tuning:

1. **NO enseñará tool calling nativo** — el modelo aprenderá a escribir `<tool_call>` como texto
2. **Tendrá argumentos inconsistentes** — dos schemas diferentes entremezclados
3. **Aprenderá a alucinar datos** — financiamiento sin tool, URLs vacías, nombres inventados

**Recomendación:** Ejecutar el script de conversión (Fase 1), luego pasar cada conversación por el framework de evaluación (Sección 5-7) con un LLM, corregir o descartar las que fallen, y regenerar los escenarios faltantes (Sección 10).
