#!/usr/bin/env node

import 'dotenv/config';
import express, { Request, Response } from 'express';
import cors from 'cors';
import { randomUUID } from 'node:crypto';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js';
import * as z from 'zod';

// Importar herramientas
import { buscarVehiculos, BuscarVehiculosSchema } from './tools/buscarVehiculos.js';
import { obtenerVehiculo, ObtenerVehiculoSchema } from './tools/obtenerVehiculo.js';
import { buscarAlternativas, BuscarAlternativasSchema } from './tools/buscarAlternativas.js';
import { compararVehiculos, CompararVehiculosSchema } from './tools/compararVehiculos.js';
import { estadisticasInventario } from './tools/estadisticasInventario.js';
import { calcularFinanciamiento, CalcularFinanciamientoSchema } from './tools/calcularFinanciamiento.js';
import { buscarInformacion, BuscarInformacionSchema } from './tools/buscarInformacion.js';
import { obtenerInfoNegocio, ObtenerInfoNegocioSchema } from './tools/obtenerInfoNegocio.js';
import { obtenerFaqs, ObtenerFaqsSchema } from './tools/obtenerFaqs.js';
import { solicitarDatosContacto, SolicitarDatosContactoSchema } from './tools/solicitarDatosContacto.js';
import { enviarCotizacionEmail, EnviarCotizacionEmailSchema } from './tools/enviarCotizacionEmail.js';
import { agendarCita, AgendarCitaSchema } from './tools/agendarCita.js';

// ============================================================================
// CONFIGURACIÓN
// ============================================================================

const PORT = process.env.PORT || 3001;
const API_KEY = process.env.MCP_API_KEY;

// ============================================================================
// TOOL REGISTRY — Mapeo nombre → handler + schema + definición
// ============================================================================

interface ToolRegistryEntry {
  handler: (args: any) => Promise<any>;
  schema?: z.ZodSchema;
  definition: {
    name: string;
    description: string;
    inputSchema: object;
  };
}

