import { Tabs } from '@mantine/core';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { PageHeader } from '@/components/PageHeader';
import { routes } from '@/lib/routes';

/** The legacy Admin page's six st.tabs, as addressable sub-routes. */
const ADMIN_TABS = [
  { label: 'Members', to: routes.admin.members },
  { label: 'Payment Ledger', to: routes.admin.ledger },
  { label: 'Suppliers', to: routes.admin.suppliers },
  { label: 'Column Maps', to: routes.admin.columnMaps },
  { label: 'Tier C Rules', to: routes.admin.tierC },
  { label: 'Aliases', to: routes.admin.aliases },
] as const;

export function AdminLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const active = ADMIN_TABS.find((t) => pathname.startsWith(t.to))?.to ?? null;

  return (
    <>
      <PageHeader
        title="Admin"
        description="Suppliers, members, ledger, column maps, Tier C rules, and aliases."
      />
      <Tabs
        value={active}
        onChange={(to) => {
          if (to) navigate(to);
        }}
        mb="lg"
        keepMounted={false}
      >
        {/* One row that scrolls sideways on narrow screens, rather than wrapping. */}
        <Tabs.List
          style={{
            flexWrap: 'nowrap',
            overflowX: 'auto',
            overflowY: 'hidden',
            scrollbarWidth: 'thin',
          }}
        >
          {ADMIN_TABS.map((t) => (
            <Tabs.Tab key={t.to} value={t.to}>
              {t.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      <Outlet />
    </>
  );
}
