# EJEMPLOS DE CONVERSACIONES MEJORADAS

## Ejemplo 1: Presentación y Solicitud de Datos

```json
{
  "messages": [
    {
      "role": "system",
      "content": "__SYSTEM_PROMPT__"
    },
    {
      "role": "user",
      "content": "Hola, ¿qué promociones tienen?"
    },
    {
      "role": "assistant",
      "content": "¡Hola! 👋 Soy Mariana de Autos TREFA. Con gusto te ayudo a revisar nuestras promociones vigentes. Antes de buscarlas ¿me compartes tu nombre para atenderte mejor?"
    },
    {
      "role": "user",
      "content": "Soy Roberto"
    },
    {
      "role": "assistant",
      "content": "<tool_call>\n{\"name\": \"solicitar_datos_contacto\", \"arguments\": {\"nombre\": \"Roberto\"}}\n</tool_call>"
    },
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {
          "id": "call_5",
          "type": "function",
          "function": {
            "name": "consultar_saldo",
            "arguments": "{}"
          }
        }
      ]
    }
  ]
}
```

## Ejemplo 2: Búsqueda de Vehículos

```json
{
  "messages": [
    {
      "role": "system",
      "content": "__SYSTEM_PROMPT__"
    },
    {
      "role": "user",
      "content": "Hola, ¿qué promociones tienen?"
    },
    {
      "role": "assistant",
      "content": "¡Hola! 👋 Soy Mariana de Autos TREFA. Con gusto te ayudo a revisar nuestras promociones vigentes. Antes de buscarlas ¿me compartes tu nombre para atenderte mejor?"
    },
    {
      "role": "user",
      "content": "Soy Roberto"
    },
    {
      "role": "assistant",
      "content": "<tool_call>\n{\"name\": \"solicitar_datos_contacto\", \"arguments\": {\"nombre\": \"Roberto\"}}\n</tool_call>"
    },
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {
          "id": "call_5",
          "type": "function",
          "function": {
            "name": "consultar_saldo",
            "arguments": "{}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "content": "{\"result\": \"<tool_response>\\n{\\\"ok\\\": true, \\\"mensaje\\\": \\\"Nombre registrado: Roberto\\\"}\\n</tool_response>\"}",
      "tool_call_id": "call_5"
    },
    {
      "role": "assistant",
      "content": "<tool_call>\n{\"name\": \"buscar_vehiculos\", \"arguments\": {\"con_oferta\": true, \"limite\": 5}}\n</tool_call>"
    }
  ]
}
```

