import { Navigate, type RouteObject } from 'react-router-dom';

import { NotFoundPage, RootErrorBoundary, RouteErrorBoundary } from '@/components/ErrorViews';
import { AppLayout } from '@/components/layout/AppLayout';
import { routes } from '@/lib/routes';
import { AdminLayout } from '@/pages/admin/AdminLayout';
import { AliasesTab } from '@/pages/admin/AliasesTab';
import { LedgerTab } from '@/pages/admin/LedgerTab';
import { MembersTab } from '@/pages/admin/MembersTab';
import { SuppliersTab } from '@/pages/admin/SuppliersTab';
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
              { path: routes.admin.members, element: <MembersTab /> },
              { path: routes.admin.ledger, element: <LedgerTab /> },
              { path: routes.admin.suppliers, element: <SuppliersTab /> },
              { path: routes.admin.columnMaps, element: <AdminTabStub feature="Column maps" /> },
              { path: routes.admin.tierC, element: <AdminTabStub feature="Tier C rules" /> },
              { path: routes.admin.aliases, element: <AliasesTab /> },
            ],
          },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
