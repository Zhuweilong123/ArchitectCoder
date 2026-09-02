/**
 * Application entry point.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import { useUiStore } from './stores/uiStore';

const Root: React.FC = () => {
  const interfaceLanguage = useUiStore((state) => state.interfaceLanguage);

  return (
    <ConfigProvider
      locale={interfaceLanguage === 'zh' ? zhCN : enUS}
      theme={{
        token: {
          colorPrimary: '#2563eb',
          colorInfo: '#2563eb',
          borderRadius: 8,
          fontSize: 13,
        },
        components: {
          Button: {
            controlHeight: 32,
          },
          Tabs: {
            cardPadding: '6px 10px',
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  );
};

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);