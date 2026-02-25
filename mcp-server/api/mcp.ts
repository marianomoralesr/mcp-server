import type { VercelRequest, VercelResponse } from '@vercel/node';
import { randomUUID } from 'node:crypto';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js';
import * as z from 'zod';

// Importar herramientas
import {
  buscarVehiculos,
  BuscarVehiculosSchema,
  obtenerVehiculo,
  ObtenerVehiculoSchema,
  buscarAlternativas,
  BuscarAlternativasSchema,
  compararVehiculos,
  CompararVehiculosSchema,
  obtenerEstadisticasInventario,
} from '../src/tools/inventory.js';

import {
  obtenerSolicitud,
  ObtenerSolicitudSchema,
  listarSolicitudesUsuario,
  ListarSolicitudesUsuarioSchema,
  actualizarStatusSolicitud,
  ActualizarStatusSolicitudSchema,
  verificarDocumentos,
  VerificarDocumentosSchema,
  obtenerPerfilBancario,
  ObtenerPerfilBancarioSchema,
  calcularFinanciamiento,
  CalcularFinanciamientoSchema,
} from '../src/tools/financing.js';

import {
  buscarUsuario,
  BuscarUsuarioSchema,
  obtenerPerfilCompleto,
  ObtenerPerfilCompletoSchema,
  verificarCompletitudPerfil,
  VerificarCompletitudPerfilSchema,
  obtenerHistorialUsuario,
  ObtenerHistorialUsuarioSchema,
} from '../src/tools/profiles.js';

import {
  buscarInformacion,
  BuscarInformacionSchema,
  obtenerInfoNegocio,
  ObtenerInfoNegocioSchema,
  obtenerFAQs,
} from '../src/tools/knowledge.js';

// ============================================================================
// CONFIGURACIÓN
// ============================================================================

const API_KEY = process.env.MCP_API_KEY;

// Almacenamiento en memoria para sesiones (limitación de serverless)
// En producción considera usar Redis/KV store
const transports: Map<string, StreamableHTTPServerTransport> = new Map();

// ============================================================================
// CREAR SERVIDOR MCP
// ============================================================================

