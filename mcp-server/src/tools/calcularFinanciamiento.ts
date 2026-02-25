import { z } from 'zod';
import { getSupabaseClient, formatPrice } from '../lib/supabase.js';
import { normalizeAmount } from '../lib/normalize-amount.js';

export const CalcularFinanciamientoSchema = z.object({
  precio_vehiculo: z.number().optional().describe('Precio del vehículo en MXN (opcional si se pasa vehiculo_id)'),
  vehiculo_id: z.number().optional().describe('ID del vehículo para usar datos reales de financiamiento de la base de datos'),
  enganche_porcentaje: z.number().optional().default(20).describe('Porcentaje de enganche (default: 20)'),
  plazo_meses: z.number().optional().default(48).describe('Plazo en meses (default: 48)'),
  tasa_anual: z.number().optional().default(15).describe('Tasa de interés anual en % (default: 15)'),
});

export async function calcularFinanciamiento(params: z.infer<typeof CalcularFinanciamientoSchema>) {
  try {
    const correcciones: string[] = [];
    let datosVehiculo: any = null;

    // Si se pasa vehiculo_id, obtener datos reales de la DB
    if (params.vehiculo_id) {
      const supabase = getSupabaseClient();
      const { data, error } = await supabase
        .from('vehiculos_completos')
        .select('id, titulo, marca, modelo, autoano, precio, enganchemin, enganche_recomendado, mensualidad_minima, mensualidad_recomendada, plazomax')
        .eq('id', params.vehiculo_id)
        .single();

      if (!error && data) {
        datosVehiculo = data;
        if (!params.precio_vehiculo) {
          params.precio_vehiculo = Number(data.precio);
        }
      }
    }

    if (!params.precio_vehiculo) {
      return {
        error: 'Se requiere precio_vehiculo o vehiculo_id para calcular financiamiento.',
      };
    }

    // Normalizar precio
    const rp = normalizeAmount(params.precio_vehiculo, 'precio');
    if (rp.wasNormalized) {
      correcciones.push(`Precio: ${rp.interpretacion}`);
      params.precio_vehiculo = rp.value;
    }

    const precio = params.precio_vehiculo;
    const enganchePct = params.enganche_porcentaje ?? 20;
    const plazo = params.plazo_meses ?? 48;
    const tasaAnual = params.tasa_anual ?? 15;

    const enganche = precio * (enganchePct / 100);
    const montoFinanciar = precio - enganche;
    const tasaMensual = tasaAnual / 100 / 12;

    let mensualidad: number;
    if (tasaMensual === 0) {
      mensualidad = montoFinanciar / plazo;
    } else {
      const factor = Math.pow(1 + tasaMensual, plazo);
      mensualidad = montoFinanciar * (tasaMensual * factor) / (factor - 1);
    }

    const totalPagar = mensualidad * plazo + enganche;
    const costoFinanciamiento = totalPagar - precio;

    const resultado: any = {
      precio_vehiculo: formatPrice(precio),
      enganche_porcentaje: enganchePct,
      enganche: formatPrice(enganche),
      monto_a_financiar: formatPrice(montoFinanciar),
      tasa_anual: `${tasaAnual}%`,
      plazo_meses: plazo,
      mensualidad_estimada: formatPrice(Math.round(mensualidad)),
      total_a_pagar: formatPrice(Math.round(totalPagar)),
      costo_financiamiento: formatPrice(Math.round(costoFinanciamiento)),
      nota: 'Estos valores son estimados. La tasa real depende del perfil crediticio del cliente y puede variar.',
    };

    // Si hay datos reales del vehículo, incluirlos como referencia
    if (datosVehiculo) {
      resultado.vehiculo = `${datosVehiculo.marca} ${datosVehiculo.modelo} ${datosVehiculo.autoano}`;
      resultado.datos_reales = {
        enganche_minimo: datosVehiculo.enganchemin ? formatPrice(datosVehiculo.enganchemin) : null,
        enganche_recomendado: datosVehiculo.enganche_recomendado ? formatPrice(datosVehiculo.enganche_recomendado) : null,
        mensualidad_minima: datosVehiculo.mensualidad_minima ? formatPrice(datosVehiculo.mensualidad_minima) : null,
        mensualidad_recomendada: datosVehiculo.mensualidad_recomendada ? formatPrice(datosVehiculo.mensualidad_recomendada) : null,
        plazo_maximo: datosVehiculo.plazomax ? `${datosVehiculo.plazomax} meses` : null,
      };
    }

    if (correcciones.length > 0) resultado.correcciones = correcciones;

    return resultado;
  } catch (err) {
    console.error('[calcular_financiamiento]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos realizar el cálculo de financiamiento en este momento. Por favor verifica los datos e intenta de nuevo.',
    };
  }
}
