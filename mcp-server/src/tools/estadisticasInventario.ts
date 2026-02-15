import { getSupabaseClient, formatPrice, aplicarFiltrosBase } from '../lib/supabase.js';

export async function estadisticasInventario() {
  try {
    const supabase = getSupabaseClient();

    let query = supabase
      .from('vehiculos_completos')
      .select('marca, precio, autoano');

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

    const precios = vehiculos.map((v: any) => v.precio).filter(Boolean) as number[];
    const años = vehiculos.map((v: any) => v.autoano).filter(Boolean) as number[];

    const precioMin = Math.min(...precios);
    const precioMax = Math.max(...precios);
    const precioPromedio = precios.reduce((a, b) => a + b, 0) / precios.length;

    const añoMin = Math.min(...años);
    const añoMax = Math.max(...años);

    const marcas: Record<string, number> = {};
    vehiculos.forEach((v: any) => {
      if (v.marca) marcas[v.marca] = (marcas[v.marca] || 0) + 1;
    });

    const marcasDisponibles = Object.entries(marcas)
      .sort((a, b) => b[1] - a[1])
      .map(([marca, cantidad]) => ({ marca, cantidad }));

    return {
      total_vehiculos: total,
      rango_precios: {
        minimo: formatPrice(precioMin),
        maximo: formatPrice(precioMax),
        promedio: formatPrice(precioPromedio),
      },
      rango_anos: { desde: añoMin, hasta: añoMax },
      marcas_disponibles: marcasDisponibles,
    };
  } catch (err) {
    console.error('[estadisticas_inventario]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos obtener las estadísticas del inventario en este momento. Por favor intenta de nuevo.',
    };
  }
}
