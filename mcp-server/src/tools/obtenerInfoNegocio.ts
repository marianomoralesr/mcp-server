import { z } from 'zod';
import { getSupabaseClient } from '../lib/supabase.js';

export const ObtenerInfoNegocioSchema = z.object({
  tema: z.enum([
    'horarios', 'ubicaciones', 'contacto', 'garantias', 'financiamiento',
    'documentos_requeridos', 'proceso_compra', 'devoluciones', 'intercambio', 'servicios',
  ]).describe('Tema de información del negocio'),
});

const TEMA_MAP: Record<string, { category: string; titlePattern: string }> = {
  'horarios':              { category: 'general',         titlePattern: '%Horario%' },
  'ubicaciones':           { category: 'general',         titlePattern: '%Sobre%' },
  'contacto':              { category: 'general',         titlePattern: '%Contacto%' },
  'garantias':             { category: 'garantia',        titlePattern: '%' },
  'financiamiento':        { category: 'financiamiento',  titlePattern: '%' },
  'documentos_requeridos': { category: 'proceso',         titlePattern: '%Documento%' },
  'proceso_compra':        { category: 'proceso',         titlePattern: '%Compra%' },
  'devoluciones':          { category: 'politica',        titlePattern: '%Devolucion%' },
  'intercambio':           { category: 'servicio',        titlePattern: '%Intercambio%' },
  'servicios':             { category: 'servicio',        titlePattern: '%' },
};

const TEMA_LABELS: Record<string, string> = {
  'horarios': 'horarios', 'ubicaciones': 'ubicaciones', 'contacto': 'contacto',
  'garantias': 'garantías', 'financiamiento': 'financiamiento',
  'documentos_requeridos': 'documentos requeridos', 'proceso_compra': 'proceso de compra',
  'devoluciones': 'devoluciones', 'intercambio': 'intercambio de autos', 'servicios': 'servicios',
};

export async function obtenerInfoNegocio(params: z.infer<typeof ObtenerInfoNegocioSchema>) {
  try {
    const supabase = getSupabaseClient();
    const mapping = TEMA_MAP[params.tema];

    if (!mapping) {
      return {
        informacion: [],
        mensaje: 'No reconocimos ese tema. Puedes consultar sobre: horarios, ubicaciones, contacto, garantías, financiamiento, documentos requeridos, proceso de compra, devoluciones, intercambio o servicios.',
      };
    }

    const { data, error } = await supabase
      .from('business_knowledge')
      .select('id, category, title, content, priority')
      .eq('is_active', true)
      .eq('category', mapping.category)
      .ilike('title', mapping.titlePattern)
      .order('priority', { ascending: false });

    if (error) throw new Error(error.message);

    const informacion = (data || []).map((r: any) => ({
      id: r.id,
      titulo: r.title,
      contenido: r.content,
    }));

    if (informacion.length === 0) {
      const label = TEMA_LABELS[params.tema] || params.tema;
      return {
        informacion: [],
        mensaje: `Estamos actualizando la información sobre ${label}. Para obtener estos detalles, te invitamos a contactar directamente a un asesor de Autos TREFA.`,
      };
    }

    return { informacion };
  } catch (err) {
    console.error('[obtener_info_negocio]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos obtener la información solicitada en este momento. Por favor intenta de nuevo.',
    };
  }
}
