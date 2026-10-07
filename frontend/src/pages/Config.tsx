import { useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
} from '@mantine/core';
import { modals } from '../lib/modals';
import { IconCheck, IconRefresh, IconSearch, IconX } from '@tabler/icons-react';
import { useConfig, useReloadEngine, useResolvedConfig, useStatus, useValidateConfig } from '../api/hooks';
import { ErrorAlert, Hint, KeyValue, PageHeader } from '../components/common';
import { CATEGORY_COLOR, CATEGORY_LABEL, friendlyEntity } from '../lib/entities';
import { shortSha } from '../lib/format';

export function ConfigPage() {
  const { data: c, error } = useConfig();
  const { data: status } = useStatus();
  const [tab, setTab] = useState<string | null>('entities');
  const [q, setQ] = useState('');
  const validate = useValidateConfig();
  const reload = useReloadEngine();
  const resolved = useResolvedConfig(tab === 'resolved');
  const ents = useMemo(
    () =>
      (c?.entities ?? []).filter(
        (e) => !q || `${e.id} ${e.description} ${e.category}`.toLowerCase().includes(q.toLowerCase()),
      ),
    [c, q],
  );
  if (error) return <ErrorAlert error={error} />;
  if (!c) return <Loader />;
  const isAdmin = status?.user.role === 'admin';
  return (
    <>
      <PageHeader
        title="Configuration"
        description="What the engine treats as PHI and how it handles it. This view is read-only: changes are made in configs/phi/ through review (see Help → Configuration guide)."
        actions={
          <>
            <Button
              variant="light"
              leftSection={<IconCheck size={16} />}
              onClick={() => validate.mutate()}
              loading={validate.isPending}
            >
              Validate all environments
            </Button>
            {isAdmin && (
              <Button
                variant="light"
                color="orange"
                leftSection={<IconRefresh size={16} />}
                loading={reload.isPending}
                onClick={() =>
                  modals.confirm(
                    'Reload the engine?',
                    'Re-reads configs/phi and rebuilds the engine (including the start-up self-test). Use after a reviewed change was deployed.',
                    () => reload.mutate(),
                  )
                }
              >
                Reload engine
              </Button>
            )}
          </>
        }
      />
      <ErrorAlert error={validate.error || reload.error} />
      {validate.data && (
        <Card withBorder radius="md" mb="md">
          <Table fz="sm">
            <Table.Tbody>
              {validate.data.map((v) => (
                <Table.Tr key={v.environment}>
                  <Table.Td w={100}>{v.environment}</Table.Td>
                  <Table.Td w={120}>
                    {v.ok ? (
                      <Badge color="green" leftSection={<IconCheck size={12} />}>
                        valid
                      </Badge>
                    ) : (
                      <Badge color="red" leftSection={<IconX size={12} />}>
                        invalid
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {v.ok ? (
                      <Code>{shortSha(v.config_sha256)}</Code>
                    ) : (
                      <Text size="xs" c="red" style={{ whiteSpace: 'pre-wrap' }}>
                        {v.message}
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
      <Text size="sm" c="dimmed" mb="sm">
        Environment <b>{c.describe.environment}</b> · config <Code>{shortSha(c.describe.config_sha256)}</Code>{' '}
        · catalog {c.describe.catalog_version}
      </Text>
      <Tabs value={tab} onChange={setTab}>
        <Tabs.List>
          <Tabs.Tab value="entities">What counts as PHI ({c.entities.length})</Tabs.Tab>
          <Tabs.Tab value="safety">Redaction & safety</Tabs.Tab>
          <Tabs.Tab value="recognizers">Recognizers ({c.recognizers.length})</Tabs.Tab>
          <Tabs.Tab value="models">Models</Tabs.Tab>
          <Tabs.Tab value="thresholds">Thresholds</Tabs.Tab>
          <Tabs.Tab value="resolved">Resolved file</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="entities" pt="md">
          <TextInput
            mb="sm"
            placeholder="Search entity types"
            leftSection={<IconSearch size={14} />}
            value={q}
            onChange={(e) => setQ(e.currentTarget.value)}
            maw={360}
          />
          <Card withBorder radius="md" p={0}>
            <ScrollArea>
              <Table fz="sm" striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Type</Table.Th>
                    <Table.Th>Description</Table.Th>
                    <Table.Th>Category</Table.Th>
                    <Table.Th>Risk</Table.Th>
                    <Table.Th>HIPAA Safe Harbor</Table.Th>
                    <Table.Th>Replaced with</Table.Th>
                    <Table.Th>Egress guard</Table.Th>
                    <Table.Th>Active</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {ents.map((e) => (
                    <Table.Tr key={e.id}>
                      <Table.Td>
                        <Text size="sm" fw={600}>
                          {friendlyEntity(e.id)}
                        </Text>
                        <Text size="xs" c="dimmed" ff="monospace">
                          {e.id}
                        </Text>
                      </Table.Td>
                      <Table.Td maw={340}>{e.description}</Table.Td>
                      <Table.Td>
                        <Badge color={CATEGORY_COLOR[e.category] ?? 'gray'} variant="light">
                          {CATEGORY_LABEL[e.category] ?? e.category}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{e.risk_tier}</Table.Td>
                      <Table.Td>{e.safe_harbor.join(', ') || '-'}</Table.Td>
                      <Table.Td>{e.default_action ?? '-'}</Table.Td>
                      <Table.Td>{e.outbound_guard}</Table.Td>
                      <Table.Td>
                        {e.enabled ? <Badge color="green">on</Badge> : <Badge color="gray">off</Badge>}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="safety" pt="md">
          <Stack>
            <Card withBorder radius="md">
              <KeyValue
                rows={[
                  ['Default replacement', String(c.redaction.default_operator)],
                  ['Token format', <Code key="t">{String(c.redaction.token_format)}</Code>],
                  ['Token consistency scope', String(c.redaction.consistency_scope)],
                  ['Vault', <Code key="v">{JSON.stringify(c.redaction.vault)}</Code>],
                  ['Outbound guard', <Code key="o">{JSON.stringify(c.outbound_guard)}</Code>],
                  [
                    'On failure',
                    <Code key="f" block>
                      {JSON.stringify(c.failure, null, 1)}
                    </Code>,
                  ],
                  [
                    'Type refinements',
                    <Code key="r" block>
                      {JSON.stringify(c.type_refinements, null, 1)}
                    </Code>,
                  ],
                ]}
              />
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="recognizers" pt="md">
          <Card withBorder radius="md" p={0}>
            <ScrollArea>
              <Table fz="sm" striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Recognizer</Table.Th>
                    <Table.Th>Kind</Table.Th>
                    <Table.Th>Entities</Table.Th>
                    <Table.Th>Developed on</Table.Th>
                    <Table.Th>Mode</Table.Th>
                    <Table.Th>Active</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {c.recognizers.map((r) => (
                    <Table.Tr key={r.id}>
                      <Table.Td ff="monospace">{r.id}</Table.Td>
                      <Table.Td>{r.kind}</Table.Td>
                      <Table.Td maw={320}>{r.entities.join(', ') || '-'}</Table.Td>
                      <Table.Td>{r.developed_on.join(', ') || '-'}</Table.Td>
                      <Table.Td>
                        {r.mode === 'shadow' ? <Badge color="violet">shadow</Badge> : 'production'}
                      </Table.Td>
                      <Table.Td>
                        {r.enabled ? <Badge color="green">on</Badge> : <Badge color="gray">off</Badge>}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="models" pt="md">
          <Hint>
            Contextual models run locally; weights are verified against a pinned hash before the engine
            serves.
          </Hint>
          <Card withBorder radius="md" p={0}>
            <Table fz="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Key</Table.Th>
                  <Table.Th>Model</Table.Th>
                  <Table.Th>Revision</Table.Th>
                  <Table.Th>Mode</Table.Th>
                  <Table.Th>Weights pinned</Table.Th>
                  <Table.Th>Active</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {c.models.map((m) => (
                  <Table.Tr key={m.id}>
                    <Table.Td ff="monospace">{m.id}</Table.Td>
                    <Table.Td>{m.model_id}</Table.Td>
                    <Table.Td ff="monospace">{m.revision}</Table.Td>
                    <Table.Td>{m.mode}</Table.Td>
                    <Table.Td>
                      {m.weights_pinned ? <Badge color="green">yes</Badge> : <Badge color="yellow">no</Badge>}
                    </Table.Td>
                    <Table.Td>
                      {m.enabled ? <Badge color="green">on</Badge> : <Badge color="gray">off</Badge>}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="thresholds" pt="md">
          <Alert color="blue" variant="light" mb="sm">
            Model confidence: modality.model_entity &gt; model_entity &gt; modality.entity &gt; entity &gt;
            model &gt; recognizer &gt; default. Calibrated on development data only.
          </Alert>
          <Card withBorder radius="md">
            <ScrollArea h={520}>
              <Code block>{JSON.stringify(c.thresholds, null, 1)}</Code>
            </ScrollArea>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="resolved" pt="md">
          <Card withBorder radius="md">
            <Text size="sm" c="dimmed" mb="xs">
              Sources (in order):{' '}
              {c.describe.sources
                .map((s) => (typeof s === 'string' ? s : (s.path.split('/configs/')[1] ?? s.path)))
                .join(' → ')}
            </Text>
            {resolved.isLoading ? (
              <Loader />
            ) : (
              <ScrollArea h={560}>
                <Code block>{resolved.data?.yaml}</Code>
              </ScrollArea>
            )}
          </Card>
        </Tabs.Panel>
      </Tabs>
      <Group h={24} />
    </>
  );
}
