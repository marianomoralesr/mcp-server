import { z } from 'zod';
import { getSupabaseClient, formatPrice, formatKm } from '../lib/supabase.js';

export const ObtenerVehiculoSchema = z.object({
  id: z.number().optional().describe('ID numérico del vehículo'),
  slug: z.string().optional().describe('Slug URL del vehículo'),
}).refine(d => d.id !== undefined || d.slug !== undefined, {
  message: 'Se requiere al menos id o slug',
});

export async function obtenerVehiculo(params: z.infer<typeof ObtenerVehiculoSchema>) {
  try {
    const supabase = getSupabaseClient();

    let query = supabase
      .from('vehiculos_completos')
      .select('*');

    if (params.id !== undefined) {
      query = query.eq('id', params.id);
    } else if (params.slug) {
      query = query.eq('slug', params.slug);
    }

    const { data, error } = await query.single();

    if (error) {
      if (error.code === 'PGRST116') {
        return {
          mensaje: 'No encontramos ese vehículo en nuestro inventario. Es posible que ya se haya vendido o que el identificador no sea correcto. ¿Te gustaría que busquemos opciones similares?',
        };
      }
      throw new Error(error.message);
    }

    const v = data as any;

    return {
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
      garantia: v.garantia,
      descripcion: v.descripcion,
      enganche_minimo: v.enganchemin ? formatPrice(v.enganchemin) : null,
      enganche_recomendado: v.enganche_recomendado ? formatPrice(v.enganche_recomendado) : null,
      mensualidad_desde: v.mensualidad_minima ? formatPrice(v.mensualidad_minima) : null,
      mensualidad_recomendada: v.mensualidad_recomendada ? formatPrice(v.mensualidad_recomendada) : null,
      plazo_maximo: v.plazomax,
      con_oferta: v.con_oferta,
      oferta: v.oferta ? formatPrice(v.oferta) : null,
      promociones: v.promociones,
      imagen_principal: v.feature_image_url,
      galeria_exterior: v.galeria_exterior,
      galeria_interior: v.galeria_interior,
      url: v.liga_web || (v.slug ? `https://autostrefa.mx/autos/${v.slug}` : null),
    };
  } catch (err) {
    console.error('[obtener_vehiculo]', err instanceof Error ? err.message : '');
    return {
      error: 'Tuvimos un inconveniente al consultar los detalles de este vehículo. Por favor intenta de nuevo en un momento.',
    };
  }
}
