import { getSupabaseClient, formatPrice, formatKm, aplicarFiltrosBase } from '../lib/supabase.js';

export async function estadisticasInventario() {
  try {
    const supabase = getSupabaseClient();

    let query = supabase
      .from('vehiculos_completos')
      .select('marca, precio, autoano, carroceria, transmision, combustible, ubicacion, kilometraje, garantia');

    query = aplicarFiltrosBase(query);

    const { data, error } = await query;

    if (error) throw new Error(error.message);

    const vehiculos = data || [];
    const total = vehiculos.length;

    if (total === 0) {
      return {
        total_vehiculos: 0,
        mensaje: 'Nuestro inventario se está actualizando en este momento. Por favor intenta de nuevo en unos minutos.',
      };
    }

    const precios = vehiculos.map((v: any) => Number(v.precio)).filter(Boolean);
    const años = vehiculos.map((v: any) => v.autoano).filter(Boolean) as number[];
    const kms = vehiculos.map((v: any) => Number(v.kilometraje)).filter(Boolean);

    const precioMin = Math.min(...precios);
    const precioMax = Math.max(...precios);
    const precioPromedio = precios.reduce((a, b) => a + b, 0) / precios.length;

    const añoMin = Math.min(...años);
    const añoMax = Math.max(...años);

    const kmMin = kms.length > 0 ? Math.min(...kms) : 0;
    const kmMax = kms.length > 0 ? Math.max(...kms) : 0;
    const kmPromedio = kms.length > 0 ? kms.reduce((a, b) => a + b, 0) / kms.length : 0;

    // Agrupar por diferentes dimensiones
    const agrupar = (campo: string): { nombre: string; cantidad: number }[] => {
      const conteo: Record<string, number> = {};
      vehiculos.forEach((v: any) => {
        const val = v[campo];
        if (val && val.trim()) conteo[val] = (conteo[val] || 0) + 1;
      });
      return Object.entries(conteo)
        .sort((a, b) => b[1] - a[1])
        .map(([nombre, cantidad]) => ({ nombre, cantidad }));
    };

    return {
      total_vehiculos: total,
      rango_precios: {
        minimo: formatPrice(precioMin),
        maximo: formatPrice(precioMax),
        promedio: formatPrice(precioPromedio),
      },
      rango_anos: { desde: añoMin, hasta: añoMax },
      rango_kilometraje: {
        minimo: formatKm(kmMin),
        maximo: formatKm(kmMax),
        promedio: formatKm(Math.round(kmPromedio)),
      },
      marcas_disponibles: agrupar('marca'),
      carrocerias_disponibles: agrupar('carroceria'),
      ubicaciones: agrupar('ubicacion'),
      transmisiones: agrupar('transmision'),
      combustibles: agrupar('combustible'),
      garantias: agrupar('garantia'),
    };
  } catch (err) {
    console.error('[estadisticas_inventario]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos obtener las estadísticas del inventario en este momento. Por favor intenta de nuevo.',
    };
  }
}
