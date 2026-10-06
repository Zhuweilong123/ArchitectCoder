import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { escapeHtml } from './safeHtml';
import { t, type InterfaceLanguage } from '../i18n';

/** Limit model-generated markup to document formatting, without active content. */
export function renderChatMarkdown(content: string): string {
  const rendered = marked.parse(content, { gfm: true, async: false });
  const fragment = DOMPurify.sanitize(rendered, {
    ALLOWED_TAGS: ['p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'strong', 'em', 'del', 'blockquote', 'pre', 'code', 'ul', 'ol', 'li',
      'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'a', 'input'],
    ALLOWED_ATTR: ['href', 'title', 'class', 'start', 'align', 'type', 'checked', 'disabled'],
    ALLOW_DATA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  });
  fragment.querySelectorAll('a').forEach((link) => {
    const href = link.getAttribute('href') || '';
    if (!/^(https?:\/\/|mailto:)/i.test(href)) {
      link.removeAttribute('href');
    } else {
      link.setAttribute('target', '_blank');
      link.setAttribute('rel', 'noopener noreferrer');
    }
  });
  fragment.querySelectorAll('input').forEach((input) => {
    if (input.getAttribute('type') !== 'checkbox') input.remove();
    else input.setAttribute('disabled', '');
  });
  const container = document.createElement('div');
  container.appendChild(fragment);
  return container.innerHTML;
}

/** Standalone reader: the raw source is data, never part of executable JS. */
export function createChatReaderHtml(content: string, language: InterfaceLanguage, messageId: string): string {
  const title = t(language, 'readMessage');
  const nonce = Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (byte) => byte.toString(16).padStart(2, '0')).join('');
  const data = JSON.stringify({
    content,
    filename: `ArchitectCoder-${messageId.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 80) || 'reply'}.md`,
    copied: t(language, 'messageCopied'),
    copyFailed: t(language, 'messageCopyFailed'),
  }).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
  const body = renderChatMarkdown(content);
  return `<!doctype html>
<html lang="${language === 'zh' ? 'zh-CN' : 'en'}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>${escapeHtml(title)} · ArchitectCoder</title>
<style>
*{box-sizing:border-box}body{margin:0;color:#243248;background:#f5f6f8;font:16px/1.8 system-ui,-apple-system,'Segoe UI',sans-serif}
header{position:sticky;top:0;z-index:1;background:#fff;border-bottom:1px solid #e2e8f0;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.brand{font-size:13px;color:#64748b}.brand strong{color:#243248;margin-right:12px}nav{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{font:inherit;font-size:13px;cursor:pointer;padding:6px 12px;border:1px solid #d5deeb;border-radius:7px;background:#fff;color:#243248}button:hover{background:#eff6ff;border-color:#93c5fd}button:focus-visible,a:focus-visible{outline:2px solid #2563eb;outline-offset:3px}button:disabled{cursor:wait;opacity:.6}#status{font-size:12px;color:#64748b}
main{max-width:960px;margin:32px auto;padding:36px 48px;background:#fff;border:1px solid #e2e8f0;border-radius:12px;min-height:60vh;overflow-wrap:anywhere}main>:first-child{margin-top:0}h1,h2,h3,h4,h5,h6{line-height:1.4;color:#182538;margin:1.5em 0 .65em}h1{font-size:2em}h2{font-size:1.5em;border-bottom:1px solid #e2e8f0;padding-bottom:.35em}h3{font-size:1.2em}p,ul,ol,blockquote{margin:1em 0}li>p{margin:.4em 0}a{color:#2563eb}blockquote{border-left:4px solid #cbd5e1;padding:4px 18px;margin-left:0;color:#64748b;background:#f8fafc}hr{border:0;border-top:1px solid #e2e8f0;margin:2em 0}
code{font: .9em/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;background:#f1f5f9;padding:2px 5px;border-radius:4px}pre{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;overflow:auto;white-space:pre;overflow-wrap:normal}pre code{background:transparent;padding:0}table{display:block;overflow-x:auto;max-width:100%;border-collapse:collapse;margin:1.5em 0;font-size:.95em}th,td{border:1px solid #dbe3ed;padding:9px 14px;min-width:100px}th{background:#f1f5f9;text-align:left}tbody tr:nth-child(even){background:#fafbfd}input[type=checkbox]{margin-right:6px}
@media(max-width:640px){header{padding:12px 16px}main{margin:16px 8px;padding:24px 20px}h1{font-size:1.6em}}
@media print{header{display:none}body{background:white}main{border:0;margin:0;padding:0;max-width:none}pre,blockquote,tr{break-inside:avoid}}
</style></head>
<body><header><div class="brand"><strong>ArchitectCoder</strong>${escapeHtml(title)}</div><nav aria-label="${escapeHtml(title)}"><span id="status" role="status" aria-live="polite"></span><button id="copy" type="button">${escapeHtml(t(language, 'copyMarkdownSource'))}</button><button id="download" type="button">${escapeHtml(t(language, 'downloadMarkdown'))}</button></nav></header>
<main>${body}</main>
<script type="application/json" id="source" nonce="${nonce}">${data}</script>
<script nonce="${nonce}">
(() => {
  const data = JSON.parse(document.getElementById('source').textContent);
  const copy = document.getElementById('copy');
  const status = document.getElementById('status');
  let timer;
  const notify = (text) => {
    clearTimeout(timer);
    status.textContent = text;
    timer = setTimeout(() => { status.textContent = ''; }, 2500);
  };
  const legacyCopy = () => {
    const textarea = document.createElement('textarea');
    textarea.value = data.content;
    textarea.setAttribute('readonly', '');
    textarea.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.appendChild(textarea);
    try {
      textarea.select();
      if (!document.execCommand('copy')) throw new Error('Copy failed');
    } finally { textarea.remove(); copy.focus({ preventScroll: true }); }
  };
  copy.addEventListener('click', async () => {
    copy.disabled = true;
    try {
      try {
        if (!navigator.clipboard?.writeText) throw new Error('No clipboard API');
        await navigator.clipboard.writeText(data.content);
      } catch { legacyCopy(); }
      notify(data.copied);
    } catch { notify(data.copyFailed); }
    finally { copy.disabled = false; }
  });
  document.getElementById('download').addEventListener('click', () => {
    const url = URL.createObjectURL(new Blob([data.content], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = data.filename;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
</script></body></html>`;
}

export function openChatReader(content: string, language: InterfaceLanguage, messageId: string): boolean {
  const html = createChatReaderHtml(content, language, messageId);
  // Open synchronously inside the click gesture so popup blocking is detectable.
  const reader = window.open('about:blank', '_blank');
  if (!reader) return false;
  reader.opener = null;
  const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
  try {
    reader.location.replace(url);
    // Keep the URL alive for reloads, then release it when the parent closes.
    window.addEventListener('pagehide', () => URL.revokeObjectURL(url), { once: true });
    return true;
  } catch (error) {
    reader.close();
    URL.revokeObjectURL(url);
    throw error;
  }
}
