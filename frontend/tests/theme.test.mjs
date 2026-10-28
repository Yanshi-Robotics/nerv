import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { THEME_INIT } from '../lib/theme.ts';

function page(saved, dark = false, blocked = false) {
  const dataset = {};
  const events = {};
  const media = { matches: dark, addEventListener: (_, fn) => { events.system = fn; } };
  vm.runInNewContext(THEME_INIT, {
    document: { documentElement: { dataset } },
    window: { matchMedia: () => media, addEventListener: (name, fn) => { events[name] = fn; } },
    localStorage: { getItem: () => { if (blocked) throw Error('denied'); return saved; } },
  });
  return { dataset, events, media };
}

test('first visit and unavailable storage default to light even on a dark system', () => {
  assert.equal(page(null, true).dataset.theme, 'light');
  assert.equal(page(null, true, true).dataset.theme, 'light');
  assert.equal(page('invalid', true).dataset.themePreference, 'light');
});
test('system preference tracks system changes without overriding an explicit choice', () => {
  const p = page('system', true);
  assert.equal(p.dataset.theme, 'dark');
  p.media.matches = false; p.events.system();
  assert.equal(p.dataset.theme, 'light');
  p.dataset.themePreference = 'dark'; p.events.system();
  assert.equal(p.dataset.theme, 'dark');
});
test('saved preferences survive startup and synchronize across tabs', () => {
  const p = page('dark', false);
  assert.equal(p.dataset.theme, 'dark');
  p.events.storage({ key: 'nerv-theme', newValue: 'system' });
  assert.equal(p.dataset.themePreference, 'system');
  assert.equal(p.dataset.theme, 'light');
  p.events.storage({ key: null, newValue: null });
  assert.equal(p.dataset.themePreference, 'light');
});
