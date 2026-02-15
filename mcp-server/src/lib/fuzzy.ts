import { getSupabaseClient } from './supabase.js';

// ============================================================================
// MAPA ESTÁTICO DE CORRECCIONES COMUNES — MARCAS
// ============================================================================

const MARCA_CORRECTIONS: Record<string, string> = {
  // Toyota
  toyoda: 'Toyota', toyot: 'Toyota', toyoya: 'Toyota', toiota: 'Toyota',
  toyota: 'Toyota', toyotta: 'Toyota',
  // Nissan
  nisan: 'Nissan', nisson: 'Nissan', nissa: 'Nissan', nisam: 'Nissan',
  niss: 'Nissan', nissan: 'Nissan', nisssn: 'Nissan',
  // Chevrolet
  chevi: 'Chevrolet', chevy: 'Chevrolet', chevroled: 'Chevrolet',
  shevrolet: 'Chevrolet', chevrolet: 'Chevrolet', chebrolet: 'Chevrolet',
  chevolet: 'Chevrolet', chevrol: 'Chevrolet', chebr: 'Chevrolet',
  // Volkswagen
  vw: 'Volkswagen', wv: 'Volkswagen', folkswagen: 'Volkswagen',
  volswagen: 'Volkswagen', volksvagen: 'Volkswagen', volkswagen: 'Volkswagen',
  wolkswagen: 'Volkswagen', folcks: 'Volkswagen', volks: 'Volkswagen',
  volksw: 'Volkswagen', bw: 'Volkswagen',
  // Hyundai
  hundai: 'Hyundai', hyunday: 'Hyundai', hundayi: 'Hyundai',
  hiunday: 'Hyundai', hyundai: 'Hyundai', hundaai: 'Hyundai',
  jundai: 'Hyundai', hiundai: 'Hyundai',
  // Honda
  hoda: 'Honda', honada: 'Honda', jonda: 'Honda', onda: 'Honda',
  // Mazda
  masda: 'Mazda', mazada: 'Mazda', mazd: 'Mazda', mazds: 'Mazda',
  // Mitsubishi
  mitsubichi: 'Mitsubishi', mitsubisi: 'Mitsubishi', mitzubishi: 'Mitsubishi',
  mitsub: 'Mitsubishi', mitsu: 'Mitsubishi', mitsubushi: 'Mitsubishi',
  // KIA
  kya: 'Kia', kia: 'Kia', quía: 'Kia',
  // Suzuki
  susuki: 'Suzuki', suzuky: 'Suzuki', zusuki: 'Suzuki', suzuki: 'Suzuki',
  // Mercedes-Benz
  mercedes: 'Mercedes-Benz', mercedez: 'Mercedes-Benz',
  'mercedes benz': 'Mercedes-Benz', merced: 'Mercedes-Benz',
  // BMW
  bmv: 'BMW', bwm: 'BMW', bmw: 'BMW', vemeve: 'BMW',
  // Renault
  renauld: 'Renault', renaul: 'Renault', reno: 'Renault', renau: 'Renault',
  // Peugeot
  peugeod: 'Peugeot', peugot: 'Peugeot', peyot: 'Peugeot', peugo: 'Peugeot',
  // Seat / Cupra
  ceat: 'Seat', seat: 'Seat', sear: 'Seat',
  cupra: 'Cupra', cupla: 'Cupra',
  // Audi
  audy: 'Audi', aody: 'Audi',
  // Jeep
  jip: 'Jeep', yip: 'Jeep', jiip: 'Jeep', yeep: 'Jeep',
  // Ford
  frod: 'Ford', fford: 'Ford',
  // Dodge / RAM
  doge: 'Dodge', dodg: 'Dodge', doch: 'Dodge',
  ran: 'RAM', ramm: 'RAM',
  // Fiat
  fiar: 'Fiat', fiad: 'Fiat',
  // Subaru
  subaro: 'Subaru', subari: 'Subaru',
  // Acura
  akura: 'Acura',
  // Lincoln
  lincon: 'Lincoln', lincol: 'Lincoln',
  // Buick
  buik: 'Buick', buic: 'Buick',
  // GMC
  gmc: 'GMC', gms: 'GMC',
  // MG
  mg: 'MG',
  // JAC
  jac: 'JAC', jak: 'JAC',
  // Changan
  changan: 'Changan', changam: 'Changan', changa: 'Changan',
  // BAIC
  baic: 'BAIC', baik: 'BAIC',
  // Chirey
  chirey: 'Chirey', chiry: 'Chirey', cherry: 'Chirey',
};

