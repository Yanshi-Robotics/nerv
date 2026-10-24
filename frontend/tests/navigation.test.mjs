import { test } from 'node:test';
import assert from 'node:assert/strict';
import { canonicalRoute } from '../lib/navigation.ts';

test('new session accepts empty and legacy home bookmarks', () => {
  for (const route of ['', '#', '#home', '#home/new', 'home/new', '#new']) {
    assert.equal(canonicalRoute(route), 'new');
  }
});
test('session and settings links retain their targets', () => {
  for (const route of ['session/s_123', 'robots', 'settings/about', 'settings/updates']) {
    assert.equal(canonicalRoute('#' + route), route);
  }
  assert.equal(canonicalRoute('#session%2Fs_123'), 'session/s_123');
});
test('malformed encoded links recover to new session', () => {
  assert.equal(canonicalRoute('#%not-encoded'), 'new');
});
