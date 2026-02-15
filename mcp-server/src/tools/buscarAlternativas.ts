import { z } from 'zod';
import { getSupabaseClient, formatPrice, formatKm, aplicarFiltrosBase } from '../lib/supabase.js';
import { normalizeMarca, getMarcasDisponibles } from '../lib/fuzzy.js';
import { normalizeAmount } from '../lib/normalize-amount.js';

export const BuscarAlternativasSchema = z.object({
  marca_original: z.string().describe('Marca que el cliente buscaba originalmente'),
  presupuesto: z.number().describe('Presupuesto máximo del cliente en MXN'),
  modelo_original: z.string().optional().describe('Modelo que buscaba'),
  tipo_uso: z.string().optional().describe('Para qué usará el auto: familia, trabajo, ciudad, carretera'),
});

export async function buscarAlternativas(params: z.infer<typeof BuscarAlternativasSchema>) {
  try {
    const supabase = getSupabaseClient();
    const correcciones: string[] = [];

    // Normalizar marca
    const rm = await normalizeMarca(params.marca_original);
    params.marca_original = rm.corrected;
    if (rm.wasCorrected) correcciones.push(`Marca: "${rm.original}" → "${rm.corrected}"`);

    // Normalizar presupuesto
    const rp = normalizeAmount(params.presupuesto, 'precio');
    if (rp.wasNormalized) {
      correcciones.push(`Presupuesto: ${rp.interpretacion}`);
      params.presupuesto = rp.value;
    }

    const precioMin = params.presupuesto * 0.8;
    const precioMax = params.presupuesto * 1.2;

    let query = supabase
      .from('vehiculos_completos')
      .select('id, titulo, marca, modelo, autoano, precio, transmision, combustible, carroceria, ubicacion, kilometraje, garantia, enganchemin, mensualidad_minima, slug, liga_web');

    query = aplicarFiltrosBase(query);
    query = query
      .not('marca', 'ilike', `%${params.marca_original}%`)
      .gte('precio', precioMin)
      .lte('precio', precioMax)
      .order('precio', { ascending: true })
      .limit(5);

    const { data, error } = await query;

    if (error) throw new Error(error.message);

    const alternativas = (data || []).map((v: any) => ({
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

    if (alternativas.length > 0) {
      return {
        alternativas,
        total: alternativas.length,
        ...(correcciones.length > 0 && { correcciones }),
      };
    }

    // Sin alternativas — mensaje amigable
    const marcas = await getMarcasDisponibles();
    const marcasTexto = marcas.slice(0, 8).join(', ');

    return {
      alternativas: [],
      total: 0,
      mensaje: `Por el momento no encontramos alternativas en ese rango de presupuesto (${formatPrice(precioMin)} – ${formatPrice(precioMax)}). Contamos con marcas como ${marcasTexto}. ¿Te gustaría que busquemos con un rango de precio diferente?`,
      ...(correcciones.length > 0 && { correcciones }),
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : '';
    console.error('[buscar_alternativas]', msg);
    return {
      error: 'Tuvimos un inconveniente al buscar alternativas. Por favor intenta de nuevo en un momento.',
    };
  }
}
