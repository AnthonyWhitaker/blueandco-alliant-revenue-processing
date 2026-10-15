import { Navigate, type RouteObject } from 'react-router-dom';

import { NotFoundPage, RootErrorBoundary, RouteErrorBoundary } from '@/components/ErrorViews';
import { AppLayout } from '@/components/layout/AppLayout';
import { routes } from '@/lib/routes';
import { AdminLayout } from '@/pages/admin/AdminLayout';
import { DashboardPage } from '@/pages/DashboardPage';
import { AdminTabStub, ExportPage, ReviewPage, UploadPage } from '@/pages/stubs';

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
          { path: routes.upload, element: <UploadPage /> },
          { path: routes.review, element: <ReviewPage /> },
          { path: routes.export, element: <ExportPage /> },
          {
            path: routes.admin.root,
            element: <AdminLayout />,
            children: [
              { index: true, element: <Navigate to={routes.admin.members} replace /> },
              { path: routes.admin.members, element: <AdminTabStub feature="Members" /> },
              { path: routes.admin.ledger, element: <AdminTabStub feature="The payment ledger" /> },
              {
                path: routes.admin.suppliers,
                element: <AdminTabStub feature="Supplier profiles" />,
              },
              { path: routes.admin.columnMaps, element: <AdminTabStub feature="Column maps" /> },
              { path: routes.admin.tierC, element: <AdminTabStub feature="Tier C rules" /> },
              { path: routes.admin.aliases, element: <AdminTabStub feature="Aliases" /> },
            ],
          },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
