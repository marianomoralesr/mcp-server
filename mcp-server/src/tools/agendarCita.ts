import { z } from 'zod';
import { getSupabaseClient } from '../lib/supabase.js';

export const AgendarCitaSchema = z.object({
  nombre: z.string().describe('Nombre del cliente'),
  telefono: z.string().describe('Teléfono del cliente'),
  email: z.string().optional().describe('Email del cliente'),
  sucursal: z.enum(['Monterrey', 'Guadalupe', 'Saltillo', 'Reynosa']).describe(
    'Sucursal para la visita: Monterrey, Guadalupe, Saltillo o Reynosa'
  ),
  fecha: z.string().describe('Fecha propuesta para la visita (formato YYYY-MM-DD)'),
  hora: z.string().describe('Hora propuesta (formato HH:MM, horario de sucursal)'),
  vehiculo_interes: z.string().optional().describe('Vehículo de interés del cliente'),
  comentarios: z.string().optional().describe('Comentarios adicionales'),
});

const SUCURSAL_HORARIOS: Record<string, string> = {
  Monterrey: 'Lun-Sáb 9:00–19:00',
  Guadalupe: 'Lun-Sáb 9:00–19:00',
  Saltillo: 'Lun-Sáb 9:00–19:00',
  Reynosa: 'Lun-Sáb 9:00–19:00',
};

async function notificarTelegram(cita: {
  nombre: string;
  telefono: string;
  email?: string;
  sucursal: string;
  fecha: string;
  hora: string;
  vehiculo_interes?: string;
  comentarios?: string;
}): Promise<boolean> {
  const botToken = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;

  if (!botToken || !chatId) {
    console.error('[agendar_cita] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados');
    return false;
  }

  const vehiculoInfo = cita.vehiculo_interes ? `\n🚗 Vehículo: ${cita.vehiculo_interes}` : '';
  const emailInfo = cita.email ? `\n📧 Email: ${cita.email}` : '';
  const comentariosInfo = cita.comentarios ? `\n💬 Notas: ${cita.comentarios}` : '';

  const mensaje =
    `📅 *Nueva cita programada*\n\n` +
    `👤 Cliente: ${cita.nombre}\n` +
    `📱 Teléfono: ${cita.telefono}${emailInfo}\n` +
    `📍 Sucursal: ${cita.sucursal}\n` +
    `🗓 Fecha: ${cita.fecha}\n` +
    `🕐 Hora: ${cita.hora}${vehiculoInfo}${comentariosInfo}\n\n` +
    `_Agendada desde el chat de Mariana_`;

  try {
    const url = `https://api.telegram.org/bot${botToken}/sendMessage`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text: mensaje,
        parse_mode: 'Markdown',
      }),
    });

    if (!res.ok) {
      const body = await res.text();
      console.error('[agendar_cita] Telegram error:', res.status, body);
      return false;
    }

    return true;
  } catch (err) {
    console.error('[agendar_cita] Telegram fetch error:', err instanceof Error ? err.message : err);
    return false;
  }
}

export async function agendarCita(params: z.infer<typeof AgendarCitaSchema>) {
  try {
    const supabase = getSupabaseClient();

    // Validar fecha no sea pasada
    const fechaCita = new Date(`${params.fecha}T${params.hora}:00`);
    const ahora = new Date();
    if (fechaCita < ahora) {
      return {
        error: 'La fecha y hora de la cita ya pasaron. Por favor elige una fecha futura.',
        cita_creada: false,
      };
    }

    // Validar día (no domingos)
    if (fechaCita.getDay() === 0) {
      return {
        error: 'Las sucursales no abren los domingos. Por favor elige un día de lunes a sábado.',
        cita_creada: false,
      };
    }

    // Validar hora dentro de horario (9:00-19:00)
    const hora = parseInt(params.hora.split(':')[0], 10);
    if (hora < 9 || hora >= 19) {
      return {
        error: `El horario de la sucursal ${params.sucursal} es ${SUCURSAL_HORARIOS[params.sucursal]}. Por favor elige una hora dentro de ese rango.`,
        cita_creada: false,
      };
    }

    // Registrar en chat_leads con source indicando cita
    const { error: insertError } = await supabase.from('chat_leads').insert({
      nombre: params.nombre,
      telefono: params.telefono,
      email: params.email || null,
      vehiculo_interes: params.vehiculo_interes || null,
      comentarios: `[CITA] Sucursal: ${params.sucursal} | Fecha: ${params.fecha} | Hora: ${params.hora}${params.comentarios ? ` | ${params.comentarios}` : ''}`,
      source: 'cita_programada',
    });

    if (insertError) throw new Error(insertError.message);

    // Notificar por Telegram
    const telegramOk = await notificarTelegram(params);

    return {
      cita_creada: true,
      sucursal: params.sucursal,
      fecha: params.fecha,
      hora: params.hora,
      horario_sucursal: SUCURSAL_HORARIOS[params.sucursal],
      notificacion_telegram: telegramOk,
      mensaje: `¡Listo, ${params.nombre}! Tu visita a nuestra sucursal de ${params.sucursal} queda agendada para el ${params.fecha} a las ${params.hora}. Un asesor te estará esperando. Si necesitas cambiar la cita, avísame con tiempo.`,
    };
  } catch (err) {
    console.error('[agendar_cita]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos agendar la cita en este momento. Por favor intenta de nuevo o comunícate directamente con nosotros.',
      cita_creada: false,
    };
  }
}