const TOOL_REGISTRY: Record<string, ToolRegistryEntry> = {
  buscar_vehiculos: {
    handler: (args) => buscarVehiculos(BuscarVehiculosSchema.parse(args)),
    schema: BuscarVehiculosSchema,
    definition: {
      name: 'buscar_vehiculos',
      description: 'Busca vehículos en el inventario con filtros opcionales (marca, modelo, año, precio, carrocería, transmisión, combustible).',
      inputSchema: {
        type: 'object',
        properties: {
          marca: { type: 'string', description: 'Marca del vehículo' },
          modelo: { type: 'string', description: 'Modelo específico' },
          año_minimo: { type: 'number', description: 'Año mínimo' },
          año_maximo: { type: 'number', description: 'Año máximo' },
          precio_minimo: { type: 'number', description: 'Precio mínimo en MXN' },
          precio_maximo: { type: 'number', description: 'Precio máximo en MXN' },
          tipo_carroceria: { type: 'string', description: 'SUV, Sedan, Hatchback, Pickup, Van' },
          transmision: { type: 'string', description: 'Automática o Manual' },
          combustible: { type: 'string', description: 'Gasolina, Diesel, Híbrido, Eléctrico' },
          limite: { type: 'number', description: 'Máximo de resultados (default: 5)' },
        },
      },
    },
  },
  obtener_vehiculo: {
    handler: (args) => obtenerVehiculo(ObtenerVehiculoSchema.parse(args)),
    schema: ObtenerVehiculoSchema,
    definition: {
      name: 'obtener_vehiculo',
      description: 'Detalle completo de un vehículo específico por id o slug.',
      inputSchema: {
        type: 'object',
        properties: {
          id: { type: 'number', description: 'ID numérico del vehículo' },
          slug: { type: 'string', description: 'Slug URL del vehículo' },
        },
      },
    },
  },
  buscar_alternativas: {
    handler: (args) => buscarAlternativas(BuscarAlternativasSchema.parse(args)),
    schema: BuscarAlternativasSchema,
    definition: {
      name: 'buscar_alternativas',
      description: 'Busca alternativas de otras marcas cuando no hay el modelo exacto. Rango ±20% del presupuesto.',
      inputSchema: {
        type: 'object',
        properties: {
          marca_original: { type: 'string', description: 'Marca que el cliente buscaba' },
          presupuesto: { type: 'number', description: 'Presupuesto máximo en MXN' },
          modelo_original: { type: 'string', description: 'Modelo que buscaba' },
          tipo_uso: { type: 'string', description: 'Uso: familia, trabajo, ciudad, carretera' },
        },
        required: ['marca_original', 'presupuesto'],
      },
    },
  },
  comparar_vehiculos: {
    handler: (args) => compararVehiculos(CompararVehiculosSchema.parse(args)),
    schema: CompararVehiculosSchema,
    definition: {
      name: 'comparar_vehiculos',
      description: 'Compara 2-4 vehículos lado a lado.',
      inputSchema: {
        type: 'object',
        properties: {
          vehiculo_ids: { type: 'array', items: { type: 'number' }, minItems: 2, maxItems: 4, description: 'IDs de los vehículos a comparar (2-4)' },
        },
        required: ['vehiculo_ids'],
      },
    },
  },
  estadisticas_inventario: {
    handler: () => estadisticasInventario(),
    definition: {
      name: 'estadisticas_inventario',
      description: 'Estadísticas generales: total vehículos, rangos de precio/año, marcas disponibles.',
      inputSchema: { type: 'object', properties: {} },
    },
  },
  calcular_financiamiento: {
    handler: (args) => calcularFinanciamiento(CalcularFinanciamientoSchema.parse(args)),
    schema: CalcularFinanciamientoSchema,
    definition: {
      name: 'calcular_financiamiento',
      description: 'Calcula mensualidades estimadas. Defaults: enganche 20%, plazo 48 meses, tasa 15%.',
      inputSchema: {
        type: 'object',
        properties: {
          precio_vehiculo: { type: 'number', description: 'Precio del vehículo en MXN' },
          enganche_porcentaje: { type: 'number', description: 'Porcentaje de enganche (default: 20)' },
          plazo_meses: { type: 'number', description: 'Plazo en meses (default: 48)' },
          tasa_anual: { type: 'number', description: 'Tasa de interés anual en % (default: 15)' },
        },
        required: ['precio_vehiculo'],
      },
    },
  },
  buscar_informacion: {
    handler: (args) => buscarInformacion(BuscarInformacionSchema.parse(args)),
    schema: BuscarInformacionSchema,
    definition: {
      name: 'buscar_informacion',
      description: 'Busca en la base de conocimiento de TREFA (políticas, procesos, garantías, etc.).',
      inputSchema: {
        type: 'object',
        properties: {
          pregunta: { type: 'string', description: 'Pregunta o tema a buscar' },
          categoria: { type: 'string', enum: ['faq', 'politicas', 'procesos', 'financiamiento', 'garantias', 'general'], description: 'Categoría opcional' },
        },
        required: ['pregunta'],
      },
    },
  },
  obtener_info_negocio: {
    handler: (args) => obtenerInfoNegocio(ObtenerInfoNegocioSchema.parse(args)),
    schema: ObtenerInfoNegocioSchema,
    definition: {
      name: 'obtener_info_negocio',
      description: 'Info específica del negocio: horarios, ubicaciones, contacto, garantias, financiamiento, documentos, proceso de compra, devoluciones, intercambio, servicios.',
      inputSchema: {
        type: 'object',
        properties: {
          tema: { type: 'string', enum: ['horarios', 'ubicaciones', 'contacto', 'garantias', 'financiamiento', 'documentos_requeridos', 'proceso_compra', 'devoluciones', 'intercambio', 'servicios'], description: 'Tema de información' },
        },
        required: ['tema'],
      },
    },
  },
  obtener_faqs: {
    handler: (args) => obtenerFaqs(ObtenerFaqsSchema.parse(args || {})),
    schema: ObtenerFaqsSchema,
    definition: {
      name: 'obtener_faqs',
      description: 'Preguntas frecuentes, opcionalmente filtradas por categoría.',
      inputSchema: {
        type: 'object',
        properties: {
          categoria: { type: 'string', description: 'Categoría para filtrar FAQs' },
        },
      },
    },
  },
  solicitar_datos_contacto: {
    handler: (args) => solicitarDatosContacto(SolicitarDatosContactoSchema.parse(args)),
    schema: SolicitarDatosContactoSchema,
    definition: {
      name: 'solicitar_datos_contacto',
      description: 'Registra datos de contacto del cliente para seguimiento por un asesor.',
      inputSchema: {
        type: 'object',
        properties: {
          nombre: { type: 'string', description: 'Nombre del cliente' },
          telefono: { type: 'string', description: 'Teléfono del cliente' },
          email: { type: 'string', description: 'Email del cliente' },
          vehiculo_interes: { type: 'string', description: 'Vehículo de interés' },
          comentarios: { type: 'string', description: 'Comentarios adicionales' },
        },
        required: ['nombre', 'telefono'],
      },
    },
  },
  enviar_cotizacion_email: {
    handler: (args) => enviarCotizacionEmail(EnviarCotizacionEmailSchema.parse(args)),
    schema: EnviarCotizacionEmailSchema,
    definition: {
      name: 'enviar_cotizacion_email',
      description: 'Envía cotización formal por email con datos del vehículo y financiamiento.',
      inputSchema: {
        type: 'object',
        properties: {
          email_destino: { type: 'string', description: 'Email del cliente' },
          nombre_cliente: { type: 'string', description: 'Nombre del cliente' },
          id: { type: 'number', description: 'ID del vehículo a cotizar' },
          enganche_porcentaje: { type: 'number', description: 'Porcentaje de enganche (default: 20)' },
          plazo_meses: { type: 'number', description: 'Plazo en meses (default: 48)' },
        },
        required: ['email_destino', 'nombre_cliente', 'id'],
      },
    },
  },
  agendar_cita: {
    handler: (args) => agendarCita(AgendarCitaSchema.parse(args)),
    schema: AgendarCitaSchema,
    definition: {
      name: 'agendar_cita',
      description: 'Agenda una visita del cliente a una sucursal de Autos TREFA. Valida horarios y disponibilidad. Notifica automáticamente al equipo por Telegram.',
      inputSchema: {
        type: 'object',
        properties: {
          nombre: { type: 'string', description: 'Nombre del cliente' },
          telefono: { type: 'string', description: 'Teléfono del cliente' },
          email: { type: 'string', description: 'Email del cliente' },
          sucursal: { type: 'string', enum: ['Monterrey', 'Guadalupe', 'Saltillo', 'Reynosa'], description: 'Sucursal para la visita' },
          fecha: { type: 'string', description: 'Fecha propuesta (YYYY-MM-DD)' },
          hora: { type: 'string', description: 'Hora propuesta (HH:MM)' },
          vehiculo_interes: { type: 'string', description: 'Vehículo de interés del cliente' },
          comentarios: { type: 'string', description: 'Comentarios adicionales' },
        },
        required: ['nombre', 'telefono', 'sucursal', 'fecha', 'hora'],
      },
    },
  },
};

