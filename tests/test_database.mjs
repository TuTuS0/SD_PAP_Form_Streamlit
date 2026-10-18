// Isolated, in-memory PostgreSQL tests. Never contacts a Supabase project.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { PGlite } from '../.test-tools/node_modules/@electric-sql/pglite/dist/index.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const python = process.argv[2] ?? (process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const payloads = JSON.parse(execFileSync(python, ['tests/database_payloads.py'], { cwd: root, encoding: 'utf8' }));
const db = new PGlite();
await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
await db.exec(await readFile(new URL('../schema.sql', import.meta.url), 'utf8'));
await db.exec('set role service_role');
const submit = p => db.query('select public.submit_pixel_batch($1::jsonb, $2::jsonb) as receipt', [p.p_batch, p.p_votes]);
const count = async table => Number((await db.query(`select count(*) as n from public.${table}`)).rows[0].n);

// A complete batch is stored, and repeated identical RPCs are harmless.
assert.equal((await submit(payloads[0])).rows[0].receipt, payloads[0].p_batch.batch_id);
await Promise.all([submit(payloads[0]), submit(payloads[0])]);
assert.equal(await count('pixel_batches'), 1);
assert.equal(await count('pixel_votes'), 10);

// A different payload under the same batch ID cannot overwrite research data.
const changed = structuredClone(payloads[0]);
changed.p_votes[0].prompt = 'tampered';
await assert.rejects(submit(changed));
assert.equal(await count('pixel_votes'), 10);

// A failure at vote 10 rolls back the preceding nine votes AND batch header.
const invalid = structuredClone(payloads[1]);
invalid.p_votes[9].winner = 'invalid';
await assert.rejects(submit(invalid));
assert.equal(await count('pixel_votes'), 10);
assert.equal(await count('pixel_batches'), 1);

const short = structuredClone(payloads[1]);
short.p_votes.pop();
await assert.rejects(submit(short));
await assert.rejects(submit(payloads[2])); // Cannot skip batch number two.
const wrongId = structuredClone(payloads[1]);
wrongId.p_votes[9].participant_id = '00000000-0000-4000-8000-000000000000';
await assert.rejects(submit(wrongId));
const duplicate = structuredClone(payloads[1]);
duplicate.p_votes[9].comparison_id = duplicate.p_votes[0].comparison_id;
await assert.rejects(submit(duplicate));
assert.equal(await count('pixel_batches'), 1);

// Valid continuation, including correctly flagged pool exhaustion and repeats.
for (const payload of payloads.slice(1)) await submit(payload);
assert.equal(await count('pixel_batches'), 10);
assert.equal(await count('pixel_votes'), 100);
assert.equal(Number((await db.query('select count(*) n from public.pixel_votes where is_repeat')).rows[0].n), 9);
assert.equal((await db.query('select max(comparison_number) n from public.pixel_votes')).rows[0].n, 100);

// The participant/browser roles cannot read votes or invoke the private RPC.
await db.exec('reset role; set role anon');
await assert.rejects(db.query('select * from public.pixel_votes'));
await assert.rejects(submit(payloads[0]));
await db.exec('reset role; set role authenticated');
await assert.rejects(db.query('select * from public.pixel_batches'));
await assert.rejects(submit(payloads[0]));

await db.close();
console.log('PostgreSQL checks passed: atomic batches, rollback, idempotency, numbering, repeats, and private access.');
