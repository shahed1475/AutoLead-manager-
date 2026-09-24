import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import PortalApp from './PortalApp'
import '../index.css'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <PortalApp />
      <Toaster
        position="top-center"
        toastOptions={{
          style: {
            background: 'rgb(var(--surface-elevated))', color: 'rgb(var(--foreground))',
            border: '1px solid rgb(var(--border))', boxShadow: 'var(--shadow-lg)', borderRadius: '12px', fontSize: '14px',
          },
        }}
      />
    </QueryClientProvider>
  </React.StrictMode>,
)
