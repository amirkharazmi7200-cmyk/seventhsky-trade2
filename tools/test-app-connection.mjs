import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const client = readFileSync(new URL('../deploy-app/backend-client.js', import.meta.url), 'utf8');
const page = readFileSync(new URL('../deploy-app/index.html', import.meta.url), 'utf8');
let response;
const clientContext = {
  window: {}, URL, location: { href: 'https://app.example.test/' },
  fetch: async () => { if (response instanceof Error) throw response; return response; },
};
vm.runInNewContext(client, clientContext);
const api = clientContext.window.SevenSkyAPI;
const jsonResponse = (status, body) => ({ status, ok: status === 200, json: async () => body });

response = jsonResponse(401, { ok: false, error: 'unauthorized' });
await assert.rejects(api.leads(), e => e.code === 'unauthorized' && e.message.includes('کلید اتصال'));
response = jsonResponse(503, { ok: false, error: 'app_proxy_token_missing' });
await assert.rejects(api.health(), e => e.code === 'app_proxy_token_missing');
response = { status: 200, ok: true, json: async () => { throw new Error('HTML response'); } };
await assert.rejects(api.health(), e => e.code === 'invalid_response');
response = jsonResponse(200, {});
await assert.rejects(api.health(), e => e.code === 'invalid_response');
response = new Error('network failure');
await assert.rejects(api.health(), e => e.code === 'network_error');
response = jsonResponse(200, { ok: true, leads: [{ id: 'buyer-1' }] });
assert.equal((await api.leads())[0].id, 'buyer-1');

// Run the actual connection lifecycle with controlled API responses.
const start = page.indexOf('      function setConnection(');
const end = page.indexOf('      document.querySelectorAll("[data-view]")', start);
assert.ok(start > 0 && end > start);
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    const classes = new Set();
    nodes.set(id, { textContent: '', innerHTML: '', className: '', classes,
      classList: { add: name => classes.add(name), remove: (...names) => names.forEach(name => classes.delete(name)) } });
  }
  return nodes.get(id);
}
let mode = 'unauthorized';
let renders = 0;
const state = { ready: false, view: 'radar', leads: [], radar: [], leadStates: new Map(), radarStates: new Map() };
const lifecycle = {
  state, $: node, location: { hash: '#radar' }, Intl, Date, esc: text => text,
  normalizeRadarEvent: event => event,
  renderOverview: () => renders++, renderRadar() {}, renderBuyers() {}, renderToday() {}, setView() {},
  SevenSkyAPI: {
    health: async () => ({ ok: true }),
    leads: async () => {
      if (mode === 'unauthorized') throw new Error('unauthorized');
      return [{ id: 'buyer-1' }];
    },
    inbox: async () => {
      if (mode === 'offline') throw new Error('network failure');
      return [];
    },
    state: async () => {
      if (mode === 'state-denied') throw new Error('unauthorized');
      return {};
    },
  },
};
vm.runInNewContext(page.slice(start, end), lifecycle);
await lifecycle.boot();
assert.equal(state.ready, false);
assert.equal(renders, 0);
assert.ok(node('live').classes.has('bad'));
assert.ok(!node('live').classes.has('ok'));
assert.equal(node('data-status').className, 'pill tag bad');
mode = 'online';
await lifecycle.refreshRadarOnly();
assert.equal(state.ready, true);
assert.equal(state.leads.length, 1);
assert.ok(node('live').classes.has('ok'));
assert.ok(!node('live').classes.has('bad'));
mode = 'offline';
await lifecycle.refreshRadarOnly();
assert.equal(node('data-status').className, 'pill tag bad');
assert.equal(state.leads.length, 1);
mode = 'online';
await lifecycle.refreshRadarOnly();
assert.equal(node('data-status').className, 'pill tag good');
mode = 'state-denied';
await lifecycle.boot();
assert.equal(state.ready, false);
assert.equal(node('data-status').className, 'pill tag bad');
console.log('PASS: API errors, invalid responses, failed authorization, reconnection, stale-data status, and approval-state read failures.');
