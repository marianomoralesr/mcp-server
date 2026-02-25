import { z } from 'zod';
import { getSupabaseClient } from '../lib/supabase.js';

export const ObtenerFaqsSchema = z.object({
  categoria: z.string().optional().describe('Categoría para filtrar FAQs'),
});

export async function obtenerFaqs(params: z.infer<typeof ObtenerFaqsSchema>) {
  try {
    const supabase = getSupabaseClient();

    let query = supabase
      .from('business_knowledge')
      .select('id, title, content, priority')
      .eq('is_active', true)
      .eq('category', 'faq');

    if (params.categoria) {
      query = query.ilike('content', `%${params.categoria}%`);
    }

    query = query
      .order('priority', { ascending: false })
      .limit(10);

    const { data, error } = await query;

    if (error) throw new Error(error.message);

    const faqs = (data || []).map((r: any) => ({
      id: r.id,
      pregunta: r.title,
      respuesta: r.content,
    }));

    if (faqs.length === 0) {
      return {
        faqs: [],
        mensaje: 'No encontramos preguntas frecuentes sobre ese tema. Si tienes alguna duda específica, con gusto un asesor de Autos TREFA puede ayudarte.',
      };
    }

    return { faqs };
  } catch (err) {
    console.error('[obtener_faqs]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos consultar las preguntas frecuentes en este momento. Por favor intenta de nuevo.',
    };
  }
}
