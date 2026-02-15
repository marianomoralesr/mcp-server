import { z } from 'zod';
import { getSupabaseClient, formatPrice, formatKm, aplicarFiltrosBase } from '../lib/supabase.js';
import { normalizeMarca, normalizeModelo, getMarcasDisponibles } from '../lib/fuzzy.js';
import { normalizeAmount } from '../lib/normalize-amount.js';

export const BuscarVehiculosSchema = z.object({
  marca: z.string().optional().describe('Marca del vehículo (ej: Toyota, Honda, Mazda)'),
  modelo: z.string().optional().describe('Modelo específico (ej: Corolla, Civic, CX-5)'),
  año_minimo: z.number().optional().describe('Año mínimo del vehículo'),
  año_maximo: z.number().optional().describe('Año máximo del vehículo'),
  precio_minimo: z.number().optional().describe('Precio mínimo en MXN'),
  precio_maximo: z.number().optional().describe('Precio máximo en MXN'),
  tipo_carroceria: z.string().optional().describe('Tipo: SUV, Sedan, Hatchback, Pickup, Van'),
  transmision: z.string().optional().describe('Automática o Manual'),
  combustible: z.string().optional().describe('Gasolina, Diesel, Híbrido, Eléctrico'),
  limite: z.number().optional().default(5).describe('Número máximo de resultados (default: 5)'),
});

export async function buscarVehiculos(params: z.infer<typeof BuscarVehiculosSchema>) {
  try {
    const supabase = getSupabaseClient();
    const correcciones: string[] = [];
    let marcaSinCoincidencia = false;
    let modeloSinCoincidencia = false;

    // Normalizar marca
    if (params.marca) {
      const r = await normalizeMarca(params.marca);
      params.marca = r.corrected;
      if (r.wasCorrected) correcciones.push(`Marca: "${r.original}" → "${r.corrected}"`);
      if (r.sinCoincidencia) marcaSinCoincidencia = true;
    }

    // Normalizar modelo
    if (params.modelo) {
      const r = await normalizeModelo(params.modelo);
      params.modelo = r.corrected;
      if (r.wasCorrected) correcciones.push(`Modelo: "${r.original}" → "${r.corrected}"`);
      if (r.sinCoincidencia) modeloSinCoincidencia = true;
    }

    // Normalizar precios
    if (params.precio_minimo) {
      const r = normalizeAmount(params.precio_minimo, 'precio');
      if (r.wasNormalized) {
        correcciones.push(`Precio mínimo: ${r.interpretacion}`);
        params.precio_minimo = r.value;
      }
    }
    if (params.precio_maximo) {
      const r = normalizeAmount(params.precio_maximo, 'precio');
      if (r.wasNormalized) {
        correcciones.push(`Precio máximo: ${r.interpretacion}`);
        params.precio_maximo = r.value;
      }
    }

    let query = supabase
      .from('vehiculos_completos')
      .select('id, titulo, marca, modelo, autoano, precio, transmision, combustible, carroceria, ubicacion, kilometraje, garantia, enganchemin, mensualidad_minima, slug, liga_web');

    query = aplicarFiltrosBase(query);

    if (params.marca) query = query.ilike('marca', `%${params.marca}%`);
    if (params.modelo) query = query.ilike('modelo', `%${params.modelo}%`);
    if (params.año_minimo) query = query.gte('autoano', params.año_minimo);
    if (params.año_maximo) query = query.lte('autoano', params.año_maximo);
    if (params.precio_minimo) query = query.gte('precio', params.precio_minimo);
    if (params.precio_maximo) query = query.lte('precio', params.precio_maximo);
    if (params.tipo_carroceria) query = query.ilike('carroceria', `%${params.tipo_carroceria}%`);
    if (params.transmision) query = query.ilike('transmision', `%${params.transmision}%`);
    if (params.combustible) query = query.ilike('combustible', `%${params.combustible}%`);

    query = query
      .order('precio', { ascending: true })
      .limit(params.limite || 5);

    const { data, error } = await query;

    if (error) throw new Error(error.message);

    const vehiculos = (data || []).map((v: any) => ({
      id: v.id,
      titulo: v.titulo || `${v.marca} ${v.modelo} ${v.autoano}`,
      marca: v.marca,
      modelo: v.modelo,
      año: v.autoano,
      precio: formatPrice(v.precio),
      precio_numerico: v.precio,
      transmision: v.transmision,
      combustible: v.combustible,
      carroceria: v.carroceria,
      ubicacion: v.ubicacion,
      kilometraje: formatKm(v.kilometraje),
      garantia: v.garantia,
      enganche_minimo: v.enganchemin ? formatPrice(v.enganchemin) : null,
      mensualidad_desde: v.mensualidad_minima ? formatPrice(v.mensualidad_minima) : null,
      url: v.liga_web || (v.slug ? `https://autostrefa.mx/autos/${v.slug}` : null),
    }));

    // Si hay resultados, devolver normalmente
    if (vehiculos.length > 0) {
      return {
        vehiculos,
        total: vehiculos.length,
        ...(correcciones.length > 0 && { correcciones }),
      };
    }

    // Sin resultados — construir mensaje amigable según el caso
    if (marcaSinCoincidencia || modeloSinCoincidencia) {
      const marcas = await getMarcasDisponibles();
      const marcasTexto = marcas.slice(0, 10).join(', ');

      const partes: string[] = [];
      if (marcaSinCoincidencia && params.marca) {
        partes.push(`No encontramos la marca "${params.marca}" en nuestro inventario actual`);
      }
      if (modeloSinCoincidencia && params.modelo) {
        partes.push(`no contamos con el modelo "${params.modelo}" por el momento`);
      }

      return {
        vehiculos: [],
        total: 0,
        mensaje: `${partes.join(' y ')}. Actualmente contamos con: ${marcasTexto}, entre otras. ¿Te gustaría explorar alguna de estas opciones?`,
        ...(correcciones.length > 0 && { correcciones }),
      };
    }

    // Sin resultados por filtros (precio, año, etc.)
    return {
      vehiculos: [],
      total: 0,
      mensaje: 'Por el momento no encontramos vehículos con esos criterios. Te sugerimos ampliar tu búsqueda o ajustar los filtros. Con gusto te ayudamos a encontrar opciones.',
      ...(correcciones.length > 0 && { correcciones }),
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : '';
    console.error('[buscar_vehiculos]', msg);
    return {
      error: 'Tuvimos un inconveniente al buscar en nuestro inventario. Por favor intenta de nuevo en un momento.',
    };
  }
}
