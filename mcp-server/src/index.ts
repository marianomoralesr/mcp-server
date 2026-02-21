#!/usr/bin/env node

import 'dotenv/config';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

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

// ============================================================================
// DEFINICIÓN DE LAS 11 HERRAMIENTAS
// ============================================================================

const TOOLS = [
  // 1. buscar_vehiculos
  {
    name: 'buscar_vehiculos',
    description: `Busca vehículos en el inventario de Autos TREFA con filtros opcionales.

CUÁNDO USAR: Cuando el cliente quiere ver opciones de autos con criterios específicos.
EJEMPLOS:
- "Busco una SUV Toyota" → marca: "Toyota", tipo_carroceria: "SUV"
- "Autos menores a 300 mil" → precio_maximo: 300000`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        marca: { type: 'string', description: 'Marca del vehículo (ej: Toyota, Honda, Mazda)' },
        modelo: { type: 'string', description: 'Modelo específico (ej: Corolla, Civic, CX-5)' },
        año_minimo: { type: 'number', description: 'Año mínimo del vehículo' },
        año_maximo: { type: 'number', description: 'Año máximo del vehículo' },
        precio_minimo: { type: 'number', description: 'Precio mínimo en MXN' },
        precio_maximo: { type: 'number', description: 'Precio máximo en MXN' },
        tipo_carroceria: { type: 'string', description: 'Tipo: SUV, Sedán, Hatchback, Pick Up, Van' },
        transmision: { type: 'string', description: 'Automática o Manual' },
        combustible: { type: 'string', description: 'Gasolina, Diesel, Híbrido, Eléctrico' },
        ubicacion: { type: 'string', description: 'Sucursal: Monterrey, Guadalupe, Saltillo, Reynosa' },
        kilometraje_max: { type: 'number', description: 'Kilometraje máximo (ej: 50000)' },
        garantia: { type: 'string', description: 'Tipo de garantía: Agencia, 365 días, 90 días, Sin Garantía' },
        motor: { type: 'string', description: 'Motor del vehículo (ej: 2.0L, 1.5L)' },
        limite: { type: 'number', description: 'Máximo de resultados (default: 5)' },
      },
    },
  },

  // 2. obtener_vehiculo
  {
    name: 'obtener_vehiculo',
    description: `Obtiene el detalle completo de un vehículo específico.

CUÁNDO USAR: Cuando el cliente pregunta por un auto en particular o quiere más detalles.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        id: { type: 'number', description: 'ID numérico del vehículo' },
        slug: { type: 'string', description: 'Slug URL del vehículo' },
      },
    },
  },

  // 3. buscar_alternativas
  {
    name: 'buscar_alternativas',
    description: `Busca alternativas cuando no hay el modelo exacto que busca el cliente.

CUÁNDO USAR: Cuando NO se encuentra el modelo específico, o el cliente está abierto a opciones.
Busca vehículos de OTRAS marcas en un rango de ±20% del presupuesto.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        marca_original: { type: 'string', description: 'Marca que el cliente buscaba' },
        presupuesto: { type: 'number', description: 'Presupuesto máximo en MXN' },
        modelo_original: { type: 'string', description: 'Modelo que buscaba' },
        tipo_uso: { type: 'string', description: 'Para qué usará: familia, trabajo, ciudad, carretera' },
        carroceria: { type: 'string', description: 'Tipo preferido: SUV, Sedán, Hatchback, Pick Up, Van' },
        ubicacion: { type: 'string', description: 'Sucursal preferida: Monterrey, Guadalupe, Saltillo, Reynosa' },
      },
      required: ['marca_original', 'presupuesto'],
    },
  },

  // 4. comparar_vehiculos
  {
    name: 'comparar_vehiculos',
    description: `Compara 2-4 vehículos lado a lado.

CUÁNDO USAR: Cuando el cliente está indeciso entre varias opciones.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        vehiculo_ids: {
          type: 'array',
          items: { type: 'number' },
          minItems: 2,
          maxItems: 4,
          description: 'IDs de los vehículos a comparar (2-4)',
        },
      },
      required: ['vehiculo_ids'],
    },
  },

  // 5. estadisticas_inventario
  {
    name: 'estadisticas_inventario',
    description: `Estadísticas generales del inventario: total, rango de precios, rango de años, marcas.

CUÁNDO USAR: "¿Qué marcas tienen?", "¿Cuántos autos hay?", "¿Cuál es el rango de precios?"`,
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },

  // 6. calcular_financiamiento
  {
    name: 'calcular_financiamiento',
    description: `Calcula mensualidades estimadas para un vehículo.

CUÁNDO USAR: "¿Cuánto pagaría mensualmente?", "¿Cuál sería mi mensualidad con 30% de enganche?"
Defaults: enganche 20%, plazo 48 meses, tasa 15% anual.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        precio_vehiculo: { type: 'number', description: 'Precio del vehículo en MXN (opcional si se pasa vehiculo_id)' },
        vehiculo_id: { type: 'number', description: 'ID del vehículo para usar datos reales de financiamiento' },
        enganche_porcentaje: { type: 'number', description: 'Porcentaje de enganche (default: 20)' },
        plazo_meses: { type: 'number', description: 'Plazo en meses (default: 48)' },
        tasa_anual: { type: 'number', description: 'Tasa de interés anual en % (default: 15)' },
      },
    },
  },

  // 7. buscar_informacion
  {
    name: 'buscar_informacion',
    description: `Busca en la base de conocimiento de TREFA.

CUÁNDO USAR: Para cualquier pregunta sobre políticas, compras, garantías, financiamiento, etc.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        pregunta: { type: 'string', description: 'Pregunta o tema a buscar' },
        categoria: {
          type: 'string',
          enum: ['faq', 'politicas', 'compras', 'financiamiento', 'garantias', 'general'],
          description: 'Categoría opcional para filtrar',
        },
      },
      required: ['pregunta'],
    },
  },

  // 8. obtener_info_negocio
  {
    name: 'obtener_info_negocio',
    description: `Obtiene información específica del negocio por tema.

TEMAS: horarios, ubicaciones, contacto, garantias, financiamiento, documentos_requeridos, proceso_compra, devoluciones, intercambio, servicios`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        tema: {
          type: 'string',
          enum: ['horarios', 'ubicaciones', 'contacto', 'garantias', 'financiamiento', 'documentos_requeridos', 'proceso_compra', 'devoluciones', 'intercambio', 'servicios'],
          description: 'Tema de información',
        },
      },
      required: ['tema'],
    },
  },

  // 9. obtener_faqs
  {
    name: 'obtener_faqs',
    description: `Obtiene preguntas frecuentes, opcionalmente filtradas por categoría.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        categoria: { type: 'string', description: 'Categoría para filtrar FAQs' },
      },
    },
  },

  // 10. solicitar_datos_contacto
  {
    name: 'solicitar_datos_contacto',
    description: `Registra datos de contacto del cliente para seguimiento por un asesor.

CUÁNDO USAR: Cuando el cliente quiere ser contactado, dejar sus datos, o agendar una cita.`,
    inputSchema: {
      type: 'object' as const,
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

  // 11. enviar_cotizacion_email
  {
    name: 'enviar_cotizacion_email',
    description: `Envía una cotización formal por email al cliente.

CUÁNDO USAR: Cuando el cliente solicita que le envíen la cotización por correo.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        email_destino: { type: 'string', description: 'Email del cliente' },
        nombre_cliente: { type: 'string', description: 'Nombre del cliente' },
        vehiculo_id: { type: 'number', description: 'ID del vehículo a cotizar' },
        enganche_porcentaje: { type: 'number', description: 'Porcentaje de enganche (default: 20)' },
        plazo_meses: { type: 'number', description: 'Plazo en meses (default: 48)' },
      },
      required: ['email_destino', 'nombre_cliente', 'vehiculo_id'],
    },
  },
];

// ============================================================================
// SERVIDOR MCP
// ============================================================================

const server = new Server(
  {
    name: 'trefa-mcp-server',
    version: '2.0.0',
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

// Handler para listar herramientas
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return { tools: TOOLS };
});

// Handler para ejecutar herramientas
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    let result: any;

    switch (name) {
      case 'buscar_vehiculos':
        result = await buscarVehiculos(BuscarVehiculosSchema.parse(args));
        break;
      case 'obtener_vehiculo':
        result = await obtenerVehiculo(ObtenerVehiculoSchema.parse(args));
        break;
      case 'buscar_alternativas':
        result = await buscarAlternativas(BuscarAlternativasSchema.parse(args));
        break;
      case 'comparar_vehiculos':
        result = await compararVehiculos(CompararVehiculosSchema.parse(args));
        break;
      case 'estadisticas_inventario':
        result = await estadisticasInventario();
        break;
      case 'calcular_financiamiento':
        result = await calcularFinanciamiento(CalcularFinanciamientoSchema.parse(args));
        break;
      case 'buscar_informacion':
        result = await buscarInformacion(BuscarInformacionSchema.parse(args));
        break;
      case 'obtener_info_negocio':
        result = await obtenerInfoNegocio(ObtenerInfoNegocioSchema.parse(args));
        break;
      case 'obtener_faqs':
        result = await obtenerFaqs(ObtenerFaqsSchema.parse(args || {}));
        break;
      case 'solicitar_datos_contacto':
        result = await solicitarDatosContacto(SolicitarDatosContactoSchema.parse(args));
        break;
      case 'enviar_cotizacion_email':
        result = await enviarCotizacionEmail(EnviarCotizacionEmailSchema.parse(args));
        break;
      default:
        throw new Error(`Herramienta desconocida: ${name}`);
    }

    return {
      content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
    };
  } catch (error) {
    const mensaje = error instanceof Error ? error.message : 'Error desconocido';
    console.error(`[MCP] Error en ${name}:`, mensaje);
    return {
      content: [{ type: 'text', text: JSON.stringify({ error: mensaje }) }],
      isError: true,
    };
  }
});

// Iniciar servidor
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('TREFA MCP Server v2.0.0 iniciado (stdio)');
  console.error(`Herramientas disponibles: ${TOOLS.length}`);
}

main().catch((error) => {
  console.error('Error fatal:', error);
  process.exit(1);
});
