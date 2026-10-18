import {
  Alert,
  Anchor,
  Button,
  Card,
  List,
  SimpleGrid,
  Skeleton,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { PageHeader } from '@/components/PageHeader';
import { routes } from '@/lib/routes';
import { dashboardQuery } from '@/queries/dashboard';

const number = new Intl.NumberFormat('en-US');

function Stat({ label, value, href }: { label: string; value: number | undefined; href?: string }) {
  return (
    <Card padding="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      {value === undefined ? (
        <Skeleton height={30} width="50%" mt={6} />
      ) : (
        <Text fz={28} fw={650} mt={4} className="tabular">
          {href && value > 0 ? (
            <Anchor component={Link} to={href} inherit>
              {number.format(value)}
            </Anchor>
          ) : (
            number.format(value)
          )}
        </Text>
      )}
    </Card>
  );
}

export function DashboardPage() {
  const { data, error, refetch } = useQuery(dashboardQuery);

  return (
    <>
      <PageHeader
        title="Revenue Processing"
        description="Reconcile supplier remittance files against the Alliant member database."
      />

      {error ? (
        <Alert color="red" icon={<IconAlertTriangle />} title="Couldn't load the overview" mb="lg">
          <Text size="sm" mb="xs">
            {error.message}
          </Text>
          <Button size="xs" variant="light" color="red" onClick={() => void refetch()}>
            Try again
          </Button>
        </Alert>
      ) : (
        <SimpleGrid cols={{ base: 2, md: 4 }} mb="xl">
          <Stat label="Members loaded" value={data?.active_members} />
          <Stat label="Suppliers configured" value={data?.suppliers} />
          <Stat label="Jobs processed" value={data?.jobs} />
          <Stat label="Pending reviews" value={data?.pending_reviews} href={routes.review} />
        </SimpleGrid>
      )}

      <Card maw={720}>
        <Title order={2} size="h3" mb="sm">
          Getting started
        </Title>
        <List type="ordered" spacing="xs" size="sm">
          <List.Item>
            First time? Load the Account List in{' '}
            <Anchor component={Link} to={routes.admin.members}>
              Admin › Members
            </Anchor>
            .
          </List.Item>
          <List.Item>
            Import the payment ledger in{' '}
            <Anchor component={Link} to={routes.admin.ledger}>
              Admin › Payment Ledger
            </Anchor>
            .
          </List.Item>
          <List.Item>
            Check supplier profiles in{' '}
            <Anchor component={Link} to={routes.admin.suppliers}>
              Admin › Suppliers
            </Anchor>
            .
          </List.Item>
          <List.Item>
            Then{' '}
            <Anchor component={Link} to={routes.upload}>
              upload
            </Anchor>{' '}
            a supplier file to process it.
          </List.Item>
        </List>
      </Card>
    </>
  );
}
