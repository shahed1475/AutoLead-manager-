import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider, QueryCache, MutationCache } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import App from './App'
import './index.css'

// Queries handle their own error UI (ErrorState + retry) per-page; mutations
// mostly already toast their own onError. These caches are a safety net so
// a query/mutation failure is never fully silent (logged) even if a page
// forgets to wire isError, without double-toasting mutations that already do.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      console.error(`AutoLead: query [${query.queryKey.join(', ')}] failed —`, error)
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      if (!mutation.options.onError) {
        console.error('AutoLead: mutation failed —', error)
      }
    },
  }),
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster
        position="top-right"
        toastOptions={{
          style: {
            background: 'rgb(var(--surface-elevated))',
            color: 'rgb(var(--foreground))',
            border: '1px solid rgb(var(--border))',
            boxShadow: 'var(--shadow-lg)',
            borderRadius: '12px',
            fontSize: '13px',
          },
          success: { iconTheme: { primary: 'rgb(var(--success))', secondary: 'white' } },
          error: { iconTheme: { primary: 'rgb(var(--error))', secondary: 'white' } },
        }}
      />
    </QueryClientProvider>
  </React.StrictMode>
)
