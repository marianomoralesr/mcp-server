import { z } from 'zod';
import { getSupabaseClient, formatPrice } from '../lib/supabase.js';

export const EnviarCotizacionEmailSchema = z.object({
  email_destino: z.string().describe('Email del cliente'),
  nombre_cliente: z.string().describe('Nombre del cliente'),
  id: z.number().describe('ID del vehículo a cotizar'),
  enganche_porcentaje: z.number().optional().default(20).describe('Porcentaje de enganche (default: 20)'),
  plazo_meses: z.number().optional().default(48).describe('Plazo en meses (default: 48)'),
});

export async function enviarCotizacionEmail(params: z.infer<typeof EnviarCotizacionEmailSchema>) {
  try {
    const supabase = getSupabaseClient();

    // Obtener datos del vehículo
    const { data: vehiculo, error: vError } = await supabase
      .from('vehiculos_completos')
      .select('*')
      .eq('id', params.id)
      .single();

    if (vError) {
      if (vError.code === 'PGRST116') {
        return {
          mensaje: 'No encontramos el vehículo para la cotización. Es posible que ya no esté disponible. ¿Te gustaría cotizar otro vehículo?',
          enviado: false,
        };
      }
      throw new Error(vError.message);
    }

    // Calcular financiamiento con tasa fija 15%
    const precio = vehiculo.precio;
    const enganchePct = params.enganche_porcentaje ?? 20;
    const plazo = params.plazo_meses ?? 48;
    const tasaAnual = 15;

    const enganche = precio * (enganchePct / 100);
    const montoFinanciar = precio - enganche;
    const tasaMensual = tasaAnual / 100 / 12;
    const factor = Math.pow(1 + tasaMensual, plazo);
    const mensualidad = montoFinanciar * (tasaMensual * factor) / (factor - 1);

    const tituloVehiculo = vehiculo.titulo || `${vehiculo.marca} ${vehiculo.modelo} ${vehiculo.autoano}`;
    const urlVehiculo = vehiculo.liga_web || (vehiculo.slug ? `https://autostrefa.mx/autos/${vehiculo.slug}` : '');

    // Invocar edge function de envío de email
    const { error: emailError } = await supabase.functions.invoke('send-notification-email', {
      body: {
        to: params.email_destino,
        subject: `Cotización: ${tituloVehiculo} — Autos TREFA`,
        type: 'cotizacion',
        data: {
          nombre_cliente: params.nombre_cliente,
          vehiculo: {
            titulo: tituloVehiculo,
            marca: vehiculo.marca,
            modelo: vehiculo.modelo,
            año: vehiculo.autoano,
            precio: formatPrice(precio),
            url: urlVehiculo,
          },
          financiamiento: {
            enganche_porcentaje: enganchePct,
            enganche: formatPrice(enganche),
            monto_a_financiar: formatPrice(montoFinanciar),
            tasa_anual: `${tasaAnual}%`,
            plazo_meses: plazo,
            mensualidad_estimada: formatPrice(Math.round(mensualidad)),
          },
        },
      },
    });

    if (emailError) {
      console.error('[enviar_cotizacion_email] Edge function error:', emailError.message);
      return {
        mensaje: `No pudimos enviar la cotización a ${params.email_destino} en este momento. Por favor verifica que el correo sea correcto e intenta de nuevo.`,
        enviado: false,
      };
    }

    return {
      mensaje: `¡Cotización enviada! Revisa tu correo en ${params.email_destino} para ver los detalles del ${tituloVehiculo}. Si no lo encuentras, revisa tu carpeta de spam.`,
      enviado: true,
    };
  } catch (err) {
    console.error('[enviar_cotizacion_email]', err instanceof Error ? err.message : '');
    return {
      error: 'Tuvimos un inconveniente al enviar la cotización. Por favor intenta de nuevo en un momento.',
    };
  }
}