function createMcpServer(): McpServer {
  const server = new McpServer({
    name: 'trefa-mcp-server',
    version: '1.0.0',
  });

  // === HERRAMIENTAS DE INVENTARIO ===

  server.tool(
    'buscar_vehiculos',
    `Busca vehículos en el inventario de Autos TREFA.`,
    {
      marca: z.string().optional().describe('Marca del vehículo'),
      modelo: z.string().optional().describe('Modelo específico'),
      año_minimo: z.number().optional().describe('Año mínimo'),
      año_maximo: z.number().optional().describe('Año máximo'),
      precio_minimo: z.number().optional().describe('Precio mínimo en MXN'),
      precio_maximo: z.number().optional().describe('Precio máximo en MXN'),
      tipo_carroceria: z.string().optional().describe('Tipo: SUV, Sedan, Hatchback, Pickup, Van'),
      transmision: z.string().optional().describe('Automática o Manual'),
      combustible: z.string().optional().describe('Gasolina, Diesel, Híbrido, Eléctrico'),
      kilometraje_maximo: z.number().optional().describe('Kilometraje máximo'),
      con_oferta: z.boolean().optional().describe('Solo vehículos en oferta'),
      limite: z.number().optional().describe('Número máximo de resultados'),
    },
    async (args) => {
      const result = await buscarVehiculos(BuscarVehiculosSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'obtener_vehiculo',
    `Obtiene información detallada de un vehículo específico.`,
    {
      id: z.number().optional().describe('ID numérico del vehículo'),
      slug: z.string().optional().describe('Slug URL del vehículo'),
      record_id: z.string().optional().describe('Record ID'),
    },
    async (args) => {
      const result = await obtenerVehiculo(ObtenerVehiculoSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'buscar_alternativas',
    `Busca vehículos alternativos cuando no hay el modelo exacto.`,
    {
      marca_original: z.string().describe('Marca que buscaba'),
      modelo_original: z.string().optional().describe('Modelo que buscaba'),
      presupuesto: z.number().describe('Presupuesto máximo en MXN'),
      prioridad: z.enum(['precio', 'año', 'equipamiento', 'marca_premium']).optional(),
      tipo_uso: z.string().optional().describe('Uso: familia, trabajo, ciudad'),
    },
    async (args) => {
      const result = await buscarAlternativas(BuscarAlternativasSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'comparar_vehiculos',
    `Compara 2-4 vehículos lado a lado.`,
    {
      vehiculo_ids: z.array(z.number()).min(2).max(4).describe('IDs de vehículos'),
    },
    async (args) => {
      const result = await compararVehiculos(CompararVehiculosSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'estadisticas_inventario',
    `Obtiene estadísticas generales del inventario.`,
    {},
    async () => {
      const result = await obtenerEstadisticasInventario();
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // === HERRAMIENTAS DE FINANCIAMIENTO ===

  server.tool(
    'obtener_solicitud_financiamiento',
    `Obtiene el estado de una solicitud de financiamiento.`,
    {
      application_id: z.string().optional().describe('ID de la solicitud'),
      user_id: z.string().optional().describe('ID del usuario'),
    },
    async (args) => {
      const result = await obtenerSolicitud(ObtenerSolicitudSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'listar_solicitudes_usuario',
    `Lista todas las solicitudes de un usuario.`,
    {
      user_id: z.string().describe('ID del usuario'),
      status: z.string().optional().describe('Filtrar por status'),
    },
    async (args) => {
      const result = await listarSolicitudesUsuario(ListarSolicitudesUsuarioSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'actualizar_status_solicitud',
    `Actualiza el status de una solicitud. SOLO ADMIN.`,
    {
      application_id: z.string().describe('ID de la solicitud'),
      nuevo_status: z.enum([
        'pendiente_documentos', 'documentos_completos', 'en_revision_banco',
        'pre_aprobada', 'aprobada', 'rechazada', 'firmada', 'completada', 'cancelada'
      ]),
      notas: z.string().optional().describe('Notas'),
    },
    async (args) => {
      const result = await actualizarStatusSolicitud(ActualizarStatusSolicitudSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'verificar_documentos',
    `Verifica qué documentos ha subido el cliente.`,
    {
      application_id: z.string().describe('ID de la solicitud'),
    },
    async (args) => {
      const result = await verificarDocumentos(VerificarDocumentosSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'obtener_perfil_bancario',
    `Obtiene el resultado de la perfilación bancaria.`,
    {
      user_id: z.string().describe('ID del usuario'),
    },
    async (args) => {
      const result = await obtenerPerfilBancario(ObtenerPerfilBancarioSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'calcular_financiamiento',
    `Calcula mensualidades estimadas para un vehículo.`,
    {
      precio_vehiculo: z.number().describe('Precio del vehículo en MXN'),
      enganche_porcentaje: z.number().optional().describe('Porcentaje de enganche'),
      plazo_meses: z.number().optional().describe('Plazo en meses'),
      tasa_anual: z.number().optional().describe('Tasa de interés anual'),
    },
    async (args) => {
      const result = await calcularFinanciamiento(CalcularFinanciamientoSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // === HERRAMIENTAS DE PERFILES ===

  server.tool(
    'buscar_usuario',
    `Busca un usuario. SOLO ADMIN.`,
    {
      email: z.string().optional().describe('Email'),
      telefono: z.string().optional().describe('Teléfono'),
      nombre: z.string().optional().describe('Nombre'),
      user_id: z.string().optional().describe('ID'),
    },
    async (args) => {
      const result = await buscarUsuario(BuscarUsuarioSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'obtener_perfil_completo',
    `Obtiene toda la información de un usuario.`,
    {
      user_id: z.string().describe('ID del usuario'),
    },
    async (args) => {
      const result = await obtenerPerfilCompleto(ObtenerPerfilCompletoSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'verificar_completitud_perfil',
    `Verifica si el perfil está completo para financiamiento.`,
    {
      user_id: z.string().describe('ID del usuario'),
    },
    async (args) => {
      const result = await verificarCompletitudPerfil(VerificarCompletitudPerfilSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'obtener_historial_usuario',
    `Obtiene el historial completo del usuario.`,
    {
      user_id: z.string().describe('ID del usuario'),
    },
    async (args) => {
      const result = await obtenerHistorialUsuario(ObtenerHistorialUsuarioSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  // === HERRAMIENTAS DE CONOCIMIENTO ===

  server.tool(
    'buscar_informacion',
    `Busca información en la base de conocimiento de TREFA.`,
    {
      pregunta: z.string().describe('Pregunta o tema'),
      categoria: z.enum(['faq', 'politicas', 'procesos', 'financiamiento', 'garantias', 'general']).optional(),
    },
    async (args) => {
      const result = await buscarInformacion(BuscarInformacionSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'obtener_info_negocio',
    `Obtiene información específica del negocio.`,
    {
      tema: z.enum([
        'horarios', 'ubicaciones', 'contacto', 'garantias', 'financiamiento',
        'documentos_requeridos', 'proceso_compra', 'devoluciones', 'intercambio', 'servicios'
      ]),
    },
    async (args) => {
      const result = await obtenerInfoNegocio(ObtenerInfoNegocioSchema.parse(args));
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    'obtener_faqs',
    `Obtiene preguntas frecuentes.`,
    {
      categoria: z.string().optional().describe('Categoría'),
    },
    async (args) => {
      const result = await obtenerFAQs(args?.categoria);
      return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }
  );

  return server;
}

// ============================================================================
// CORS HEADERS
// ============================================================================

function setCorsHeaders(res: VercelResponse) {
  res.setHeader('Access-Control-Allow-Origin', process.env.ALLOWED_ORIGINS || '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, mcp-session-id');
}

// ============================================================================
// AUTENTICACIÓN
// ============================================================================

function checkAuth(req: VercelRequest): boolean {
  if (!API_KEY) return true;

  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) return false;

  return authHeader.substring(7) === API_KEY;
}

// ============================================================================
// HANDLER PRINCIPAL
// ============================================================================

export default async function handler(req: VercelRequest, res: VercelResponse) {
  setCorsHeaders(res);

  // Preflight CORS
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  // Verificar autenticación
  if (!checkAuth(req)) {
    return res.status(401).json({
      jsonrpc: '2.0',
      error: { code: -32001, message: 'Autenticación requerida' },
      id: null,
    });
  }

  const sessionId = req.headers['mcp-session-id'] as string | undefined;

  try {
    if (req.method === 'POST') {
      let transport: StreamableHTTPServerTransport;

      if (sessionId && transports.has(sessionId)) {
        transport = transports.get(sessionId)!;
      } else if (!sessionId && isInitializeRequest(req.body)) {
        transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (id) => {
            transports.set(id, transport);
          },
        });

        transport.onclose = () => {
          if (transport.sessionId) {
            transports.delete(transport.sessionId);
          }
        };

        const server = createMcpServer();
        await server.connect(transport);
      } else {
        return res.status(400).json({
          jsonrpc: '2.0',
          error: { code: -32000, message: 'Sesión inválida' },
          id: null,
        });
      }

      // Convertir VercelRequest/Response para el transport
      await transport.handleRequest(req as any, res as any, req.body);
    } else if (req.method === 'GET') {
      if (!sessionId || !transports.has(sessionId)) {
        return res.status(400).json({
          jsonrpc: '2.0',
          error: { code: -32000, message: 'Sesión no encontrada' },
          id: null,
        });
      }

      const transport = transports.get(sessionId)!;
      await transport.handleRequest(req as any, res as any);
    } else if (req.method === 'DELETE') {
      if (!sessionId || !transports.has(sessionId)) {
        return res.status(400).json({
          jsonrpc: '2.0',
          error: { code: -32000, message: 'Sesión no encontrada' },
          id: null,
        });
      }

      const transport = transports.get(sessionId)!;
      await transport.handleRequest(req as any, res as any);
    } else {
      return res.status(405).json({
        jsonrpc: '2.0',
        error: { code: -32601, message: 'Método no permitido' },
        id: null,
      });
    }
  } catch (error) {
    console.error('Error en MCP handler:', error);
    return res.status(500).json({
      jsonrpc: '2.0',
      error: { code: -32603, message: 'Error interno del servidor' },
      id: null,
    });
  }
}
