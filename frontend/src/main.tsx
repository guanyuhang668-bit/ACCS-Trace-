import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#167f79',
          colorInfo: '#167f79',
          colorSuccess: '#2f7d4a',
          colorWarning: '#b06d0a',
          colorError: '#b33b3b',
          colorText: '#243038',
          colorTextSecondary: '#65717a',
          colorBgLayout: '#f4f6f5',
          colorBorderSecondary: '#e2e7e4',
          borderRadius: 6,
          borderRadiusLG: 8,
          fontFamily: "Inter, 'Segoe UI', Arial, sans-serif",
        },
        components: {
          Layout: { headerBg: '#ffffff', siderBg: '#ffffff' },
          Table: { headerBg: '#f7f9f8', rowHoverBg: '#f0f7f6' },
          Menu: { itemBorderRadius: 5, itemSelectedBg: '#e9f4f2', itemSelectedColor: '#126d68' },
        },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter><App /></BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
