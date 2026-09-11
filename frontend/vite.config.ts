import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // Ant Design is a shared UI runtime; keep its vendor chunk explicit while
    // avoiding a warning for the intentionally shared bundle.
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('@antv/x6-plugin')) return 'vendor-x6-plugins';
          if (id.includes('@antv/x6')) return 'vendor-x6-core';
          if (id.includes('monaco-editor') || id.includes('@monaco-editor')) return 'vendor-monaco';
          if (id.includes('antd') || id.includes('@ant-design') || id.includes('/rc-')) return 'vendor-antd';
          if (id.includes('xlsx')) return 'vendor-xlsx';
          if (id.includes('react') || id.includes('zustand')) return 'vendor-react';
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        ws: true, // allow WebSocket upgrade on /api paths too
      },
    },
  },
});
