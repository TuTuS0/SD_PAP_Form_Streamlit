// Exercise the actual Apps Script source against isolated Google API mocks.
// No Google account or live Drive files are accessed by this test.
import assert from 'node:assert/strict';
import { createHash, createHmac, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = fileURLToPath(new URL('../', import.meta.url));
const python = process.argv[2] ?? (process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const payloads = JSON.parse(execFileSync(python, ['tests/database_payloads.py'], { cwd: root, encoding: 'utf8' }));
const secret = 'local-tests-only-' + 'x'.repeat(32);
const files = new Map();
let locked = false;
let writes = 0;
let busy = false;
let failAfterWrite = false;

function parseCsv(text) {
  const rows = [];
  let row = [], cell = '', quoted = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') { cell += '"'; index++; }
      else quoted = !quoted;
    } else if (!quoted && char === ',') { row.push(cell); cell = ''; }
    else if (!quoted && (char === '\n' || char === '\r')) {
      if (char === '\r' && text[index + 1] === '\n') index++;
      row.push(cell); rows.push(row); row = []; cell = '';
    } else cell += char;
  }
  if (row.length || cell.length) { row.push(cell); rows.push(row); }
  return rows;
}

const folder = {
  getId: () => 'test-folder',
  getFilesByName: name => {
    const result = files.get(name) ?? [];
    let index = 0;
    return {hasNext: () => index < result.length, next: () => result[index++]};
  },
  createFile: blob => {
    assert.equal(locked, true, 'Every creation must happen while holding the script lock');
    const file = {text: blob.text, getBlob() { return {getDataAsString: () => this.text}; }};
    files.set(blob.name, [...(files.get(blob.name) ?? []), file]);
    writes++;
    if (failAfterWrite) { failAfterWrite = false; throw new Error('Lost acknowledgement after whole-file creation'); }
    return file;
  },
};

const context = vm.createContext({
  console,
  PropertiesService: {getScriptProperties: () => ({getProperty: key => ({SHARED_SECRET: secret, RESULTS_FOLDER_ID: 'test-folder'})[key]})},
  DriveApp: {getFolderById: id => { assert.equal(id, 'test-folder'); return folder; }},
  LockService: {getScriptLock: () => ({
    tryLock() { if (busy || locked) return false; locked = true; return true; },
    releaseLock() { assert.equal(locked, true); locked = false; },
  })},
  ContentService: {MimeType: {JSON: 'application/json'}, createTextOutput: value => ({value, setMimeType() { return this; }})},
  Utilities: {
    Charset: {UTF_8: 'UTF-8'}, DigestAlgorithm: {SHA_256: 'SHA_256'},
    computeHmacSha256Signature: (text, key) => [...createHmac('sha256', key).update(text).digest()].map(byte => byte > 127 ? byte - 256 : byte),
    computeDigest: (algorithm, text) => [...createHash('sha256').update(text).digest()].map(byte => byte > 127 ? byte - 256 : byte),
    newBlob: (text, mime, name) => ({text, mime, name}), parseCsv,
  },
});
vm.runInContext(await readFile(new URL('../google_drive/Code.gs', import.meta.url), 'utf8'), context);
const envelope = payload => {
  const text = JSON.stringify(payload);
  return {payload: text, signature: createHmac('sha256', secret).update(text).digest('hex')};
};
const postEnvelope = value => JSON.parse(context.doPost({postData: {contents: JSON.stringify(value)}}).value);
const submit = payload => postEnvelope(envelope(payload));
const anotherParticipant = payload => {
  const result = structuredClone(payload);
  result.p_batch.participant_id = randomUUID();
  result.p_batch.batch_id = randomUUID();
  for (const vote of result.p_votes) {
    vote.participant_id = result.p_batch.participant_id;
    vote.batch_id = result.p_batch.batch_id;
  }
  return result;
};

