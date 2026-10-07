import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  List,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconCircleCheck,
  IconCircleX,
  IconHelpCircle,
  IconPlayerPlay,
  IconRocket,
} from '@tabler/icons-react';
import { useGates, useStatus, useSubmitJob } from '../api/hooks';
import { EmptyState, ErrorAlert, PageHeader, VerdictBadge } from '../components/common';
import { JobDrawer } from '../components/JobDrawer';
import { when } from '../lib/format';

const ICON = { PASS: IconCircleCheck, FAIL: IconCircleX, INSUFFICIENT_EVIDENCE: IconHelpCircle };
const COLOR = { PASS: 'green', FAIL: 'red', INSUFFICIENT_EVIDENCE: 'yellow' };

export function GatesPage() {
  const { data, error, isLoading } = useGates();
  const { data: status } = useStatus();
  const submit = useSubmitJob();
  const [drawer, setDrawer] = useState<string | null>(null);
  const run = () =>
    submit.mutate(
      { type: 'gates', params: { with_model: true } },
      {
        onSuccess: (j) => {
          notifications.show({ message: 'Release gates started' });
          setDrawer(j.id);
        },
      },
    );
  return (
    <>
      <PageHeader
        title="Release readiness"
        description="Go / no-go across gates G1–G9. The decision is the minimum across gates: any failure or missing evidence means NO-GO."
        actions={
          <Button
            leftSection={<IconPlayerPlay size={16} />}
            onClick={run}
            loading={submit.isPending}
            disabled={!status?.permissions.run_jobs}
          >
            Run release gates
          </Button>
        }
      />
      <ErrorAlert error={error || submit.error} />
      {isLoading ? (
        <Loader />
      ) : !data ? (
        <Card withBorder>
          <EmptyState icon={<IconRocket />} title="No gate run yet">
            Run the release gates to produce a go / no-go decision.
          </EmptyState>
        </Card>
      ) : (
        <Stack>
          <Alert
            color={data.summary.decision === 'GO' ? 'green' : 'red'}
            icon={<IconRocket size={22} />}
            title={`Decision: ${data.summary.decision}`}
          >
            Run {data.name} · {when(data.summary.created)} · gate thresholds status{' '}
            <Badge size="xs">{data.gates_status}</Badge>
            {data.gates_status === 'proposed' && ' - numbers await sign-off by the risk owner.'}
          </Alert>
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
            {Object.entries(data.summary.gates).map(([k, g]) => {
              const def = data.definitions[k];
              const Icon = ICON[g.verdict] ?? IconHelpCircle;
              return (
                <Card
                  key={k}
                  withBorder
                  radius="md"
                  padding="lg"
                  style={{ borderTop: `4px solid var(--mantine-color-${COLOR[g.verdict] ?? 'gray'}-filled)` }}
                >
                  <Group justify="space-between" mb={4}>
                    <Group gap={6}>
                      <Icon size={20} color={`var(--mantine-color-${COLOR[g.verdict] ?? 'gray'}-filled)`} />
                      <Title order={5}>
                        {k} · {def?.name}
                      </Title>
                    </Group>
                    <VerdictBadge verdict={g.verdict} size="sm" />
                  </Group>
                  <Text size="xs" c="dimmed" mb="xs">
                    {def?.metric} · evidence {def?.evidence?.join(', ')}
                  </Text>
                  <List size="sm" spacing={2}>
                    {g.reasons.slice(0, 5).map((r, i) => (
                      <List.Item key={i}>{r}</List.Item>
                    ))}
                    {g.reasons.length > 5 && <List.Item>… {g.reasons.length - 5} more</List.Item>}
                  </List>
                </Card>
              );
            })}
          </SimpleGrid>
        </Stack>
      )}
      <JobDrawer id={drawer} onClose={() => setDrawer(null)} canCancel={!!status?.permissions.run_jobs} />
    </>
  );
}
