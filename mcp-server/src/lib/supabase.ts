import { createClient, SupabaseClient } from '@supabase/supabase-js';

let client: SupabaseClient | null = null;

export function getSupabaseClient(): SupabaseClient {
  if (!client) {
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;

    if (!url || !key) {
      throw new Error('SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY son requeridos');
    }

    client = createClient(url, key);
  }

  return client;
}

export function formatPrice(price: number): string {
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency: 'MXN',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(price);
}

export function formatKm(km: number | null): string {
  if (km === null || km === undefined) return 'No disponible';
  return new Intl.NumberFormat('es-MX').format(km) + ' km';
}

/** Filtros base que siempre aplican a vehiculos_completos */
export function aplicarFiltrosBase(query: any) {
  return query
    .eq('exhibicion_inventario', true)
    .eq('separado', false)
    .not('precio', 'is', null);
}
