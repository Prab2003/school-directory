// MIT: Skolo Saka NPC. Portable Request/Response handler; no database credentials here.
const LEVELS = new Set(['primary', 'high', 'combined', 'other']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function publicSchool(row) {
  if (!row || !UUID.test(row.id) || typeof row.name !== 'string' || !row.name.trim()
    || row.name.length > 300 || !LEVELS.has(row.level) || typeof row.province !== 'string'
    || typeof row.verified !== 'boolean'
    || ![row.town, row.municipality].every(v => v === null || typeof v === 'string')) {
    throw new Error('Invalid school response');
  }
  return { id: row.id, name: row.name, level: row.level, province: row.province,
    town: row.town, municipality: row.municipality, verified: row.verified };
}

export function createSchoolSearchHandler({ search, origins = ['https://skolo-saka.github.io'],
  rateLimit = () => true, timeoutMs = 3000 }) {
  const allowed = new Set(origins);
  return async function handle(request) {
    const origin = request.headers.get('origin');
    const headers = new Headers({ Vary: 'Origin', 'Cache-Control': 'no-store' });
    if (origin && allowed.has(origin)) headers.set('Access-Control-Allow-Origin', origin);
    const reply = (body, status = 200) => Response.json(body, { status, headers });
    if (origin && !allowed.has(origin)) return reply({ error: 'Origin is not allowed.' }, 403);
    if (request.method === 'OPTIONS') {
      headers.set('Access-Control-Allow-Methods', 'GET, OPTIONS');
      headers.set('Access-Control-Allow-Headers', 'authorization, apikey, content-type, x-client-info');
      headers.set('Access-Control-Max-Age', '600');
      return new Response(null, { status: 204, headers });
    }
    if (request.method !== 'GET') {
      headers.set('Allow', 'GET, OPTIONS');
      return reply({ error: 'Method is not allowed.' }, 405);
    }
    if (!rateLimit(request)) {
      headers.set('Retry-After', '60');
      return reply({ error: 'Too many searches. Try again shortly.' }, 429);
    }
    const terms = new URL(request.url).searchParams.getAll('q');
    const query = terms[0]?.trim().replace(/[,%()_*]/g, ' ').replace(/\s+/g, ' ');
    if (terms.length !== 1 || terms[0].length > 100 || !query || query.length < 2 || /[\x00-\x1f\x7f]/.test(terms[0])) {
      return reply({ error: 'Provide one school search of 2 to 100 characters.' }, 400);
    }
    const controller = new AbortController();
    const abort = () => controller.abort();
    request.signal.addEventListener('abort', abort, { once: true });
    if (request.signal.aborted) controller.abort();
    let timer;
    try {
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => { controller.abort(); reject(new Error('Search timed out')); }, timeoutMs);
      });
      const rows = await Promise.race([search(query, { signal: controller.signal }), timeout]);
      if (!Array.isArray(rows)) throw new Error('Invalid directory response');
      const items = rows.slice(0, 6).map(publicSchool);
      headers.set('Cache-Control', 'public, max-age=0, s-maxage=60, stale-while-revalidate=120');
      return reply({ items });
    } catch {
      return reply({ error: 'School search is unavailable. Please try again.' }, 503);
    } finally {
      clearTimeout(timer);
      request.signal.removeEventListener('abort', abort);
    }
  };
}
