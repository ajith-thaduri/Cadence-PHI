import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconChecklist, IconFiles, IconRocket, IconWand } from '@tabler/icons-react';
import { Link } from 'react-router';
import { useGates, useJobs, useStatus } from '../api/hooks';
import {
  ErrorAlert,
  JobStatusBadge,
  KeyValue,
  PageHeader,
  StatCard,
  VerdictBadge,
} from '../components/common';
import { shortSha, when } from '../lib/format';
import { usePrefs } from '../lib/prefs';

export function OverviewPage() {
  const { data: s, error } = useStatus();
  const { data: gates } = useGates();
  const { data: jobs } = useJobs();
  const { prefs } = usePrefs();
  if (error) return <ErrorAlert error={error} />;
  if (!s) return <Loader />;
  const e = s.engine;
  const verified = Object.values(e.weights_verified).filter((v) => v && v !== 'injected').length;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Find and remove protected health information (PHI) from text before it is shared - on this machine, with no external services."
      />
      {!e.ready && !e.error && (
        <Alert color="yellow" icon={<Loader size={16} />} mb="md" title="The engine is starting">
          Loading the detection rules and the contextual model and running the start-up self-test. This
          usually takes a few seconds.
        </Alert>
      )}
      {e.error && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />} mb="md" title="The engine could not start">
          {e.error}. The console refuses to process text until this is fixed (fail-closed). See Help → Console
          guide.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 1, sm: 3 }} mb="lg">
        <ActionCard
          icon={<IconWand />}
          title="Redact text"
          to="/redact"
          enabled={s.permissions.analyze}
          text="Paste or drop a document and get a redacted copy you can share."
        />
        <ActionCard
          icon={<IconFiles />}
          title="Redact many files"
          to="/batch"
          enabled={s.permissions.batch}
          text="Upload text or JSONL files and download redacted copies as a zip."
        />
        <ActionCard
          icon={<IconChecklist />}
          title="Run checks"
          to="/checks"
          enabled={s.permissions.run_jobs}
          text="Run behavioural tests, evaluations, performance tests and release gates."
        />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 2, md: 4 }} mb="lg">
        <StatCard
          label="Engine"
          value={e.ready ? 'Ready' : e.error ? 'Failed' : 'Starting'}
          color={e.ready ? 'green' : e.error ? 'red' : 'yellow'}
        />
        <StatCard
          label="Environment"
          value={e.environment}
          hint="Which configuration overlay the engine uses"
        />
        <StatCard
          label="Release decision"
          value={gates?.summary.decision ?? 'Not run'}
          color={gates?.summary.decision === 'GO' ? 'green' : gates ? 'red' : undefined}
          hint="Minimum across release gates G1-G9 (latest run)"
        />
        <StatCard
          label="Model integrity"
          value={`${verified}/${e.models.length} verified`}
          color={verified === e.models.length ? 'green' : 'yellow'}
          hint="Model weight files checked against the pinned SHA-256 before serving"
        />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder radius="md">
          <Title order={4} mb="sm">
            Safety settings in force
          </Title>
          <KeyValue
            rows={[
              [
                'Outbound guard',
                e.outbound_guard ? <Badge color="green">on</Badge> : <Badge color="red">off</Badge>,
              ],
              ['Token consistency', e.consistency_scope],
              ['Re-identification vault', e.vault_backend],
              ['Start-up self-test', e.self_test_required ? 'required' : 'optional'],
              ['Contextual model(s)', e.models.join(', ') || 'none (rules only)'],
              ['Device', e.device ?? '-'],
              ...(prefs.technical
                ? ([
                    ['Config hash', <Code key="c">{shortSha(e.config_sha256)}</Code>],
                    ['Catalog version', e.catalog_version],
                    ['Code', <Code key="k">{`${e.code_commit} / ${shortSha(e.code_fingerprint)}`}</Code>],
                    ['Console version', s.console_version],
                  ] as [string, React.ReactNode][])
                : []),
            ]}
          />
        </Card>
        <Card withBorder radius="md">
          <Group justify="space-between" mb="sm">
            <Title order={4}>Recent activity</Title>
            <Anchor component={Link} to="/checks" size="sm">
              All jobs
            </Anchor>
          </Group>
          {jobs && jobs.length > 0 ? (
            <Table fz="sm" verticalSpacing={6}>
              <Table.Tbody>
                {jobs.slice(0, 6).map((j) => (
                  <Table.Tr key={j.id}>
                    <Table.Td>{j.title}</Table.Td>
                    <Table.Td c="dimmed">{when(j.created)}</Table.Td>
                    <Table.Td>
                      <JobStatusBadge status={j.status} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : (
            <Text c="dimmed" size="sm">
              No jobs yet.
            </Text>
          )}
          {gates && (
            <Group mt="md" gap="xs">
              <IconRocket size={16} />
              <Text size="sm">Latest gates:</Text>
              {Object.entries(gates.summary.gates).map(([k, v]) => (
                <Group key={k} gap={2}>
                  <Text size="xs" fw={600}>
                    {k}
                  </Text>
                  <VerdictBadge verdict={v.verdict} size="sm" />
                </Group>
              ))}
            </Group>
          )}
        </Card>
      </SimpleGrid>
    </>
  );
}

function ActionCard({
  icon,
  title,
  text,
  to,
  enabled,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
  to: string;
  enabled: boolean;
}) {
  return (
    <Card withBorder radius="md" padding="lg">
      <Stack gap="xs" h="100%" justify="space-between">
        <div>
          <Group gap="xs" mb={4}>
            {icon}
            <Text fw={700}>{title}</Text>
          </Group>
          <Text size="sm" c="dimmed">
            {text}
          </Text>
        </div>
        <Button component={Link} to={to} variant="light" disabled={!enabled}>
          {enabled ? 'Open' : 'Not permitted for your role'}
        </Button>
      </Stack>
    </Card>
  );
}