// ============================================================================
// MAPA ESTÁTICO DE CORRECCIONES COMUNES — MODELOS
// ============================================================================

const MODELO_CORRECTIONS: Record<string, string> = {
  // Nissan
  bersa: 'Versa', verza: 'Versa', verssa: 'Versa', besa: 'Versa',
  sentrra: 'Sentra', senttra: 'Sentra', centra: 'Sentra',
  xtrail: 'X-Trail', 'x trail': 'X-Trail', 'x-trail': 'X-Trail', extrail: 'X-Trail',
  kicks: 'Kicks', kics: 'Kicks',
  march: 'March', marc: 'March',
  frontier: 'Frontier', fronter: 'Frontier',
  pathfinder: 'Pathfinder', patfinder: 'Pathfinder',
  // Volkswagen
  yetta: 'Jetta', jeta: 'Jetta', yeta: 'Jetta', jett: 'Jetta',
  tagun: 'Taos', taoz: 'Taos',
  tiguan: 'Tiguan', 'tiguán': 'Tiguan', tiguam: 'Tiguan',
  polo: 'Polo', pol: 'Polo',
  virtus: 'Virtus', birtus: 'Virtus',
  tcross: 'T-Cross', 't-cross': 'T-Cross', 't cross': 'T-Cross',
  nivus: 'Nivus',
  // Toyota
  corrolla: 'Corolla', corola: 'Corolla', corlla: 'Corolla', coroya: 'Corolla',
  yaris: 'Yaris', yari: 'Yaris', yariz: 'Yaris',
  hilux: 'Hilux', hilx: 'Hilux', jilux: 'Hilux',
  rav4: 'RAV4', 'rav-4': 'RAV4', rav: 'RAV4',
  camry: 'Camry', camri: 'Camry',
  prius: 'Prius',
  // Honda
  sivic: 'Civic', 'cívic': 'Civic', civiv: 'Civic', sibic: 'Civic',
  'cr-v': 'CR-V', crv: 'CR-V', 'c-rv': 'CR-V',
  'hr-v': 'HR-V', hrv: 'HR-V', 'h-rv': 'HR-V',
  'br-v': 'BR-V', brv: 'BR-V',
  city: 'City', citi: 'City',
  fit: 'Fit',
  // Mazda
  'cx-5': 'CX-5', cx5: 'CX-5',
  'cx-3': 'CX-3', cx3: 'CX-3',
  'cx-30': 'CX-30', cx30: 'CX-30',
  'cx-50': 'CX-50', cx50: 'CX-50',
  'cx-90': 'CX-90', cx90: 'CX-90',
  mazda3: 'Mazda3', 'mazda 3': 'Mazda3',
  mazda6: 'Mazda6', 'mazda 6': 'Mazda6',
  mazda2: 'Mazda2', 'mazda 2': 'Mazda2',
  // Hyundai
  tuczon: 'Tucson', tucsan: 'Tucson', tucsom: 'Tucson', tucsón: 'Tucson',
  creta: 'Creta', cretta: 'Creta',
  venue: 'Venue', benue: 'Venue',
  accent: 'Accent', axent: 'Accent',
  palisade: 'Palisade',
  // Chevrolet
  aveo: 'Aveo', abeo: 'Aveo',
  cavalier: 'Cavalier', kavalier: 'Cavalier', cabalier: 'Cavalier',
  equinocs: 'Equinox', equinox: 'Equinox', ekinox: 'Equinox',
  tracs: 'Trax', trax: 'Trax', traks: 'Trax',
  tracker: 'Tracker', traker: 'Tracker',
  onix: 'Onix', onyx: 'Onix',
  captiva: 'Captiva', captiba: 'Captiva',
  traverse: 'Traverse', travers: 'Traverse',
  suburban: 'Suburban', suburbam: 'Suburban',
  tahoe: 'Tahoe', tajo: 'Tahoe',
  silverado: 'Silverado', silverdo: 'Silverado',
  // KIA
  rio: 'Rio', ryo: 'Rio',
  sportaje: 'Sportage', sportage: 'Sportage', esportage: 'Sportage',
  seltos: 'Seltos', seltoz: 'Seltos',
  forte: 'Forte', fortte: 'Forte',
  sorento: 'Sorento', sorent: 'Sorento',
  // Suzuki
  swift: 'Swift', suift: 'Swift',
  vitara: 'Vitara', bitara: 'Vitara',
  ertiga: 'Ertiga',
  jimny: 'Jimny', yimny: 'Jimny',
  ignis: 'Ignis',
  // Jeep
  compass: 'Compass', compas: 'Compass', conpas: 'Compass',
  renegade: 'Renegade', renegeid: 'Renegade',
  wrangler: 'Wrangler', rangler: 'Wrangler',
  cherokee: 'Cherokee', cheroki: 'Cherokee',
  'grand cherokee': 'Grand Cherokee',
  // Renault
  daster: 'Duster', duter: 'Duster', doster: 'Duster',
  kwid: 'Kwid', kuig: 'Kwid',
  stepway: 'Stepway', stepuey: 'Stepway',
  koleos: 'Koleos', coleos: 'Koleos',
  // Ford
  escape: 'Escape', escap: 'Escape',
  ranger: 'Ranger', ranjer: 'Ranger',
  bronco: 'Bronco', bronko: 'Bronco',
  maverick: 'Maverick', maberick: 'Maverick',
  explorer: 'Explorer', esplorer: 'Explorer',
  mustang: 'Mustang', mustag: 'Mustang',
  // Seat / Cupra
  ibiza: 'Ibiza', iviza: 'Ibiza',
  arona: 'Arona',
  ateca: 'Ateca', ateka: 'Ateca',
  leon: 'Leon', 'león': 'Leon',
  tarraco: 'Tarraco',
  formentor: 'Formentor',
  // Dodge / RAM
  attitude: 'Attitude', atitude: 'Attitude',
  durango: 'Durango', durago: 'Durango',
  // Peugeot
  '2008': '2008', '208': '208', '3008': '3008', '5008': '5008',
  // MG
  mg5: 'MG5', 'mg 5': 'MG5',
  zs: 'ZS',
  hs: 'HS',
  marvel: 'Marvel R',
};

