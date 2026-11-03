import {
  ActionIcon,
  AppShell,
  Burger,
  Group,
  NavLink,
  Stack,
  Text,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconCloudUpload,
  IconFileSpreadsheet,
  IconLayoutDashboard,
  IconListCheck,
  IconMoon,
  IconSettings,
  IconSun,
  type Icon,
} from '@tabler/icons-react';
import { NavLink as RouterNavLink, Outlet, useLocation } from 'react-router';

import { routes } from '@/lib/routes';

import { RouteProgress } from './RouteProgress';

interface NavItem {
  label: string;
  description: string;
  to: string;
  icon: Icon;
  /** Match child routes too (Admin tabs). */
  prefix?: boolean;
}

const NAV: NavItem[] = [
  { label: 'Dashboard', description: 'Overview', to: routes.dashboard, icon: IconLayoutDashboard },
  {
    label: 'Upload',
    description: 'Process a supplier file',
    to: routes.upload,
    icon: IconCloudUpload,
  },
  {
    label: 'Review',
    description: 'Resolve unmatched customers',
    to: routes.review,
    icon: IconListCheck,
  },
  {
    label: 'Export',
    description: 'Preview and download output',
    to: routes.export,
    icon: IconFileSpreadsheet,
  },
  {
    label: 'Admin',
    description: 'Suppliers, members, rules',
    to: routes.admin.root,
    icon: IconSettings,
    prefix: true,
  },
];

function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme('light');
  const next = computed === 'dark' ? 'light' : 'dark';
  return (
    <Tooltip label={`Switch to ${next} mode`}>
      <ActionIcon
        variant="subtle"
        color="slate"
        size="lg"
        aria-label={`Switch to ${next} mode`}
        onClick={() => {
          setColorScheme(next);
        }}
      >
        {computed === 'dark' ? <IconSun size={18} /> : <IconMoon size={18} />}
      </ActionIcon>
    </Tooltip>
  );
}

export function AppLayout() {
  const [navOpen, { toggle, close }] = useDisclosure();
  const { pathname } = useLocation();

  return (
    <AppShell
      header={{ height: 52 }}
      navbar={{ width: 232, breakpoint: 'sm', collapsed: { mobile: !navOpen } }}
      padding="lg"
    >
      <AppShell.Header>
        <RouteProgress />
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger
              opened={navOpen}
              onClick={toggle}
              hiddenFrom="sm"
              size="sm"
              aria-label="Toggle navigation"
            />
            <Text fw={650} size="md">
              Alliant Revenue Processing
            </Text>
          </Group>
          <ColorSchemeToggle />
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm" aria-label="Main navigation">
        <Stack gap={2}>
          {NAV.map((item) => {
            const active = item.prefix ? pathname.startsWith(item.to) : pathname === item.to;
            return (
              <NavLink
                key={item.to}
                component={RouterNavLink}
                to={item.to}
                label={item.label}
                description={item.description}
                leftSection={<item.icon size={18} stroke={1.6} />}
                active={active}
                onClick={close}
              />
            );
          })}
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
