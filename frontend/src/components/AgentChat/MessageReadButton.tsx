import { Button, Tooltip, message } from 'antd';
import { ReadOutlined } from '@ant-design/icons';
import { t, type InterfaceLanguage } from '../../i18n';
import { openChatReader } from '../../utils/chatReaderHtml';

export function MessageReadButton({ content, language, messageId }: {
  content: string;
  language: InterfaceLanguage;
  messageId: string;
}) {
  const label = t(language, 'readMessage');
  return (
    <Tooltip title={label}>
      <Button type="text" size="small" className="agent-message-read"
        aria-label={label} icon={<ReadOutlined />}
        onClick={() => {
          try {
            if (!openChatReader(content, language, messageId)) {
              message.warning(t(language, 'readerPopupBlocked'));
            }
          } catch {
            message.error(t(language, 'readerOpenFailed'));
          }
        }}
      />
    </Tooltip>
  );
}