const first = structuredClone(payloads[0]);
first.p_votes[0].prompt = 'Une étoile, "bleue"\nsur fond noir 🌟';
assert.equal(submit(first).ok, true);
assert.equal(writes, 1);
const stored = [...files.values()][0][0];
const rows = parseCsv(stored.text);
assert.equal(rows.length, 16);
assert.equal(rows[1][rows[0].indexOf('prompt')], first.p_votes[0].prompt);
assert.equal(rows[1][rows[0].indexOf('sampling_seed')], first.p_batch.sampling_seed);
assert.equal(rows[15][rows[0].indexOf('comparison_number')], '15');
assert.deepEqual(rows.slice(1).map(row => Number(row[rows[0].indexOf('target_resolution')])), [48,64,128,48,64,128,48,64,128,48,64,128,48,64,128]);
assert.equal(submit(first).ok, true);
assert.equal(writes, 1, 'An identical retry must not create another CSV');

const conflicting = structuredClone(first);
conflicting.p_votes[0].prompt = 'modified payload';
assert.equal(submit(conflicting).ok, false);
const changedId = structuredClone(first);
changedId.p_batch.batch_id = randomUUID();
changedId.p_votes.forEach(vote => { vote.batch_id = changedId.p_batch.batch_id; });
assert.equal(submit(changedId).ok, false, 'A new ID cannot overwrite the same participant/batch slot');
assert.equal(writes, 1);

const savedText = stored.text;
stored.text = stored.text.replace('Une étoile', 'Tampered text');
assert.equal(submit(first).ok, false, 'Compare the entire stored CSV, not only its hash column');
stored.text = savedText;

const invalidSignature = envelope(first);
invalidSignature.signature = '0'.repeat(64);
assert.equal(postEnvelope(invalidSignature).ok, false);
const partial = structuredClone(first);
partial.p_votes.pop();
assert.equal(submit(partial).ok, false);
partial.p_votes = partial.p_votes.slice(0, 10);
assert.equal(submit(partial).ok, false, 'Ten votes cannot complete a new-protocol batch');
const wrongResolution = structuredClone(first);
wrongResolution.p_votes[0].target_resolution = 64;
assert.equal(submit(wrongResolution).ok, false);
const wrongOffset = anotherParticipant(first);
wrongOffset.p_votes.forEach(vote => { vote.comparison_number++; });
assert.equal(submit(wrongOffset).ok, false);
const tie = structuredClone(first);
tie.p_votes[0].selected_side = 'tie'; tie.p_votes[0].winner = 'tie';
assert.equal(submit(tie).ok, false);
const duplicatedReference = structuredClone(first);
const ref = vote => vote.left_method === 'SDPAP' ? 'left_sha256' : 'right_sha256';
duplicatedReference.p_votes[1][ref(duplicatedReference.p_votes[1])] = duplicatedReference.p_votes[0][ref(duplicatedReference.p_votes[0])];
assert.equal(submit(duplicatedReference).ok, false);
assert.equal(submit(anotherParticipant(payloads[1])).ok, false, 'Batch two needs a previous saved batch');
assert.equal(writes, 1);

busy = true;
assert.equal(submit(anotherParticipant(payloads[0])).ok, false);
busy = false;
assert.equal(writes, 1);

const uncertain = anotherParticipant(payloads[0]);
failAfterWrite = true;
assert.equal(submit(uncertain).ok, false);
assert.equal(writes, 2);
assert.equal(submit(uncertain).ok, true);
assert.equal(writes, 2, 'A lost acknowledgement after file creation is safe to retry');
for (const payload of payloads.slice(1)) assert.equal(submit(payload).ok, true);
assert.equal(writes, 11);
assert.equal(locked, false);
assert.deepEqual(JSON.parse(context.doGet().value), {service: 'pixel-study-csv', requires_signed_post: true});
const transition = JSON.parse(execFileSync(python, ['tests/database_payloads.py', '--legacy-transition'], { cwd: root, encoding:'utf8' }));
assert.equal(submit(transition[0]).row_count, 10);
assert.equal(submit(transition[0]).row_count, 10);
assert.equal(submit(transition[1]).row_count, 15);
assert.equal(writes, 13);
console.log('Apps Script checks passed: signed requests, 15-vote CSVs, resolution order, legacy continuation, lock, safe retries, conflicts, and numbering.');
