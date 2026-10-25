import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
      // 실시간 갱신용 WebSocket — useWebSocket.ts 는 같은 호스트의 /ws 에 붙는다
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    }
  }
})
