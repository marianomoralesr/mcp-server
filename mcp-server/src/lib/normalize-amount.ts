// ============================================================================
// NORMALIZACIÓN DE MONTOS ABREVIADOS
// ============================================================================

export interface NormalizeAmountResult {
  value: number;
  wasNormalized: boolean;
  interpretacion: string;
  esPorcentaje?: boolean;
}

/**
 * Normaliza montos abreviados que los usuarios escriben en el chat.
 *
 * Reglas para `precio`:
 *   - Si value < 1,000 → value * 1,000 (ej: 280 → 280,000)
 *
 * Reglas para `enganche`:
 *   - Si value ≤ 100 → tratar como porcentaje
 *   - Si value < 1,000 → value * 1,000
 */
export function normalizeAmount(
  value: number,
  context: 'precio' | 'enganche'
): NormalizeAmountResult {
  if (context === 'enganche') {
    if (value <= 100) {
      return {
        value,
        wasNormalized: false,
        interpretacion: `${value}% de enganche`,
        esPorcentaje: true,
      };
    }
    if (value < 1_000) {
      const normalized = value * 1_000;
      return {
        value: normalized,
        wasNormalized: true,
        interpretacion: `Interpretamos ${value} como $${normalized.toLocaleString('es-MX')} MXN de enganche`,
      };
    }
    return {
      value,
      wasNormalized: false,
      interpretacion: `$${value.toLocaleString('es-MX')} MXN de enganche`,
    };
  }

  // context === 'precio'
  if (value < 1_000) {
    const normalized = value * 1_000;
    return {
      value: normalized,
      wasNormalized: true,
      interpretacion: `Interpretamos ${value} como $${normalized.toLocaleString('es-MX')} MXN`,
    };
  }

  return {
    value,
    wasNormalized: false,
    interpretacion: `$${value.toLocaleString('es-MX')} MXN`,
  };
}
