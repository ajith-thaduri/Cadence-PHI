import { createBrowserRouter, RouterProvider } from 'react-router';
import { Layout } from './components/Layout';
import { SessionGate } from './components/SessionGate';
import { ActivityPage } from './pages/Activity';
import { BatchPage } from './pages/Batch';
import { ChecksPage } from './pages/Checks';
import { ConfigPage } from './pages/Config';
import { DocsPage } from './pages/Docs';
import { GatesPage } from './pages/Gates';
import { MonitoringPage } from './pages/Monitoring';
import { NotFoundPage } from './pages/NotFound';
import { OverviewPage } from './pages/Overview';
import { RedactPage } from './pages/Redact';
import { ResultsPage, RunDetailPage } from './pages/Results';

const router = createBrowserRouter([
  {
    path: '/',
    element: (
      <SessionGate>
        <Layout />
      </SessionGate>
    ),
    children: [
      { index: true, element: <OverviewPage /> },
      { path: 'redact', element: <RedactPage /> },
      { path: 'batch', element: <BatchPage /> },
      { path: 'checks', element: <ChecksPage /> },
      { path: 'results', element: <ResultsPage /> },
      { path: 'results/:kind/:name', element: <RunDetailPage /> },
      { path: 'gates', element: <GatesPage /> },
      { path: 'monitoring', element: <MonitoringPage /> },
      { path: 'config', element: <ConfigPage /> },
      { path: 'activity', element: <ActivityPage /> },
      { path: 'docs', element: <DocsPage /> },
      { path: 'docs/:slug', element: <DocsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
