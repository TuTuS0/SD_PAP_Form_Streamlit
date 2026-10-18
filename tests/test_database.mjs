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
const schema = await readFile(new URL('../schema.sql', import.meta.url), 'utf8');
await db.exec(schema);
await db.exec('set role service_role');
const submit = p => db.query('select public.submit_pixel_batch($1::jsonb, $2::jsonb) as receipt', [p.p_batch, p.p_votes]);
const count = async table => Number((await db.query(`select count(*) as n from public.${table}`)).rows[0].n);

// A complete batch is stored, and repeated identical RPCs are harmless.
assert.equal((await submit(payloads[0])).rows[0].receipt, payloads[0].p_batch.batch_id);
await Promise.all([submit(payloads[0]), submit(payloads[0])]);
assert.equal(await count('pixel_batches'), 1);
assert.equal(await count('pixel_votes'), 15);

// A different payload under the same batch ID cannot overwrite research data.
const changed = structuredClone(payloads[0]);
changed.p_votes[0].prompt = 'tampered';
await assert.rejects(submit(changed));
assert.equal(await count('pixel_votes'), 15);

// A failure at vote 15 rolls back the preceding fourteen votes AND batch header.
const invalid = structuredClone(payloads[1]);
invalid.p_votes[14].winner = 'invalid';
await assert.rejects(submit(invalid));
assert.equal(await count('pixel_votes'), 15);
assert.equal(await count('pixel_batches'), 1);

const short = structuredClone(payloads[1]);
short.p_votes.pop();
await assert.rejects(submit(short));
short.p_votes = short.p_votes.slice(0, 10);
await assert.rejects(submit(short)); // New protocols cannot masquerade as legacy batches.
const wrongResolution = structuredClone(payloads[1]);
wrongResolution.p_votes[0].target_resolution = 64;
await assert.rejects(submit(wrongResolution));
await assert.rejects(submit(payloads[2])); // Cannot skip batch number two.
const wrongId = structuredClone(payloads[1]);
wrongId.p_votes[14].participant_id = '00000000-0000-4000-8000-000000000000';
await assert.rejects(submit(wrongId));
const duplicate = structuredClone(payloads[1]);
duplicate.p_votes[14].comparison_id = duplicate.p_votes[0].comparison_id;
await assert.rejects(submit(duplicate));
assert.equal(await count('pixel_batches'), 1);

// Valid continuation, including correctly flagged pool exhaustion and repeats.
for (const payload of payloads.slice(1)) await submit(payload);
assert.equal(await count('pixel_batches'), 10);
assert.equal(await count('pixel_votes'), 150);
assert.equal(Number((await db.query('select count(*) n from public.pixel_votes where is_repeat')).rows[0].n), 3);
assert.equal((await db.query('select max(comparison_number) n from public.pixel_votes')).rows[0].n, 150);

// The participant/browser roles cannot read votes or invoke the private RPC.
await db.exec('reset role; set role anon');
await assert.rejects(db.query('select * from public.pixel_votes'));
await assert.rejects(submit(payloads[0]));
await db.exec('reset role; set role authenticated');
await assert.rejects(db.query('select * from public.pixel_batches'));
await assert.rejects(submit(payloads[0]));

await db.close();

// Upgrade the old table constraints with a saved ten-vote batch still present.
const transition = JSON.parse(execFileSync(python, ['tests/database_payloads.py', '--legacy-transition'], { cwd: root, encoding:'utf8' }));
const upgrade = new PGlite();
await upgrade.exec('create role anon; create role authenticated; create role service_role bypassrls;');
await upgrade.exec(schema);
const submitUpgrade = p => upgrade.query('select public.submit_pixel_batch($1::jsonb, $2::jsonb)', [p.p_batch, p.p_votes]);
await upgrade.exec('set role service_role');
await submitUpgrade(transition[0]);
await upgrade.exec(`reset role;
  alter table public.pixel_votes drop constraint pixel_votes_batch_comparison_number_check;
  alter table public.pixel_votes add constraint pixel_votes_batch_comparison_number_check check (batch_comparison_number between 1 and 10);
  alter table public.pixel_votes drop constraint pixel_votes_check;
  alter table public.pixel_votes add constraint pixel_votes_check check (comparison_number = (batch_number - 1) * 10 + batch_comparison_number);`);
await upgrade.exec(schema);
await upgrade.exec('set role service_role');
await submitUpgrade(transition[0]); // A legacy acknowledgement remains idempotent.
await submitUpgrade(transition[1]);
assert.equal(Number((await upgrade.query('select count(*) n from public.pixel_votes')).rows[0].n), 25);
assert.deepEqual((await upgrade.query('select comparison_number from public.pixel_votes order by comparison_number')).rows.map(row => row.comparison_number), Array.from({length:25}, (_, i) => i + 1));
await upgrade.close();
console.log('PostgreSQL checks passed: atomic batches, rollback, idempotency, numbering, repeats, and private access.');
