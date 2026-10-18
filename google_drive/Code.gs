/**
 * Pixel Art Preference Study: one private CSV per completed batch.
 * Paste into an Apps Script project owned by the Drive folder's owner.
 * Script properties: RESULTS_FOLDER_ID and SHARED_SECRET (32+ random chars).
 * Deploy as a web app, execute as yourself, access Anyone. Requests still need
 * a valid HMAC from the Streamlit server; no results are exposed over HTTP.
 */
const VOTE_COLUMNS = [
  'participant_id', 'batch_id', 'batch_number', 'comparison_number',
  'batch_comparison_number', 'comparison_id', 'prompt_id', 'prompt',
  'target_resolution', 'left_method', 'right_method', 'left_image_id',
  'right_image_id', 'left_path', 'right_path', 'left_sha256', 'right_sha256',
  'selected_side', 'winner', 'answered_at', 'pool_cycle', 'is_repeat', 'repeat_reason',
];
const CSV_COLUMNS = VOTE_COLUMNS.concat([
  'dataset_version', 'sampler_version', 'sampling_seed', 'submitted_at', 'submission_sha256',
]);

function doGet() {
  return jsonReply_({service: 'pixel-study-csv', requires_signed_post: true});
}

function doPost(event) {
  let lock;
  let locked = false;
  try {
    const properties = PropertiesService.getScriptProperties();
    const secret = properties.getProperty('SHARED_SECRET');
    const folderId = properties.getProperty('RESULTS_FOLDER_ID');
    if (!secret || secret.length < 32 || secret.startsWith('REPLACE') || !folderId) {
      throw new Error('Setup incomplete');
    }
    const body = event && event.postData && event.postData.contents;
    if (typeof body !== 'string' || body.length > 1000000) throw new Error('Invalid request');
    const envelope = JSON.parse(body);
    if (typeof envelope.payload !== 'string' || typeof envelope.signature !== 'string') {
      throw new Error('Invalid request');
    }
    const signature = hex_(Utilities.computeHmacSha256Signature(envelope.payload, secret, Utilities.Charset.UTF_8));
    if (!sameSignature_(signature, envelope.signature)) throw new Error('Invalid signature');
    const payload = JSON.parse(envelope.payload);
    validateBatch_(payload);
    const batch = payload.p_batch;
    const digest = hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, envelope.payload, Utilities.Charset.UTF_8));

    lock = LockService.getScriptLock();
    locked = lock.tryLock(20000);
    if (!locked) throw new Error('Storage busy');
    const folder = DriveApp.getFolderById(folderId);
    const name = batchFilename_(batch.participant_id, batch.batch_number);
    const existing = folder.getFilesByName(name);
    if (existing.hasNext()) {
      const file = existing.next();
      if (existing.hasNext()) throw new Error('Duplicate storage files');
      const text = file.getBlob().getDataAsString('UTF-8');
      const rows = Utilities.parseCsv(text).filter(row => row.some(value => value !== ''));
      if (rows.length !== 11 || JSON.stringify(rows[0]) !== JSON.stringify(CSV_COLUMNS)) {
        throw new Error('Conflicting stored batch');
      }
      const timestamp = rows[1][CSV_COLUMNS.indexOf('submitted_at')];
      // Compare the WHOLE CSV, including metadata, not just its name/hash field.
      // This also handles a lost HTTP reply immediately after file creation.
      if (text !== batchCsv_(payload, digest, timestamp)) throw new Error('Conflicting stored batch');
      return jsonReply_({ok: true, batch_id: batch.batch_id, row_count: 10, submission_sha256: digest});
    }
    if (batch.batch_number > 1 && !folder.getFilesByName(batchFilename_(batch.participant_id, batch.batch_number - 1)).hasNext()) {
      throw new Error('Previous batch has not been submitted');
    }
    const content = batchCsv_(payload, digest, new Date().toISOString());
    // One whole-file creation. No shared CSV append and no separate metadata
    // write: the CSV itself contains all 10 votes and its submission fingerprint.
    folder.createFile(Utilities.newBlob(content, 'text/csv', name));
    return jsonReply_({ok: true, batch_id: batch.batch_id, row_count: 10, submission_sha256: digest});
  } catch (error) {
    // Avoid logging votes, signatures, secrets, account information, or errors
    // from Google's internal APIs. Streamlit keeps the batch available to retry.
    return jsonReply_({ok: false, error: 'Batch not confirmed. Check setup or retry.'});
  } finally {
    if (locked) lock.releaseLock();
  }
}

