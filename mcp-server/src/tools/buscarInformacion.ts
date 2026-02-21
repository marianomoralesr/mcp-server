import { z } from 'zod';
import { getSupabaseClient } from '../lib/supabase.js';

export const BuscarInformacionSchema = z.object({
  pregunta: z.string().describe('Pregunta o tema a buscar'),
  categoria: z.enum(['faq', 'politicas', 'compras', 'financiamiento', 'garantias', 'general']).optional()
    .describe('Categoría: faq, politicas, compras, financiamiento, garantias, general'),
});

const CATEGORY_MAP: Record<string, string> = {
  'politicas': 'politica',
  'compras': 'compras',
  'garantias': 'garantia',
  'faq': 'faq',
  'financiamiento': 'financiamiento',
  'general': 'general',
};

export async function buscarInformacion(params: z.infer<typeof BuscarInformacionSchema>) {
  try {
    const supabase = getSupabaseClient();

    // Extraer palabras clave de la pregunta (> 2 caracteres)
    const palabras = params.pregunta
      .toLowerCase()
      .split(/\s+/)
      .filter(p => p.length > 2);

    let query = supabase
      .from('business_knowledge')
      .select('id, category, title, content, keywords, priority')
      .eq('is_active', true);

    // Filtrar por categoría si se proporcionó
    if (params.categoria) {
      const mapped = CATEGORY_MAP[params.categoria] || params.categoria;
      query = query.eq('category', mapped);
    }

    // Buscar por palabras clave en title y content
    if (palabras.length > 0) {
      const orConditions = palabras
        .map(p => `title.ilike.%${p}%,content.ilike.%${p}%`)
        .join(',');
      query = query.or(orConditions);
    }

    query = query
      .order('priority', { ascending: false })
      .limit(5);

    const { data, error } = await query;

    if (error) throw new Error(error.message);

    const resultados = (data || []).map((r: any) => ({
      id: r.id,
      categoria: r.category,
      titulo: r.title,
      contenido: r.content,
    }));

    if (resultados.length === 0) {
      return {
        resultados: [],
        mensaje: 'No encontramos información específica sobre ese tema en nuestra base de conocimiento. Si necesitas más detalles, con gusto un asesor de Autos TREFA puede ayudarte.',
      };
    }

    return { resultados };
  } catch (err) {
    console.error('[buscar_informacion]', err instanceof Error ? err.message : '');
    return {
      error: 'No pudimos consultar nuestra base de conocimiento en este momento. Por favor intenta de nuevo.',
    };
  }
}
