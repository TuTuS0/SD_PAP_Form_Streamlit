// V2 components run in the app page, preserving the user's click for Web Share.
export function sharePayload(data, currentUrl) {
  const current = new URL(currentUrl);
  let url;
  try { url = new URL(data.public_url || currentUrl, current); }
  catch { url = new URL(currentUrl); }
  if (!['http:', 'https:'].includes(url.protocol)) url = new URL(currentUrl);
  url.search = '';
  url.hash = '';
  url.username = '';
  url.password = '';
  return { title: data.title, text: data.text, url: url.href };
}

export function messageText(payload) {
  return `${payload.text}\n\n${payload.url}`;
}

export function messagingLinks(payload, device = {}) {
  const encoded = encodeURIComponent(messageText(payload));
  const ios = /iPhone|iPad|iPod/.test(device.userAgent || '')
    || (device.platform === 'MacIntel' && device.maxTouchPoints > 1);
  return {
    messages: `sms:${ios ? '&' : '?'}body=${encoded}`,
    whatsapp: `https://wa.me/?text=${encoded}`,
  };
}

export async function shareInApp(payload, capabilities) {
  if (typeof capabilities.share === 'function') {
    try {
      await capabilities.share(payload);
      return 'shared';
    } catch (error) {
      if (error?.name === 'AbortError') return 'cancelled';
    }
  }
  return 'messaging';
}

export async function copyMessage(payload, capabilities) {
  try {
    await capabilities.clipboard.writeText(messageText(payload));
    return 'copied';
  } catch {
    return 'manual';
  }
}

export default function mount({ parentElement, data }) {
  let disposed = false;
  const button = parentElement.querySelector('.share-button');
  const copyButton = parentElement.querySelector('.copy-button');
  const closeButton = parentElement.querySelector('.close-button');
  const dialog = parentElement.querySelector('.share-dialog');
  const preview = parentElement.querySelector('.share-preview');
  const status = parentElement.querySelector('.share-status');
  const payload = sharePayload(data, window.location.href);
  const links = messagingLinks(payload, navigator);
  const nativeShare = typeof navigator.share === 'function';
  button.querySelector('span').textContent = nativeShare ? data.share_label : data.messages_label;
  const messages = parentElement.querySelector('.messages-link');
  messages.href = links.messages;
  messages.textContent = data.sms_label;
  parentElement.querySelector('.whatsapp-link').href = links.whatsapp;
  copyButton.textContent = data.copy_label;
  closeButton.textContent = data.close_label;
  dialog.setAttribute('aria-label', data.preview_label);
  parentElement.querySelector('.preview-label').textContent = data.preview_label;
  parentElement.querySelector('.manual-hint').textContent = data.manual;
  preview.setAttribute('aria-label', data.preview_label);
  preview.value = messageText(payload);

  // Grow to the actual message height rather than adding an inner scrollbar.
  let lastWidth = -1;
  const fitPreview = () => {
    const width = preview.getBoundingClientRect().width;
    if (width === lastWidth) return;
    lastWidth = width;
    preview.style.height = 'auto';
    preview.style.height = `${preview.scrollHeight + 2}px`;
  };
  const observer = new ResizeObserver(fitPreview);
  observer.observe(preview);
  fitPreview();

  button.onclick = async () => {
    status.textContent = '';
    if (!nativeShare) {
      window.location.assign(links.messages);
      return;
    }
    button.disabled = true;
    try {
      const result = await shareInApp(payload, navigator);
      if (disposed) return;
      if (result === 'messaging') status.textContent = data.choose_app;
    } finally {
      button.disabled = false;
    }
  };
  copyButton.onclick = async () => {
    status.textContent = '';
    const result = await copyMessage(payload, navigator);
    if (disposed) return;
    if (result === 'copied') status.textContent = data.copied;
    if (result === 'manual') {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
      lastWidth = -1;
      fitPreview();
      preview.focus({ preventScroll:true });
      preview.select();
    }
  };
  closeButton.onclick = () => {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  };
  return () => {
    disposed = true;
    button.onclick = null;
    copyButton.onclick = null;
    closeButton.onclick = null;
    observer.disconnect();
  };
}
