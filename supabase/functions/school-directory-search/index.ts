import { createSchoolSearchHandler } from '../../../school_search_api.mjs';

// Use only the anonymous/public-directory database role. Never the service-role key.
const base = Deno.env.get('SUPABASE_URL');
const key = Deno.env.get('SUPABASE_ANON_KEY');
const counters = new Map<string, { count: number; reset: number }>();

const handler = createSchoolSearchHandler({
  rateLimit(request: Request) {
    // Supabase gateway appends the peer to X-Forwarded-For. Never trust the first entry.
    const ip = request.headers.get('x-forwarded-for')?.split(',').at(-1)?.trim() || 'shared';
    const now = Date.now();
    if (counters.size > 10000) {
      for (const [k, v] of counters) if (v.reset <= now) counters.delete(k);
      if (counters.size > 10000) return false;
    }
    let counter = counters.get(ip);
    if (!counter || counter.reset <= now) { counter = { count: 0, reset: now + 60000 }; counters.set(ip, counter); }
    counter.count++;
    return counter.count <= 120;
  },
  async search(query: string, { signal }: { signal: AbortSignal }) {
    if (!base || !key) throw new Error('Directory configuration is missing');
    const response = await fetch(base + '/rest/v1/rpc/search_school_directory', {
      method: 'POST', signal,
      headers: { apikey: key, Authorization: 'Bearer ' + key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ p_query: query, p_area: '', p_province: null, p_level: null, p_verified_only: false, p_page: 0 }),
    });
    if (!response.ok) throw new Error('Directory lookup failed');
    const page = await response.json();
    if (!Array.isArray(page.items)) throw new Error('Unexpected directory response');
    return page.items;
  },
});

Deno.serve(handler);
