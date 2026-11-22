import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import 'leaflet/dist/leaflet.css';
import './index.css';
import App from './App';
import { LabelCacheProvider } from './lib/labelCache';

async function enableMocks() {
  if (import.meta.env.VITE_MOCK !== '1') return;
  const { worker } = await import('./mocks/browser');
  await worker.start({ onUnhandledRequest: 'bypass', quiet: true });
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 } },
});

void enableMocks().then(() => {
  createRoot(document.getElementById('root') as HTMLElement).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <LabelCacheProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </LabelCacheProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
});