// ============================================================================
// LEVENSHTEIN DISTANCE
// ============================================================================

function levenshtein(a: string, b: string): number {
  const la = a.length;
  const lb = b.length;
  const dp: number[][] = Array.from({ length: la + 1 }, () => Array(lb + 1).fill(0));

  for (let i = 0; i <= la; i++) dp[i][0] = i;
  for (let j = 0; j <= lb; j++) dp[0][j] = j;

  for (let i = 1; i <= la; i++) {
    for (let j = 1; j <= lb; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      dp[i][j] = Math.min(
        dp[i - 1][j] + 1,
        dp[i][j - 1] + 1,
        dp[i - 1][j - 1] + cost
      );
    }
  }

  return dp[la][lb];
}

// ============================================================================
// CACHE DE MARCAS/MODELOS DEL INVENTARIO
// ============================================================================

let cachedMarcas: string[] = [];
let cachedModelos: string[] = [];
let lastCacheRefresh = 0;
const CACHE_TTL = 10 * 60 * 1000; // 10 minutos

async function refreshCache(): Promise<void> {
  const now = Date.now();
  if (now - lastCacheRefresh < CACHE_TTL && cachedMarcas.length > 0) return;

  try {
    const supabase = getSupabaseClient();

    const [marcasRes, modelosRes] = await Promise.all([
      supabase.from('vehiculos_completos').select('marca').not('marca', 'is', null),
      supabase.from('vehiculos_completos').select('modelo').not('modelo', 'is', null),
    ]);

    if (marcasRes.data) {
      cachedMarcas = [...new Set(marcasRes.data.map((r: any) => r.marca as string))];
    }
    if (modelosRes.data) {
      cachedModelos = [...new Set(modelosRes.data.map((r: any) => r.modelo as string))];
    }

    lastCacheRefresh = now;
  } catch (err) {
    console.error('[fuzzy] Error refreshing cache:', err);
  }
}

