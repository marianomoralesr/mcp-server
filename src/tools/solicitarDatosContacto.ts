import { z } from 'zod';
import { getSupabaseClient } from '../lib/supabase.js';

export const SolicitarDatosContactoSchema = z.object({
  nombre: z.string().describe('Nombre del cliente'),
  telefono: z.string().describe('Teléfono del cliente'),
  email: z.string().optional().describe('Email del cliente'),
  vehiculo_interes: z.string().optional().describe('Vehículo de interés'),
  comentarios: z.string().optional().describe('Comentarios adicionales'),
});

export async function solicitarDatosContacto(params: z.infer<typeof SolicitarDatosContactoSchema>) {
  try {
    const supabase = getSupabaseClient();

    // Verificar si existe en profiles
    const { data, error } = await supabase
      .from('profiles')
      .select('id, first_name, last_name, email, phone')
      .eq('phone', params.telefono)
      .limit(1);

    if (error) throw new Error(error.message);

    if (data && data.length > 0) {
      const perfil = data[0];
      return {
        mensaje: `${perfil.first_name || params.nombre}, ya te tenemos registrado. Un asesor se pondrá en contacto contigo pronto. ¡Gracias por tu interés en Autos TREFA!`,
        lead_existente: true,
      };
    }

    // Verificar si ya existe en chat_leads (evitar duplicados recientes)
    const { data: existingLead } = await supabase
      .from('chat_leads')
      .select('id, nombre, created_at')
      .eq('telefono', params.telefono)
      .eq('atendido', false)
      .order('created_at', { ascending: false })
      .limit(1);

    if (existingLead && existingLead.length > 0) {
      return {
        mensaje: `${params.nombre}, ya tenemos tus datos registrados. Un asesor de Autos TREFA se comunicará contigo al ${params.telefono} lo antes posible. ¡Gracias por tu paciencia!`,
        lead_existente: true,
      };
    }

    // Insertar en chat_leads
    const { error: insertError } = await supabase
      .from('chat_leads')
      .insert({
        nombre: params.nombre,
        telefono: params.telefono,
        email: params.email || null,
        vehiculo_interes: params.vehiculo_interes || null,
        comentarios: params.comentarios || null,
        source: 'chat_widget',
      });

    if (insertError) throw new Error(insertError.message);

    return {
      mensaje: `¡Listo, ${params.nombre}! Hemos registrado tus datos. Un asesor de Autos TREFA se comunicará contigo al ${params.telefono} a la brevedad. ¡Gracias por contactarnos!`,
      datos_registrados: true,
    };
  } catch (err) {
    console.error('[solicitar_datos_contacto]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos registrar tus datos en este momento. Por favor intenta de nuevo o comunícate directamente con nosotros.',
    };
  }
}
