# TREFA MCP Server

Servidor MCP (Model Context Protocol) para permitir que agentes de IA interactúen con la base de datos de Autos TREFA.

## Características

### Herramientas de Inventario
- `buscar_vehiculos` - Búsqueda avanzada de vehículos con filtros
- `obtener_vehiculo` - Detalles completos de un vehículo
- `buscar_alternativas` - Recomendaciones de vehículos similares
- `comparar_vehiculos` - Comparación lado a lado
- `estadisticas_inventario` - Resumen del inventario actual

### Herramientas de Financiamiento
- `obtener_solicitud_financiamiento` - Estado de solicitudes
- `listar_solicitudes_usuario` - Historial de solicitudes
- `actualizar_status_solicitud` - Cambiar estado (admin)
- `verificar_documentos` - Documentos subidos/faltantes
- `obtener_perfil_bancario` - Perfilación crediticia
- `calcular_financiamiento` - Calculadora de mensualidades

### Herramientas de Perfiles
- `buscar_usuario` - Buscar clientes
- `obtener_perfil_completo` - Información completa del usuario
- `verificar_completitud_perfil` - Campos completos/faltantes
- `obtener_historial_usuario` - Actividad del cliente

### Información del Negocio
- `buscar_informacion` - Búsqueda en base de conocimiento
- `obtener_info_negocio` - Información por tema
- `obtener_faqs` - Preguntas frecuentes

## Instalación

```bash
cd mcp-server
npm install
```

## Configuración

Copia `.env.example` a `.env` y configura las variables:

```bash
cp .env.example .env
```

## Uso

### Desarrollo
```bash
npm run dev
```

### Producción
```bash
npm run build
npm start
```

## Integración con Claude Desktop

Agrega a tu `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trefa": {
      "command": "node",
      "args": ["/ruta/a/trefa-app/mcp-server/dist/index.js"],
      "env": {
        "SUPABASE_URL": "https://tu-proyecto.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "tu-service-role-key"
      }
    }
  }
}
```

## Prompts del Sistema

El servidor incluye prompts predefinidos:

- `asistente-ventas` - Para atención a clientes y ventas
- `asistente-financiamiento` - Especializado en financiamiento

## Arquitectura

```
mcp-server/
├── src/
│   ├── index.ts          # Servidor principal
│   ├── tools/
│   │   ├── inventory.ts  # Herramientas de inventario
│   │   ├── financing.ts  # Herramientas de financiamiento
│   │   ├── profiles.ts   # Herramientas de perfiles
│   │   └── knowledge.ts  # Base de conocimiento
│   └── utils/
│       └── supabase.ts   # Cliente de Supabase
├── package.json
└── tsconfig.json
```

## Seguridad

- El server usa `SUPABASE_SERVICE_ROLE_KEY` para acceso completo
- Las herramientas de escritura (actualizar status) son solo para admins
- No se expone información sensible de otros clientes

## Próximos Pasos

1. Implementar pgvector para búsqueda semántica
2. Agregar autenticación por usuario
3. Implementar rate limiting
4. Agregar logging y métricas
