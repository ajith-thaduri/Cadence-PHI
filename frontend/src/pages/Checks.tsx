import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Modal,
  NumberInput,
  ScrollArea,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconPlayerPlay } from '@tabler/icons-react';
import { useJobTypes, useJobs, useRuns, useStatus, useSubmitJob } from '../api/hooks';
import type { JobType } from '../api/types';
import { ErrorAlert, JobStatusBadge, PageHeader } from '../components/common';
import { JobDrawer } from '../components/JobDrawer';
import { duration, when } from '../lib/format';

const ORDER = [
  'checklist',
  'suite',
  'evaluate',
  'gates',
  'perf',
  'canaries',
  'monitoring_baseline',
  'cards',
  'selftest',
];

export function ChecksPage() {
  const { data } = useJobTypes();
  const { data: jobs } = useJobs();
  const { data: status } = useStatus();
  const [open, setOpen] = useState<JobType | null>(null);
  const [drawer, setDrawer] = useState<string | null>(null);
  const types = (data?.types ?? []).slice().sort((a, b) => ORDER.indexOf(a.type) - ORDER.indexOf(b.type));
  return (
    <>
      <PageHeader
        title="Checks & evaluations"
        description="Run the assurance suite. Every run uses fabricated or development data unless an administrator explicitly selects test data, is recorded in the ledger, and shows the exact command so anyone can reproduce it."
      />
      <SimpleGrid cols={{ base: 1, sm: 2, xl: 3 }} mb="xl">
        {types.map((t) => (
          <Card key={t.type} withBorder radius="md" padding="lg">
            <Stack gap="xs" h="100%" justify="space-between">
              <div>
                <Group justify="space-between">
                  <Text fw={700}>{t.title}</Text>
                  <Badge variant="light" color="gray">
                    {t.estimate}
                  </Badge>
                </Group>
                <Text size="sm" c="dimmed" mt={4}>
                  {t.description}
                </Text>
              </div>
              <Button
                leftSection={<IconPlayerPlay size={16} />}
                variant="light"
                disabled={!t.allowed}
                onClick={() => setOpen(t)}
              >
                {t.allowed ? 'Run…' : 'Not permitted for your role'}
              </Button>
            </Stack>
          </Card>
        ))}
      </SimpleGrid>

      <Title order={4} mb="sm">
        Job history
      </Title>
      <Card withBorder radius="md" p={0}>
        <ScrollArea>
          <Table highlightOnHover fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Job</Table.Th>
                <Table.Th>Started by</Table.Th>
                <Table.Th>Created</Table.Th>
                <Table.Th>Duration</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(jobs ?? []).map((j) => (
                <Table.Tr key={j.id} style={{ cursor: 'pointer' }} onClick={() => setDrawer(j.id)}>
                  <Table.Td>
                    <Text size="sm" fw={500}>
                      {j.title}
                    </Text>
                    <Text size="xs" c="dimmed" ff="monospace">
                      {j.id}
                    </Text>
                  </Table.Td>
                  <Table.Td>{j.user}</Table.Td>
                  <Table.Td>{when(j.created)}</Table.Td>
                  <Table.Td>{duration(j.started, j.finished)}</Table.Td>
                  <Table.Td>
                    <JobStatusBadge status={j.status} />
                  </Table.Td>
                </Table.Tr>
              ))}
              {!jobs?.length && (
                <Table.Tr>
                  <Table.Td colSpan={5}>
                    <Text c="dimmed" size="sm" ta="center" py="md">
                      No jobs yet.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>
      {open && (
        <RunModal
          type={open}
          onClose={() => setOpen(null)}
          onStarted={(id) => {
            setOpen(null);
            setDrawer(id);
          }}
          canTestData={!!status?.permissions.run_test_data_jobs}
        />
      )}
      <JobDrawer id={drawer} onClose={() => setDrawer(null)} canCancel={!!status?.permissions.run_jobs} />
    </>
  );
}

function RunModal({
  type,
  onClose,
  onStarted,
  canTestData,
}: {
  type: JobType;
  onClose: () => void;
  onStarted: (id: string) => void;
  canTestData: boolean;
}) {
  const submit = useSubmitJob();
  const { data } = useJobTypes();
  const { data: perfRuns } = useRuns('perf');
  const [p, setP] = useState<Record<string, unknown>>(defaults(type.type));
  const [reason, setReason] = useState('');
  const set = (k: string, v: unknown) => setP((cur) => ({ ...cur, [k]: v }));
  const datasets = data?.datasets ?? [];
  const chosen = (p.datasets as string[] | undefined) ?? [];
  const testData = datasets.some((d) => chosen.includes(d.id) && d.role === 'test');

  return (
    <Modal opened onClose={onClose} title={`Run: ${type.title}`} size="lg">
      <Stack>
        <Text size="sm" c="dimmed">
          {type.description}
        </Text>
        {type.type === 'checklist' && (
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              Tier
            </Text>
            <SegmentedControl
              value={p.tier as string}
              onChange={(v) => set('tier', v)}
              data={[
                { value: 'fast', label: 'Fast (rules only)' },
                { value: 'model', label: 'With model' },
                { value: 'all', label: 'Both' },
              ]}
            />
          </Stack>
        )}
        {type.type === 'suite' && (
          <>
            <SegmentedControl
              value={p.tier as string}
              onChange={(v) => set('tier', v)}
              data={[
                { value: 'fast', label: 'Fast (rules only)' },
                { value: 'model', label: 'With model' },
              ]}
            />
            <Switch
              checked={!!p.regress}
              onChange={(e) => set('regress', e.currentTarget.checked)}
              label="Compare with the reviewed baseline (regression check)"
            />
          </>
        )}
        {type.type === 'evaluate' && (
          <>
            <Checkbox.Group label="Datasets" value={chosen} onChange={(v) => set('datasets', v)}>
              <Stack gap={6} mt={6}>
                {datasets.map((d) => (
                  <Checkbox
                    key={d.id}
                    value={d.id}
                    disabled={!d.allowed}
                    label={
                      <Group gap={6}>
                        <Text size="sm">{d.id}</Text>
                        <Badge size="xs" color={d.role === 'test' ? 'red' : 'blue'} variant="light">
                          {d.role}
                        </Badge>
                        <Badge size="xs" variant="outline" color="gray">
                          {d.phi_class}
                        </Badge>
                      </Group>
                    }
                  />
                ))}
              </Stack>
            </Checkbox.Group>
            <Select
              label="Contextual model"
              data={[
                { value: 'default', label: 'As configured' },
                { value: 'none', label: 'None (rules only)' },
              ]}
              value={p.model as string}
              onChange={(v) => v && set('model', v)}
              allowDeselect={false}
            />
            {testData && (
              <Alert color="red" icon={<IconAlertTriangle size={18} />} title="Held-out test data">
                Test data is measured once per release candidate. Do not tune anything based on what you see.
                The access and your reason are recorded in the ledger and the audit trail.
                {!canTestData && ' Your role cannot run this.'}
                <Textarea
                  mt="sm"
                  label="Reason (required)"
                  value={reason}
                  onChange={(e) => setReason(e.currentTarget.value)}
                />
              </Alert>
            )}
          </>
        )}
        {type.type === 'perf' && (
          <SimpleGrid cols={2}>
            <Select
              label="Check"
              data={['latency', 'throughput', 'soak', 'coldstart', 'determinism', 'all']}
              value={p.check as string}
              onChange={(v) => v && set('check', v)}
              allowDeselect={false}
            />
            <Select
              label="Device"
              data={['cpu', 'mps', 'cuda']}
              value={p.device as string}
              onChange={(v) => v && set('device', v)}
              allowDeselect={false}
            />
            <NumberInput
              label="Soak duration (s)"
              min={10}
              max={7200}
              value={p.duration as number}
              onChange={(v) => set('duration', Number(v))}
            />
            <NumberInput
              label="Documents per length bucket"
              min={3}
              max={500}
              value={p.n as number}
              onChange={(v) => set('n', Number(v))}
            />
            <Select
              label="Contextual model"
              data={[
                { value: 'default', label: 'As configured' },
                { value: 'none', label: 'None (rules only)' },
              ]}
              value={p.model as string}
              onChange={(v) => v && set('model', v)}
              allowDeselect={false}
            />
          </SimpleGrid>
        )}
        {type.type === 'gates' && (
          <>
            <Switch
              checked={!!p.with_model}
              onChange={(e) => set('with_model', e.currentTarget.checked)}
              label="Include model-tier checks (slower; required for a GO)"
            />
            <Select
              label="Performance evidence (G8)"
              placeholder="Newest run"
              data={(perfRuns ?? []).map((r) => r.name)}
              value={(p.perf_run as string) ?? null}
              onChange={(v) => set('perf_run', v)}
              clearable
            />
          </>
        )}
        <ErrorAlert error={submit.error} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            leftSection={<IconPlayerPlay size={16} />}
            loading={submit.isPending}
            disabled={
              (type.type === 'evaluate' && !chosen.length) ||
              (testData && (!canTestData || reason.trim().length < 10))
            }
            onClick={() =>
              submit.mutate(
                { type: type.type, params: p, reason: testData ? reason : undefined },
                {
                  onSuccess: (j) => {
                    notifications.show({ color: 'blue', message: `${j.title} started` });
                    onStarted(j.id);
                  },
                },
              )
            }
          >
            Start
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function defaults(t: string): Record<string, unknown> {
  switch (t) {
    case 'checklist':
      return { tier: 'fast' };
    case 'suite':
      return { tier: 'fast', regress: true };
    case 'evaluate':
      return { datasets: [], model: 'default' };
    case 'perf':
      return { check: 'latency', device: 'cpu', duration: 60, n: 20, model: 'default' };
    case 'gates':
      return { with_model: true, perf_run: null };
    default:
      return {};
  }
}