// ============================================================================
// FUNCIONES PÚBLICAS
// ============================================================================

export interface NormalizeResult {
  original: string;
  corrected: string;
  wasCorrected: boolean;
  /** true cuando no se encontró coincidencia ni en mapa ni en inventario */
  sinCoincidencia: boolean;
}

/** Devuelve las marcas disponibles en el inventario (para sugerencias) */
export async function getMarcasDisponibles(): Promise<string[]> {
  await refreshCache();
  return cachedMarcas;
}

function findBestMatch(input: string, candidates: string[], maxDistance = 2): string | null {
  const lower = input.toLowerCase();
  let bestMatch: string | null = null;
  let bestDist = maxDistance + 1;

  for (const candidate of candidates) {
    const dist = levenshtein(lower, candidate.toLowerCase());
    if (dist < bestDist) {
      bestDist = dist;
      bestMatch = candidate;
    }
  }

  return bestDist <= maxDistance ? bestMatch : null;
}

export async function normalizeMarca(input: string): Promise<NormalizeResult> {
  const lower = input.toLowerCase().trim();

  // 1. Mapa estático
  if (MARCA_CORRECTIONS[lower]) {
    return { original: input, corrected: MARCA_CORRECTIONS[lower], wasCorrected: true, sinCoincidencia: false };
  }

  // 2. Match exacto contra inventario (case-insensitive)
  await refreshCache();
  const exactMatch = cachedMarcas.find(m => m.toLowerCase() === lower);
  if (exactMatch) {
    return { original: input, corrected: exactMatch, wasCorrected: false, sinCoincidencia: false };
  }

  // 3. Levenshtein contra inventario real
  const match = findBestMatch(input, cachedMarcas);
  if (match) {
    return { original: input, corrected: match, wasCorrected: true, sinCoincidencia: false };
  }

  // 4. Sin coincidencia — devolver original pero marcado
  return { original: input, corrected: input, wasCorrected: false, sinCoincidencia: true };
}

export async function normalizeModelo(input: string): Promise<NormalizeResult> {
  const lower = input.toLowerCase().trim();

  // 1. Mapa estático
  if (MODELO_CORRECTIONS[lower]) {
    return { original: input, corrected: MODELO_CORRECTIONS[lower], wasCorrected: true, sinCoincidencia: false };
  }

  // 2. Match exacto contra inventario (case-insensitive)
  await refreshCache();
  const exactMatch = cachedModelos.find(m => m.toLowerCase() === lower);
  if (exactMatch) {
    return { original: input, corrected: exactMatch, wasCorrected: false, sinCoincidencia: false };
  }

  // 3. Levenshtein contra inventario real
  const match = findBestMatch(input, cachedModelos);
  if (match) {
    return { original: input, corrected: match, wasCorrected: true, sinCoincidencia: false };
  }

  // 4. Sin coincidencia — devolver original pero marcado
  return { original: input, corrected: input, wasCorrected: false, sinCoincidencia: true };
}
