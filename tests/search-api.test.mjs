import test from 'node:test';
import assert from 'node:assert/strict';
import { createSchoolSearchHandler } from '../school_search_api.mjs';

const school = { id: '00000000-0000-4000-8000-000000000001', name: 'Fictional Primary',
  level: 'primary', province: 'Limpopo', town: 'Fictional Town', municipality: null, verified: false,
  donor_email: 'private@example.invalid', principal: 'PRIVATE PERSON' };
const request = (q = 'school', options = {}) => new Request('https://example.invalid/search?q=' + encodeURIComponent(q), options);

test('returns only public fields and caps results at six', async () => {
  const handle = createSchoolSearchHandler({ search: async () => Array(10).fill(school) });
  const response = await handle(request());
  const body = await response.json();
  assert.equal(body.items.length, 6);
  assert.deepEqual(Object.keys(body.items[0]).sort(), ['id','level','municipality','name','province','town','verified']);
  assert.equal(JSON.stringify(body).includes('private@example.invalid'), false);
});
test('validates duplicate, empty, short, long and control-character queries before lookup', async () => {
  let calls = 0;
  const handle = createSchoolSearchHandler({ search: async () => { calls++; return []; } });
  for (const query of ['', 'x', 'x'.repeat(101), '\u0000school', '__']) assert.equal((await handle(request(query))).status, 400);
  assert.equal((await handle(new Request('https://example.invalid/?q=one&q=two'))).status, 400);
  assert.equal(calls, 0);
});
test('allows actual demo-origin CORS and handles OPTIONS', async () => {
  const handle = createSchoolSearchHandler({ search: async () => [] });
  const response = await handle(request('school', { headers: { origin: 'https://skolo-saka.github.io' } }));
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), 'https://skolo-saka.github.io');
  assert.equal(response.headers.get('Vary'), 'Origin');
  assert.equal((await handle(request('school', { method: 'OPTIONS', headers: { origin: 'https://skolo-saka.github.io' } }))).status, 204);
});
test('rejects unapproved origins without CORS or lookup', async () => {
  let calls = 0;
  const handle = createSchoolSearchHandler({ search: async () => { calls++; return []; } });
  const response = await handle(request('school', { headers: { origin: 'https://evil.invalid' } }));
  assert.equal(response.status, 403); assert.equal(response.headers.has('Access-Control-Allow-Origin'), false); assert.equal(calls, 0);
});
test('rejects non-GET writes', async () => {
  const handle = createSchoolSearchHandler({ search: async () => [] });
  assert.equal((await handle(request('school', { method: 'POST' }))).status, 405);
});
test('rate-limited errors are not cached', async () => {
  const handle = createSchoolSearchHandler({ search: async () => [], rateLimit: () => false });
  const response = await handle(request());
  assert.equal(response.status, 429); assert.equal(response.headers.get('Cache-Control'), 'no-store'); assert.equal(response.headers.get('Retry-After'), '60');
});
test('database errors do not leak details or produce fixtures', async () => {
  const handle = createSchoolSearchHandler({ search: async () => { throw new Error('SECRET database connection'); } });
  const response = await handle(request());
  assert.equal(response.status, 503); assert.equal((await response.text()).includes('SECRET'), false);
});
test('timeout aborts lookup and returns a bounded error', async () => {
  let signal;
  const handle = createSchoolSearchHandler({ timeoutMs: 10, search: async (_, options) => { signal = options.signal; return new Promise(() => {}); } });
  assert.equal((await handle(request())).status, 503); assert.equal(signal.aborted, true);
});
test('rejects malformed upstream records', async () => {
  const handle = createSchoolSearchHandler({ search: async () => [{ ...school, id: 'demo-001' }] });
  assert.equal((await handle(request())).status, 503);
});
