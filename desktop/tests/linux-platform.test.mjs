import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { getEngineEndpoint, invalidateEngineEndpoint } from '../src/lib/api.ts';
import { fallbackPlatform } from '../src/lib/platformInfo.ts';

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  delete globalThis.window;
  invalidateEngineEndpoint();
});

test('unknown Linux session does not claim global input capabilities', () => {
  const info = fallbackPlatform('Mozilla/5.0 X11; Linux aarch64');
  assert.equal(info.platform, 'linux');
  assert.equal(info.session, 'headless');
  assert.equal(info.automatic_insertion, false);
  assert.equal(info.global_hotkey, false);
  assert.equal(info.overlay, false);
  assert.equal(info.hotkey_label, null);
});

test('packaged startup rejects a missing runtime without fetching Vite middleware', async () => {
  let calls = 0;
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => null } };
  globalThis.fetch = async () => { calls++; throw new Error('Unexpected dev request'); };
  await assert.rejects(getEngineEndpoint(), /starting/);
  assert.equal(calls, 0);
});

test('packaged startup can recover when runtime becomes available', async () => {
  let endpoint = null;
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => endpoint } };
  await assert.rejects(getEngineEndpoint(), /starting/);
  endpoint = { host: '127.0.0.1', port: 43123, token: 'test-runtime-only' };
  assert.deepEqual(await getEngineEndpoint(), endpoint);
});

test('a packaged IPC error never falls through to browser development discovery', async () => {
  let calls = 0;
  globalThis.window = { __TAURI_INTERNALS__: { invoke: async () => { throw new Error('IPC unavailable'); } } };
  globalThis.fetch = async () => { calls++; return new Response('{}'); };
  await assert.rejects(getEngineEndpoint(), /IPC unavailable/);
  assert.equal(calls, 0);
});

test('plain-browser development still discovers the local engine', async () => {
  const endpoint = { host: '127.0.0.1', port: 43124, token: 'dev-test-only' };
  globalThis.fetch = async (path) => {
    assert.equal(path, '/__say/runtime');
    return new Response(JSON.stringify(endpoint), { status: 200 });
  };
  assert.deepEqual(await getEngineEndpoint(), endpoint);
});
