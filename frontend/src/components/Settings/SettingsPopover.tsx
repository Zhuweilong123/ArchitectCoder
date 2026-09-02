import React from 'react';
import { Button, Divider, Popover, Segmented, Space, Typography } from 'antd';
import { GlobalOutlined, SettingOutlined } from '@ant-design/icons';
import { useUiStore } from '../../stores/uiStore';
import { t } from '../../i18n';

const SettingsPopover: React.FC = () => {
  const interfaceLanguage = useUiStore((state) => state.interfaceLanguage);
  const setInterfaceLanguage = useUiStore((state) => state.setInterfaceLanguage);

  const content = (
    <div className="settings-popover">
      <Typography.Text strong>{t(interfaceLanguage, 'language')}</Typography.Text>
      <Segmented
        block
        value={interfaceLanguage}
        options={[
          { label: t(interfaceLanguage, 'english'), value: 'en' },
          { label: t(interfaceLanguage, 'chinese'), value: 'zh' },
        ]}
        onChange={(value) => setInterfaceLanguage(value as 'en' | 'zh')}
      />
      <Divider />
      <Typography.Text type="secondary">
        {interfaceLanguage === 'en'
          ? 'Language preference is saved locally.'
          : '语言偏好会保存在当前浏览器中。'}
      </Typography.Text>
    </div>
  );

  return (
    <Popover content={content} title={t(interfaceLanguage, 'settings')} trigger="click" placement="bottomRight">
      <Button
        className="settings-button"
        icon={<SettingOutlined />}
        aria-label={t(interfaceLanguage, 'settings')}
      >
        <Space size={6}>
          <GlobalOutlined />
          <span>{interfaceLanguage === 'en' ? 'EN' : '中'}</span>
        </Space>
      </Button>
    </Popover>
  );
};

export default SettingsPopover;