// ============================================================================
// CREAR Y CONFIGURAR SERVIDOR MCP
// ============================================================================

function createMcpServer(): McpServer {
  const server = new McpServer({
    name: 'trefa-mcp-server',
    version: '2.0.0',
  });

  // 1. buscar_vehiculos
  server.tool(
    'buscar_vehiculos',
    'Busca vehículos en el inventario con filtros opcionales (marca, modelo, año, precio, carrocería, transmisión, combustible).',
    {
      marca: z.string().optional().describe('Marca del vehículo'),
      modelo: z.string().optional().describe('Modelo específico'),
      año_minimo: z.number().optional().describe('Año mínimo'),
      año_maximo: z.number().optional().describe('Año máximo'),
      precio_minimo: z.number().optional().describe('Precio mínimo en MXN'),
      precio_maximo: z.number().optional().describe('Precio máximo en MXN'),
      tipo_carroceria: z.string().optional().describe('SUV, Sedan, Hatchback, Pickup, Van'),
      transmision: z.string().optional().describe('Automática o Manual'),
      combustible: z.string().optional().describe('Gasolina, Diesel, Híbrido, Eléctrico'),
      limite: z.number().optional().describe('Máximo de resultados (default: 5)'),
    },
    async (args) => {
      const result = await buscarVehiculos(BuscarVehiculosSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 2. obtener_vehiculo
  server.tool(
    'obtener_vehiculo',
    'Detalle completo de un vehículo específico por id o slug.',
    {
      id: z.number().optional().describe('ID numérico del vehículo'),
      slug: z.string().optional().describe('Slug URL del vehículo'),
    },
    async (args) => {
      const result = await obtenerVehiculo(ObtenerVehiculoSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 3. buscar_alternativas
  server.tool(
    'buscar_alternativas',
    'Busca alternativas de otras marcas cuando no hay el modelo exacto. Rango ±20% del presupuesto.',
    {
      marca_original: z.string().describe('Marca que el cliente buscaba'),
      presupuesto: z.number().describe('Presupuesto máximo en MXN'),
      modelo_original: z.string().optional().describe('Modelo que buscaba'),
      tipo_uso: z.string().optional().describe('Uso: familia, trabajo, ciudad, carretera'),
    },
    async (args) => {
      const result = await buscarAlternativas(BuscarAlternativasSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 4. comparar_vehiculos
  server.tool(
    'comparar_vehiculos',
    'Compara 2-4 vehículos lado a lado.',
    {
      vehiculo_ids: z.array(z.number()).min(2).max(4).describe('IDs de los vehículos a comparar'),
    },
    async (args) => {
      const result = await compararVehiculos(CompararVehiculosSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 5. estadisticas_inventario
  server.tool(
    'estadisticas_inventario',
    'Estadísticas generales: total vehículos, rangos de precio/año, marcas disponibles.',
    {},
    async () => {
      const result = await estadisticasInventario();
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 6. calcular_financiamiento
  server.tool(
    'calcular_financiamiento',
    'Calcula mensualidades estimadas. Defaults: enganche 20%, plazo 48 meses, tasa 15%.',
    {
      precio_vehiculo: z.number().describe('Precio del vehículo en MXN'),
      enganche_porcentaje: z.number().optional().describe('Porcentaje de enganche (default: 20)'),
      plazo_meses: z.number().optional().describe('Plazo en meses (default: 48)'),
      tasa_anual: z.number().optional().describe('Tasa anual en % (default: 15)'),
    },
    async (args) => {
      const result = await calcularFinanciamiento(CalcularFinanciamientoSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 7. buscar_informacion
  server.tool(
    'buscar_informacion',
    'Busca en la base de conocimiento de TREFA (políticas, procesos, garantías, etc.).',
    {
      pregunta: z.string().describe('Pregunta o tema a buscar'),
      categoria: z.enum(['faq', 'politicas', 'procesos', 'financiamiento', 'garantias', 'general']).optional(),
    },
    async (args) => {
      const result = await buscarInformacion(BuscarInformacionSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 8. obtener_info_negocio
  server.tool(
    'obtener_info_negocio',
    'Info específica del negocio: horarios, ubicaciones, contacto, garantias, financiamiento, documentos, proceso de compra, devoluciones, intercambio, servicios.',
    {
      tema: z.enum([
        'horarios', 'ubicaciones', 'contacto', 'garantias', 'financiamiento',
        'documentos_requeridos', 'proceso_compra', 'devoluciones', 'intercambio', 'servicios',
      ]).describe('Tema de información'),
    },
    async (args) => {
      const result = await obtenerInfoNegocio(ObtenerInfoNegocioSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 9. obtener_faqs
  server.tool(
    'obtener_faqs',
    'Preguntas frecuentes, opcionalmente filtradas por categoría.',
    {
      categoria: z.string().optional().describe('Categoría para filtrar FAQs'),
    },
    async (args) => {
      const result = await obtenerFaqs(ObtenerFaqsSchema.parse(args || {}));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 10. solicitar_datos_contacto
  server.tool(
    'solicitar_datos_contacto',
    'Registra datos de contacto del cliente para seguimiento por un asesor.',
    {
      nombre: z.string().describe('Nombre del cliente'),
      telefono: z.string().describe('Teléfono del cliente'),
      email: z.string().optional().describe('Email del cliente'),
      vehiculo_interes: z.string().optional().describe('Vehículo de interés'),
      comentarios: z.string().optional().describe('Comentarios adicionales'),
    },
    async (args) => {
      const result = await solicitarDatosContacto(SolicitarDatosContactoSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 11. enviar_cotizacion_email
  server.tool(
    'enviar_cotizacion_email',
    'Envía cotización formal por email con datos del vehículo y financiamiento.',
    {
      email_destino: z.string().describe('Email del cliente'),
      nombre_cliente: z.string().describe('Nombre del cliente'),
      id: z.number().describe('ID del vehículo a cotizar'),
      enganche_porcentaje: z.number().optional().describe('Porcentaje de enganche (default: 20)'),
      plazo_meses: z.number().optional().describe('Plazo en meses (default: 48)'),
    },
    async (args) => {
      const result = await enviarCotizacionEmail(EnviarCotizacionEmailSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 12. agendar_cita
  server.tool(
    'agendar_cita',
    'Agenda una visita del cliente a una sucursal de Autos TREFA. Valida horarios y disponibilidad. Notifica automáticamente al equipo por Telegram.',
    {
      nombre: z.string().describe('Nombre del cliente'),
      telefono: z.string().describe('Teléfono del cliente'),
      email: z.string().optional().describe('Email del cliente'),
      sucursal: z.enum(['Monterrey', 'Guadalupe', 'Saltillo', 'Reynosa']).describe('Sucursal para la visita'),
      fecha: z.string().describe('Fecha propuesta (YYYY-MM-DD)'),
      hora: z.string().describe('Hora propuesta (HH:MM)'),
      vehiculo_interes: z.string().optional().describe('Vehículo de interés del cliente'),
      comentarios: z.string().optional().describe('Comentarios adicionales'),
    },
    async (args) => {
      const result = await agendarCita(AgendarCitaSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  return server;
}

// ============================================================================
// SERVIDOR EXPRESS
// ============================================================================

const app = express();

app.use(cors({
  origin: process.env.ALLOWED_ORIGINS?.split(',') || '*',
  methods: ['GET', 'POST', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization', 'mcp-session-id'],
}));

app.use(express.json());

// Middleware de autenticación opcional
function authMiddleware(req: Request, res: Response, next: () => void) {
  if (!API_KEY) return next();

  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({
      jsonrpc: '2.0',
      error: { code: -32001, message: 'Autenticación requerida' },
      id: null,
    });
  }

  const token = authHeader.substring(7);
  if (token !== API_KEY) {
    return res.status(403).json({
      jsonrpc: '2.0',
      error: { code: -32002, message: 'Token inválido' },
      id: null,
    });
  }

  next();
}

// Sesiones activas
const transports: Record<string, StreamableHTTPServerTransport> = {};

// Health check
app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    server: 'trefa-mcp-server',
    version: '2.0.0',
    tools: Object.keys(TOOL_REGISTRY).length,
    timestamp: new Date().toISOString(),
  });
});

// ============================================================================
// ENDPOINTS REST — Para consumo directo por Edge Functions
// ============================================================================

// GET /tools — Listado de herramientas disponibles
app.get('/tools', (_req, res) => {
  const tools = Object.values(TOOL_REGISTRY).map((entry) => entry.definition);
  res.json({ tools });
});

// POST /tools/:toolName — Ejecución de herramienta por nombre
app.post('/tools/:toolName', authMiddleware, async (req: Request, res: Response) => {
  const toolName = req.params.toolName as string;
  const entry = TOOL_REGISTRY[toolName];

  if (!entry) {
    res.status(404).json({ error: `Herramienta no encontrada: ${toolName}` });
    return;
  }

  try {
    console.error(`[REST][${toolName}]`, JSON.stringify(req.body));
    const result = await entry.handler(req.body);
    res.json(result);
  } catch (err: any) {
    console.error(`[REST][${toolName}] Error:`, err.message || err);
    res.status(400).json({ error: err.message || 'Error al ejecutar herramienta' });
  }
});

// POST /mcp
app.post('/mcp', authMiddleware, async (req: Request, res: Response) => {
  const sessionId = req.headers['mcp-session-id'] as string | undefined;
  let transport: StreamableHTTPServerTransport;

  if (sessionId && transports[sessionId]) {
    transport = transports[sessionId];
  } else if (!sessionId && isInitializeRequest(req.body)) {
    transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: () => randomUUID(),
      onsessioninitialized: (id) => {
        transports[id] = transport;
        console.error('Sesión inicializada:', id);
      },
    });

    transport.onclose = () => {
      if (transport.sessionId) {
        delete transports[transport.sessionId];
        console.error('Sesión cerrada:', transport.sessionId);
      }
    };

    const server = createMcpServer();
    await server.connect(transport);
  } else {
    res.status(400).json({
      jsonrpc: '2.0',
      error: { code: -32000, message: 'Sesión inválida o no inicializada' },
      id: null,
    });
    return;
  }

  await transport.handleRequest(req, res, req.body);
});

// GET /mcp (SSE)
app.get('/mcp', authMiddleware, async (req: Request, res: Response) => {
  const sessionId = req.headers['mcp-session-id'] as string;
  const transport = transports[sessionId];

  if (transport) {
    await transport.handleRequest(req, res);
  } else {
    res.status(400).json({
      jsonrpc: '2.0',
      error: { code: -32000, message: 'Sesión no encontrada' },
      id: null,
    });
  }
});

// DELETE /mcp
app.delete('/mcp', authMiddleware, async (req: Request, res: Response) => {
  const sessionId = req.headers['mcp-session-id'] as string;
  const transport = transports[sessionId];

  if (transport) {
    await transport.handleRequest(req, res);
  } else {
    res.status(400).json({
      jsonrpc: '2.0',
      error: { code: -32000, message: 'Sesión no encontrada' },
      id: null,
    });
  }
});

// Iniciar servidor
app.listen(PORT, () => {
  console.error(`TREFA MCP Server v2.0.0 HTTP en http://localhost:${PORT}`);
  console.error(`Endpoint MCP:  http://localhost:${PORT}/mcp`);
  console.error(`REST tools:    http://localhost:${PORT}/tools`);
  console.error(`Health check:  http://localhost:${PORT}/health`);
  console.error(`Herramientas:  ${Object.keys(TOOL_REGISTRY).length}`);
  if (API_KEY) {
    console.error('Autenticación habilitada');
  } else {
    console.error('Sin autenticación — establece MCP_API_KEY para habilitar');
  }
});

export default app;
