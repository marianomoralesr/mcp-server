# Guía de Validación - Conversaciones Mejoradas Mariana

## Verificación Rápida

### 1. Estructura Básica
```bash
# Contar líneas (debe ser 42)
wc -l gold_upgraded_haiku_1285_1326.jsonl

# Verificar que cada línea es JSON válido
python3 -c "
import json
with open('gold_upgraded_haiku_1285_1326.jsonl') as f:
    for i, line in enumerate(f, 1):
        try:
            json.loads(line)
        except:
            print(f'Error en línea {i}')
print('✓ JSON válido en todas las líneas')
"
```

### 2. System Prompt Correcto
```bash
python3 << 'EOF'
import json
with open('gold_upgraded_haiku_1285_1326.jsonl') as f:
    for i, line in enumerate(f, 1):
        conv = json.loads(line)
        msg1 = conv['messages'][0]
        if msg1['role'] != 'system' or msg1['content'] != '__SYSTEM_PROMPT__':
            print(f'Error en conversación {i}')
        else:
            print(f'✓ Conv {i}: System prompt correcto')
EOF
```

### 3. Identidad de Mariana
```bash
python3 << 'EOF'
import json
with open('gold_upgraded_haiku_1285_1326.jsonl') as f:
    for i, line in enumerate(f, 1):
        conv = json.loads(line)
        texto = ' '.join([m.get('content', '') for m in conv['messages']])
        
        # Buscar problemas
        if 'TREFABOT' in texto:
            print(f'⚠ Conv {i}: Contiene TREFABOT (debe ser Mariana de Autos TREFA)')
        if 'asistente virtual' in texto:
            print(f'⚠ Conv {i}: Contiene "asistente virtual"')
        
        # Contar presentaciones correctas
        if 'Mariana de Autos TREFA' in texto:
            print(f'✓ Conv {i}: Nombre correcto encontrado')
EOF
```

### 4. Emojis
```bash
python3 << 'EOF'
import json
with open('gold_upgraded_haiku_1285_1326.jsonl') as f:
    for i, line in enumerate(f, 1):
        conv = json.loads(line)
        # Revisar solo mensajes del asistente
        for msg in conv['messages']:
            if msg['role'] == 'assistant':
                content = msg.get('content', '')
                # No revisar tool calls
                if '<tool' not in content:
                    if ':)' in content or '😊' in content or '😄' in content:
                        print(f'✓ Conv {i}: Con emoji')
                        break
                    else:
                        print(f'⚠ Conv {i}: Sin emoji en saludos')
                        break
EOF
```

### 5. Formato de Vehículos
```bash
python3 << 'EOF'
import json
import re
with open('gold_upgraded_haiku_1285_1326.jsonl') as f:
    for i, line in enumerate(f, 1):
        conv = json.loads(line)
        texto = ' '.join([m.get('content', '') for m in conv['messages']])
        
        # Buscar formato: "Opción X — **Marca Modelo Año**"
        if 'Opción' in texto:
            # Verificar que hay **negritas**
            if re.search(r'Opción\s+\d+\s*[—–-]\s*\*\*[^*]+\*\*', texto):
                print(f'✓ Conv {i}: Formato de auto correcto (con negritas)')
            else:
                print(f'⚠ Conv {i}: Autos sin formato de negritas')
EOF
```

## Verificación Manual (Muestras)

Selecciona 5 conversaciones aleatorias y verifica manualmente:

```python
import json
import random

with open('gold_upgraded_haiku_1285_1326.jsonl') as f:
    conversaciones = [json.loads(line) for line in f]

# Seleccionar 5 aleatorias
muestras = random.sample(range(len(conversaciones)), 5)

for idx in muestras:
    print(f"\n{'='*70}")
    print(f"CONVERSACIÓN #{idx + 1}")
    print(f"{'='*70}")
    
    conv = conversaciones[idx]
    for i, msg in enumerate(conv['messages'][:6], 1):  # Primeros 6 mensajes
        print(f"\n[{msg['role'].upper()}]")
        content = msg['content'][:150]
        print(f"{content}...")
```

## Checklist de Validación

Para cada conversación verificar:

- [ ] Primer mensaje es system prompt = "__SYSTEM_PROMPT__"
- [ ] Todos los mensajes tienen campos "role" y "content"
- [ ] Las respuestas del bot incluyen :) o emoji
- [ ] Sin TREFABOT, sin "asistente virtual"
- [ ] Autos presentados con **negritas** en títulos
- [ ] Tool calls están separados (no mezclados con texto)
- [ ] Tool responses están en mensajes separados
- [ ] No hay emojis como bullets (❌ 🔹)
- [ ] Flujo de conversación tiene sentido
- [ ] Datos como precios son numéricos formateados correctamente

## Ejemplos de Validación Exitosa

### System Prompt ✓
```json
{
  "role": "system",
  "content": "__SYSTEM_PROMPT__"
}
```

### Saludo con Emoji ✓
```json
{
  "role": "assistant",
  "content": "Hola :), soy Mariana de Autos TREFA. ¿Qué necesitas?"
}
```

### Presentación de Auto ✓
```json
{
  "role": "assistant",
  "content": "Opción 1 — **Toyota Corolla SE 2022**, automático, en $359,900 MXN."
}
```

### Tool Call Separado ✓
```json
{
  "role": "assistant",
  "content": "<tool_call>
{"name": "buscar_vehiculos", "arguments": {"marca": "Toyota"}}
</tool_call>"
}
```

## Ejemplos de Problemas (❌)

### System Prompt Incorrecto ❌
```json
{
  "role": "system",
  "content": "Eres un asistente para Autos TREFA..."  // INCORRECTO
}
```

### Nombre Incorrecto ❌
```json
{
  "role": "assistant",
  "content": "Soy TREFABOT de Autos TREFA"  // INCORRECTO
}
```

### Autos sin Negritas ❌
```json
{
  "role": "assistant",
  "content": "Opción 1: Toyota Corolla SE 2022"  // Falta **negritas**
}
```

### Emoji Bullet ❌
```json
{
  "role": "assistant",
  "content": "🔹 Toyota Corolla 2022
🔹 Toyota RAV4 2021"  // INCORRECTO
}
```

### Tool Call Mezclado ❌
```json
{
  "role": "assistant",
  "content": "Déjame buscar. <tool_call>...</tool_call> Aquí están."  // Debe estar solo
}
```

## Reporte Final

Después de validar, crear un reporte con:

```
RESUMEN DE VALIDACIÓN
════════════════════════════════════════
Conversaciones revisadas:       42/42
Válidas:                        42/42 (100%)
Con problemas:                  0/0

Cumplimiento de reglas:
  ✓ System prompt:              42/42 (100%)
  ✓ Nombre Mariana:             20+/42 (48%+)
  ✓ Emojis:                     38+/42 (90%+)
  ✓ Formato autos:              XX/42 (XX%)
  ✓ Tool calls:                 17/42 (40%)

RESULTADO: ✓ LISTO PARA FINE-TUNING
════════════════════════════════════════
```

## Próximos Pasos

1. ✓ Validar estructura JSON
2. ✓ Validar system prompts
3. ✓ Validar identidad de Mariana
4. ✓ Validar emojis y tono
5. ✓ Validar formato de vehículos
6. → Iniciar fine-tuning
7. → Evaluar resultados
8. → Iterar si es necesario

---

**Archivo:** gold_upgraded_haiku_1285_1326.jsonl  
**Conversaciones:** 42  
**Última validación:** 2026-02-16  
