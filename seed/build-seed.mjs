// Turns seed/shortcuts.txt into one JSON document per shortcut (seed/docs/<id>.json)
// plus seed/shortcuts.json (the whole set, importable from the page's Export / Import dialog).
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, 'shortcuts.txt'), 'utf8');
const now = Date.now();
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const docs = [];
const perApp = {};
for (const raw of src.split(/\r?\n/)) {
  const line = raw.trim();
  if (!line || line.startsWith('#')) continue;
  const [app, os, keys, action, note = '', tags = ''] = line.split('|').map(s => s.trim());
  if (!app || !keys || !action) continue;
  perApp[app] = (perApp[app] || 0) + 1;
  const id = `seed-${slug(app)}-${String(perApp[app]).padStart(2, '0')}`;
  docs.push({
    id, app, os, keys, action, note,
    tags: tags ? tags.split(',').map(t => t.trim()).filter(Boolean) : [],
    fav: false, conf: 0, drills: 0, src: 'seed',
    order: docs.length + 1, createdAt: now, updatedAt: now,
  });
}
const out = join(here, 'docs');
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
for (const d of docs) writeFileSync(join(out, `${d.id}.json`), JSON.stringify(d));
writeFileSync(join(here, 'shortcuts.json'), JSON.stringify({ format: 'keycap-atlas', v: 1, shortcuts: docs }, null, 2));
console.log(`${docs.length} shortcuts across ${Object.keys(perApp).length} apps`);
console.log(Object.entries(perApp).map(([a, n]) => `${a}: ${n}`).join(', '));
