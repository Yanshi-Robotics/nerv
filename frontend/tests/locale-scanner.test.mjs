import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { scanKeys } from '../scripts/scan-keys.mjs';

test('literal translations include either quote style and escaped apostrophes', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'nerv-locales-'));
  try {
    fs.writeFileSync(path.join(directory, 'view.tsx'), String.raw`t("Double"); t('Single'); t('User\'s model'); t(dynamic);`);
    assert.deepEqual([...scanKeys(directory).keys()], ['Double', 'Single', "User's model"]);
  } finally {
    fs.rmSync(directory, {recursive: true});
  }
});