function validateBatch_(payload) {
  const batch = payload && payload.p_batch;
  const votes = payload && payload.p_votes;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const hash = /^[0-9a-f]{64}$/;
  const methods = ['SDPAP', 'AOP', 'APIP', 'FLUX', 'GPTImage', 'MYOS', 'PAP', 'PIA'];
  if (!batch || !Array.isArray(votes) || votes.length !== 10
      || !uuid.test(batch.participant_id) || !uuid.test(batch.batch_id)
      || !Number.isSafeInteger(batch.batch_number) || batch.batch_number < 1
      || !hash.test(batch.dataset_version) || typeof batch.sampler_version !== 'string' || !batch.sampler_version
      || typeof batch.sampling_seed !== 'string' || !batch.sampling_seed) throw new Error('Invalid batch');
  const pairIds = new Set();
  const referenceHashes = new Set();
  votes.forEach((vote, index) => {
    if (!vote || VOTE_COLUMNS.some(key => !Object.prototype.hasOwnProperty.call(vote, key))
        || vote.participant_id !== batch.participant_id || vote.batch_id !== batch.batch_id
        || vote.batch_number !== batch.batch_number || vote.batch_comparison_number !== index + 1
        || vote.comparison_number !== (batch.batch_number - 1) * 10 + index + 1
        || !hash.test(vote.comparison_id) || !hash.test(vote.left_sha256) || !hash.test(vote.right_sha256)
        || !Number.isSafeInteger(vote.target_resolution) || vote.target_resolution < 1
        || !methods.includes(vote.left_method) || !methods.includes(vote.right_method)
        || ((vote.left_method === 'SDPAP') === (vote.right_method === 'SDPAP'))
        || !['left', 'right'].includes(vote.selected_side)
        || vote.winner !== (vote.selected_side === 'left' ? vote.left_method : vote.right_method)
        || !Number.isSafeInteger(vote.pool_cycle) || vote.pool_cycle < 1
        || typeof vote.is_repeat !== 'boolean'
        || vote.is_repeat !== (vote.pool_cycle > 1)
        || vote.repeat_reason !== (vote.is_repeat ? 'pool_exhausted' : '')
        || typeof vote.answered_at !== 'string' || !Number.isFinite(Date.parse(vote.answered_at))) {
      throw new Error('Invalid vote');
    }
    ['prompt_id', 'prompt', 'left_image_id', 'right_image_id', 'left_path', 'right_path'].forEach(key => {
      if (typeof vote[key] !== 'string') throw new Error('Invalid image metadata');
    });
    if (!vote.prompt_id || pairIds.has(vote.comparison_id)) throw new Error('Duplicate comparison');
    pairIds.add(vote.comparison_id);
    const referenceHash = vote.left_method === 'SDPAP' ? vote.left_sha256 : vote.right_sha256;
    if (referenceHashes.has(referenceHash)) throw new Error('Repeated reference image in batch');
    referenceHashes.add(referenceHash);
  });
}

function batchFilename_(participantId, batchNumber) {
  return 'pixel_votes_' + participantId + '_batch_' + String(batchNumber).padStart(6, '0') + '.csv';
}

function batchCsv_(payload, digest, timestamp) {
  const batch = payload.p_batch;
  const rows = [CSV_COLUMNS];
  payload.p_votes.forEach(vote => rows.push(CSV_COLUMNS.map(key => {
    if (key === 'submitted_at') return timestamp;
    if (key === 'submission_sha256') return digest;
    if (['dataset_version', 'sampler_version', 'sampling_seed'].includes(key)) return batch[key];
    return vote[key];
  })));
  return rows.map(row => row.map(value => '"' + String(value).replace(/"/g, '""') + '"').join(',')).join('\r\n') + '\r\n';
}

function sameSignature_(left, right) {
  if (left.length !== right.length) return false;
  let different = 0;
  for (let index = 0; index < left.length; index++) different |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return different === 0;
}

function hex_(bytes) {
  return bytes.map(value => (value & 255).toString(16).padStart(2, '0')).join('');
}

function jsonReply_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

/** Run manually in the editor once to authorize Drive and verify properties. */
function checkSetup() {
  const properties = PropertiesService.getScriptProperties();
  const secret = properties.getProperty('SHARED_SECRET');
  if (!secret || secret.length < 32 || secret.startsWith('REPLACE')) throw new Error('Set a random SHARED_SECRET with 32+ characters.');
  const folderId = properties.getProperty('RESULTS_FOLDER_ID');
  if (!folderId) throw new Error('Set RESULTS_FOLDER_ID.');
  DriveApp.getFolderById(folderId).getId();
  console.log('Storage folder is accessible. No votes were written.');
}
