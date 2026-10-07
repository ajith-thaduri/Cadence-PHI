import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconHeartbeat, IconPlayerPlay } from '@tabler/icons-react';
import { useMonitoring, useRunCanaries, useStatus } from '../api/hooks';
import { ErrorAlert, PageHeader, StatCard } from '../components/common';
import { num, shortSha, when } from '../lib/format';
import { usePrefs } from '../lib/prefs';

export function MonitoringPage() {
  const { data: m, error } = useMonitoring();
  const { data: status } = useStatus();
  const canaries = useRunCanaries();
  const { prefs } = usePrefs();
  if (error) return <ErrorAlert error={error} />;
  if (!m) return <Loader />;
  const c = m.counters;
  const rows = m.baseline ? Object.entries(m.baseline.rates_per_1k_chars) : [];
  return (
    <>
      <PageHeader
        title="Monitoring"
        description="Canaries push known fake identifiers through the full engine to prove detection still works; detection-rate checks compare live traffic with the expected baseline. No text is recorded - only counts."
        actions={
          <Button
            leftSection={<IconPlayerPlay size={16} />}
            onClick={() => canaries.mutate()}
            loading={canaries.isPending}
            disabled={!status?.permissions.analyze}
          >
            Run canaries now
          </Button>
        }
      />
      <ErrorAlert error={canaries.error} />
      {canaries.data && (
        <Alert
          color={canaries.data.ok ? 'green' : 'red'}
          icon={<IconHeartbeat size={18} />}
          mb="md"
          title={canaries.data.ok ? 'All canaries passed' : 'Canary failure - detection may be broken'}
        >
          {canaries.data.passed}/{canaries.data.cases} passed
          {canaries.data.failed.length ? ` · failed: ${canaries.data.failed.join(', ')}` : ''}
        </Alert>
      )}
      <SimpleGrid cols={{ base: 2, md: 4 }} mb="lg">
        <StatCard label="Requests (this process)" value={num(c.requests_ok_total ?? 0)} />
        <StatCard
          label="Blocked / failed closed"
          value={num(
            Object.entries(c)
              .filter(([k]) => k.startsWith('requests_blocked_total'))
              .reduce((s, [, v]) => s + v, 0),
          )}
        />
        <StatCard
          label="Outbound guard blocks"
          value={num(c.outbound_guard_block ?? 0)}
          color={(c.outbound_guard_block ?? 0) > 0 ? 'orange' : undefined}
        />
        <StatCard
          label="Canary failures"
          value={num(c.canary_fail ?? 0)}
          color={(c.canary_fail ?? 0) > 0 ? 'red' : 'green'}
        />
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder radius="md">
          <Title order={5} mb="xs">
            Rate alerts
          </Title>
          {m.alerts.length === 0 ? (
            <Text size="sm" c="dimmed">
              No alerts.{' '}
              {m.baseline
                ? 'Live detection rates are within the control limits (or there is not yet enough traffic to judge).'
                : 'No baseline - run “Monitoring baseline” under Checks.'}
            </Text>
          ) : (
            <Table fz="sm">
              <Table.Tbody>
                {m.alerts.map((a, i) => (
                  <Table.Tr key={i}>
                    <Table.Td>{a.entity}</Table.Td>
                    <Table.Td>{a.modality}</Table.Td>
                    <Table.Td>
                      <Badge color={a.direction === 'drop' ? 'red' : 'orange'}>{a.direction}</Badge>
                    </Table.Td>
                    <Table.Td>
                      observed {a.observed}, expected {a.expected} (z {a.z})
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          <Title order={5} mt="lg" mb="xs">
            Configured canaries
          </Title>
          <Stack gap={4}>
            {m.canaries.map((k) => (
              <Group key={k.id} gap={6}>
                <Code>{k.id}</Code>
                {k.negative ? (
                  <Badge size="xs" color="gray">
                    must not redact
                  </Badge>
                ) : (
                  k.entities.map((e) => (
                    <Badge key={e} size="xs" variant="light">
                      {e}
                    </Badge>
                  ))
                )}
              </Group>
            ))}
          </Stack>
        </Card>
        <Card withBorder radius="md">
          <Title order={5} mb="xs">
            Expected detection rates (per 1,000 characters)
          </Title>
          {m.baseline ? (
            <>
              <Text size="xs" c="dimmed" mb="xs">
                Baseline {when(m.baseline.created)} · {m.baseline.datasets.join(', ')} · config{' '}
                {shortSha(m.baseline.config_sha256)}
              </Text>
              <ScrollArea h={360}>
                <Table fz="xs" striped stickyHeader>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Entity</Table.Th>
                      {rows.map(([mod]) => (
                        <Table.Th key={mod}>{mod}</Table.Th>
                      ))}
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {[...new Set(rows.flatMap(([, r]) => Object.keys(r)))].sort().map((ent) => (
                      <Table.Tr key={ent}>
                        <Table.Td>{ent}</Table.Td>
                        {rows.map(([mod, r]) => (
                          <Table.Td key={mod}>{r[ent] !== undefined ? r[ent].toFixed(2) : '-'}</Table.Td>
                        ))}
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
            </>
          ) : (
            <Text size="sm" c="dimmed">
              No baseline yet.
            </Text>
          )}
        </Card>
      </SimpleGrid>
      {prefs.technical && (
        <Card withBorder radius="md" mt="lg">
          <Title order={6} mb="xs">
            Raw counters
          </Title>
          <ScrollArea h={300}>
            <Code block>
              {Object.entries(c)
                .sort()
                .map(([k, v]) => `${k} ${v}`)
                .join('\n')}
            </Code>
          </ScrollArea>
        </Card>
      )}
    </>
  );
}
