import { z } from 'zod';
import { getSupabaseClient, formatPrice, formatKm } from '../lib/supabase.js';

export const CompararVehiculosSchema = z.object({
  vehiculo_ids: z.array(z.number()).min(2).max(4).describe('IDs de los vehículos a comparar (2-4)'),
});

export async function compararVehiculos(params: z.infer<typeof CompararVehiculosSchema>) {
  try {
    const supabase = getSupabaseClient();

    const { data, error } = await supabase
      .from('vehiculos_completos')
      .select('id, titulo, marca, modelo, autoano, precio, transmision, combustible, carroceria, motor, cilindros, ubicacion, kilometraje, garantia, enganchemin, mensualidad_minima, slug, liga_web')
      .in('id', params.vehiculo_ids);

    if (error) throw new Error(error.message);

    if (!data || data.length < 2) {
      return {
        mensaje: 'No pudimos encontrar suficientes vehículos para la comparación. Es posible que alguno ya no esté disponible. ¿Te gustaría seleccionar otros vehículos?',
      };
    }

    const vehiculos = data.map((v: any) => ({
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
      motor: v.motor,
      cilindros: v.cilindros,
      ubicacion: v.ubicacion,
      kilometraje: formatKm(v.kilometraje),
      kilometraje_numerico: v.kilometraje,
      garantia: v.garantia,
      enganche_minimo: v.enganchemin ? formatPrice(v.enganchemin) : null,
      mensualidad_desde: v.mensualidad_minima ? formatPrice(v.mensualidad_minima) : null,
      url: v.liga_web || (v.slug ? `https://autostrefa.mx/autos/${v.slug}` : null),
    }));

    return { vehiculos };
  } catch (err) {
    console.error('[comparar_vehiculos]', err instanceof Error ? err.message : '');
    return {
      error: 'Tuvimos un inconveniente al preparar la comparación. Por favor intenta de nuevo en un momento.',
    };
  }
}
