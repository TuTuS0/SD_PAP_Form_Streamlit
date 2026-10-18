// Exercise the actual client-side share logic without posting or opening a browser.
import assert from 'node:assert/strict';
import test from 'node:test';
import { copyMessage, messageText, messagingLinks, shareInApp, sharePayload } from '../share_assets/share.mjs';

const data = { title:'Mon score pixel art', text:'J’ai évalué 30 paires. À ton tour !' };
const current = 'https://pixel-study.streamlit.app/?participant_id=private&token=secret#session';
const payload = sharePayload(data, current);

test('sharing uses the study URL without query parameters or session fragments', () => {
  assert.deepEqual(payload, { ...data, url:'https://pixel-study.streamlit.app/' });
  assert.equal(messageText(payload), `${data.text}\n\nhttps://pixel-study.streamlit.app/`);
});

test('a configured public URL preserves the study path and removes credentials and parameters', () => {
  const configured = sharePayload({ ...data, public_url:'https://user:password@example.org/pixel-study?secret=x#stats' }, current);
  assert.equal(configured.url, 'https://example.org/pixel-study');
  assert.equal(sharePayload({ ...data, public_url:'javascript:alert(1)' }, current).url, payload.url);
  assert.equal(sharePayload({ ...data, public_url:'http://[' }, current).url, payload.url);
});

test('native sharing receives the score, invitation and clean URL', async () => {
  const sent = [];
  const copied = [];
  assert.equal(await shareInApp(payload, {
    share:async value => sent.push(value),
    clipboard:{ writeText:async value => copied.push(value) },
  }), 'shared');
  assert.deepEqual(sent, [payload]);
  assert.deepEqual(copied, []);
});

test('cancelling the share sheet does not copy or retry anything', async () => {
  const copied = [];
  assert.equal(await shareInApp(payload, {
    share:async () => { throw { name:'AbortError' }; },
    clipboard:{ writeText:async value => copied.push(value) },
  }), 'cancelled');
  assert.deepEqual(copied, []);
});

test('without native sharing, the user is directed to messaging rather than copying', async () => {
  const copied = [];
  assert.equal(await shareInApp(payload, {
    clipboard:{ writeText:async value => copied.push(value) },
  }), 'messaging');
  assert.deepEqual(copied, []);
});

test('blocked native sharing offers messaging apps without copying', async () => {
  const copied = [];
  assert.equal(await shareInApp(payload, {
    share:async () => { throw { name:'NotAllowedError' }; },
    clipboard:{ writeText:async value => copied.push(value) },
  }), 'messaging');
  assert.deepEqual(copied, []);
});

test('explicit copying includes the whole invitation and link', async () => {
  const copied = [];
  assert.equal(await copyMessage(payload, {
    clipboard:{ writeText:async value => copied.push(value) },
  }), 'copied');
  assert.deepEqual(copied, [messageText(payload)]);
});

test('unavailable or denied clipboard leaves the message ready for manual copying', async () => {
  assert.equal(await copyMessage(payload, {}), 'manual');
  assert.equal(await copyMessage(payload, {
    clipboard:{ writeText:async () => { throw new Error('Permission denied'); } },
  }), 'manual');
});

test('messaging links prefill the full message and link without choosing any recipient', () => {
  const links = messagingLinks(payload, { userAgent:'Android' });
  const whatsapp = new URL(links.whatsapp);
  assert.equal(whatsapp.origin, 'https://wa.me');
  assert.equal(whatsapp.pathname, '/');
  assert.equal(whatsapp.searchParams.get('text'), messageText(payload));
  assert.ok(links.messages.startsWith('sms:?body='));
  assert.equal(decodeURIComponent(links.messages.split('body=')[1]), messageText(payload));
  assert.ok(!decodeURIComponent(links.messages).includes('participant_id'));
});

test('Messages links handle iPhone and iPad desktop mode separately from desktop Macs', () => {
  for (const device of [{userAgent:'iPhone'}, {platform:'MacIntel', maxTouchPoints:5}]) {
    const links = messagingLinks(payload, device);
    assert.ok(links.messages.startsWith('sms:&body='));
    assert.equal(decodeURIComponent(links.messages.split('body=')[1]), messageText(payload));
  }
  assert.ok(messagingLinks(payload, {platform:'MacIntel', maxTouchPoints:0}).messages.startsWith('sms:?body='));
});
