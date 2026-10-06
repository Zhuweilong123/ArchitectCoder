import { useEffect, useState, type ReactNode } from 'react';
import { Button, Tooltip, message } from 'antd';
import { CheckOutlined, CopyOutlined } from '@ant-design/icons';
import { t, type InterfaceLanguage } from '../../i18n';

async function copyText(text: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // HTTP deployments or browser permissions may require the legacy path.
  }
  const focused = document.activeElement as HTMLElement | null;
  const selection = window.getSelection();
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange())
    : [];
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.cssText = 'position:fixed;left:-9999px;top:0;';
  document.body.appendChild(textarea);
  try {
    textarea.select();
    if (!document.execCommand('copy')) throw new Error('Copy failed');
  } finally {
    textarea.remove();
    focused?.focus({ preventScroll: true });
    if (selection) {
      selection.removeAllRanges();
      ranges.forEach((range) => selection.addRange(range));
    }
  }
}

export function MessageCopyButton({ content, language, children }: {
  content: string;
  language: InterfaceLanguage;
  children?: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const [copying, setCopying] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const label = t(language, copied ? 'messageCopied' : 'copyMessage');

  return (
    <div className="agent-message-actions">
      <Tooltip title={label}>
        <Button
          className="agent-message-copy"
          type="text"
          size="small"
          aria-label={label}
          icon={copied ? <CheckOutlined /> : <CopyOutlined />}
          disabled={copying || !content.length}
          onClick={async () => {
            setCopying(true);
            setCopied(false);
            try {
              await copyText(content);
              setCopied(true);
            } catch {
              message.error(t(language, 'messageCopyFailed'));
            } finally {
              setCopying(false);
            }
          }}
        >
          {copied ? label : null}
        </Button>
      </Tooltip>
      {children}
    </div>
  );
}
