import { z } from 'zod';
import { formatPrice } from '../lib/supabase.js';
import { normalizeAmount } from '../lib/normalize-amount.js';

export const CalcularFinanciamientoSchema = z.object({
  precio_vehiculo: z.number().describe('Precio del vehículo en MXN'),
  enganche_porcentaje: z.number().optional().default(20).describe('Porcentaje de enganche (default: 20)'),
  plazo_meses: z.number().optional().default(48).describe('Plazo en meses (default: 48)'),
  tasa_anual: z.number().optional().default(15).describe('Tasa de interés anual en % (default: 15)'),
});

export async function calcularFinanciamiento(params: z.infer<typeof CalcularFinanciamientoSchema>) {
  try {
    const correcciones: string[] = [];

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

    return {
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
      ...(correcciones.length > 0 && { correcciones }),
    };
  } catch (err) {
    console.error('[calcular_financiamiento]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos realizar el cálculo de financiamiento en este momento. Por favor verifica los datos e intenta de nuevo.',
    };
  }
}
