import type { ComponentType } from 'react';
import { Navigate, type RouteObject } from 'react-router-dom';

import { NotFoundPage, RootErrorBoundary, RouteErrorBoundary } from '@/components/ErrorViews';
import { AppLayout } from '@/components/layout/AppLayout';
import { routes } from '@/lib/routes';
import { AdminLayout } from '@/pages/admin/AdminLayout';
import { DashboardPage } from '@/pages/DashboardPage';

/** Opt in to React Router v7 behaviour now, so the v7 upgrade changes nothing. */
export const routerFuture = {
  v7_relativeSplatPath: true,
  v7_fetcherPersist: true,
  v7_normalizeFormMethod: true,
  v7_partialHydration: true,
  v7_skipActionErrorRevalidation: true,
} as const;
export const providerFuture = { v7_startTransition: true } as const;

/**
 * Route-level code splitting: each page (and the table/upload libraries only
 * pages use) loads on first visit. The dashboard and the shell stay in the
 * main bundle so the landing page needs one request.
 */
function page<K extends string, M extends Record<K, ComponentType>>(
  load: () => Promise<M>,
  name: K,
) {
  return async () => ({ Component: (await load())[name] });
}

export const routeTree: RouteObject[] = [
  {
    element: <AppLayout />,
    errorElement: <RootErrorBoundary />,
    children: [
      {
        // Pathless layout route: page errors render inside the shell.
        errorElement: <RouteErrorBoundary />,
        children: [
          { path: routes.dashboard, element: <DashboardPage /> },
          {
            path: routes.upload,
            lazy: page(() => import('@/pages/upload/UploadPage'), 'UploadPage'),
          },
          {
            path: routes.review,
            lazy: page(() => import('@/pages/review/ReviewPage'), 'ReviewPage'),
          },
          {
            path: routes.export,
            lazy: page(() => import('@/pages/export/ExportPage'), 'ExportPage'),
          },
          {
            path: routes.admin.root,
            element: <AdminLayout />,
            children: [
              { index: true, element: <Navigate to={routes.admin.members} replace /> },
              {
                path: routes.admin.members,
                lazy: page(() => import('@/pages/admin/MembersTab'), 'MembersTab'),
              },
              {
                path: routes.admin.ledger,
                lazy: page(() => import('@/pages/admin/LedgerTab'), 'LedgerTab'),
              },
              {
                path: routes.admin.suppliers,
                lazy: page(() => import('@/pages/admin/SuppliersTab'), 'SuppliersTab'),
              },
              {
                path: routes.admin.columnMaps,
                lazy: page(() => import('@/pages/admin/ColumnMapsTab'), 'ColumnMapsTab'),
              },
              {
                path: routes.admin.tierC,
                lazy: page(() => import('@/pages/admin/tier-c/TierCTab'), 'TierCTab'),
              },
              {
                path: routes.admin.aliases,
                lazy: page(() => import('@/pages/admin/AliasesTab'), 'AliasesTab'),
              },
            ],
          },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
