import { useMemo, useState } from 'react';
import {
  Anchor,
  Badge,
  Card,
  Code,
  Group,
  Loader,
  ScrollArea,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconSearch } from '@tabler/icons-react';
import { Link, useParams } from 'react-router';
import { useRun, useRuns } from '../api/hooks';
import type { RunDetail, RunListItem } from '../api/types';
import { ErrorAlert, Md, PageHeader, StatCard } from '../components/common';
import { num, pct, shortSha, when } from '../lib/format';
import { usePrefs } from '../lib/prefs';

const KIND_LABEL: Record<string, string> = {
  all: 'All',
  phi: 'Evaluations',
  suite: 'Prompt suite',
  checklist: 'Checklist',
  perf: 'Performance',
  gates: 'Gates',
};

export function ResultsPage() {
  const [kind, setKind] = useState('all');
  const [q, setQ] = useState('');
  const { data, error, isLoading } = useRuns(kind === 'all' ? undefined : kind);
  const rows = useMemo(
    () => (data ?? []).filter((r) => !q || r.name.toLowerCase().includes(q.toLowerCase())),
    [data, q],
  );
  return (
    <>
      <PageHeader
        title="Results"
        description="Every evaluation, check and performance run stored under results/, newest first."
      />
      <Group mb="md" wrap="wrap">
        <SegmentedControl
          value={kind}
          onChange={setKind}
          data={Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <TextInput
          placeholder="Filter by name"
          leftSection={<IconSearch size={14} />}
          value={q}
          onChange={(e) => setQ(e.currentTarget.value)}
        />
      </Group>
      <ErrorAlert error={error} />
      {isLoading ? (
        <Loader />
      ) : (
        <Card withBorder radius="md" p={0}>
          <ScrollArea>
            <Table highlightOnHover fz="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Run</Table.Th>
                  <Table.Th>Type</Table.Th>
                  <Table.Th>When</Table.Th>
                  <Table.Th>Headline</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r) => (
                  <Table.Tr key={`${r.kind}/${r.name}`}>
                    <Table.Td>
                      <Anchor component={Link} to={`/results/${r.kind}/${r.name}`} ff="monospace" size="sm">
                        {r.name}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light">{r.label}</Badge>
                    </Table.Td>
                    <Table.Td>{when(r.modified)}</Table.Td>
                    <Table.Td>
                      <Headline r={r} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </Card>
      )}
    </>
  );
}

function Headline({ r }: { r: RunListItem }) {
  const h = r.headline as Record<string, unknown>;
  if (r.kind === 'phi') {
    const ds = (h.datasets ?? {}) as Record<string, Record<string, number>>;
    return (
      <Stack gap={0}>
        {Object.entries(ds).map(([d, v]) => (
          <Text key={d} size="xs">
            {d}: residual {pct(v.residual_mean)} · high-risk recall {pct(v.high_risk_full_recall)}
          </Text>
        ))}
      </Stack>
    );
  }
  if (r.kind === 'checklist')
    return (
      <Group gap={6}>
        <Text size="xs">
          {String(h.passed)}/{String(h.cases)} passed
        </Text>
        <Badge size="xs" color={h.g5 ? 'green' : 'red'}>
          G5 {h.g5 ? 'pass' : 'fail'}
        </Badge>
      </Group>
    );
  if (r.kind === 'suite')
    return (
      <Text size="xs">
        {String(h.detected)}/{String(h.expected_entities)} detected · {String(h.false_positives)} false
        positives · residual {pct(h.residual_phi_pct)}
      </Text>
    );
  if (r.kind === 'perf')
    return (
      <Text size="xs">
        {String(h.device)} · typical p95 {num(h.p95_ms_typical)} ms · G8 {String(h.g8)}
      </Text>
    );
  if (r.kind === 'gates')
    return (
      <Badge color={h.decision === 'GO' ? 'green' : 'red'} variant="light">
        {String(h.decision)}
      </Badge>
    );
  return null;
}

export function RunDetailPage() {
  const { kind = '', name = '' } = useParams();
  const { data, error, isLoading } = useRun(kind, name);
  const { prefs } = usePrefs();
  if (error) return <ErrorAlert error={error} />;
  if (isLoading || !data) return <Loader />;
  return (
    <>
      <PageHeader
        title={data.label}
        description={
          <>
            <Code>{data.name}</Code> ·{' '}
            <Anchor component={Link} to="/results">
              all results
            </Anchor>
          </>
        }
      />
      {data.kind === 'phi' && <EvalDetail d={data} />}
      {data.kind === 'checklist' && <ChecklistDetail d={data} />}
      {data.kind === 'suite' && <SuiteDetail d={data} />}
      {(data.kind === 'perf' || data.kind === 'gates') && data.report_md && (
        <Card withBorder radius="md">
          <Md>{data.report_md}</Md>
        </Card>
      )}
      {prefs.technical && (
        <Card withBorder radius="md" mt="lg">
          <Title order={6} mb="xs">
            Raw summary
          </Title>
          <ScrollArea h={400}>
            <Code block>{JSON.stringify(data.summary, null, 1)}</Code>
          </ScrollArea>
        </Card>
      )}
    </>
  );
}

function EvalDetail({ d }: { d: RunDetail }) {
  const datasets = (d.summary.datasets ?? {}) as Record<string, Record<string, unknown>>;
  const [ds, setDs] = useState(Object.keys(datasets)[0] ?? '');
  const v = datasets[ds] ?? {};
  const resid = (v.RESIDUAL_PHI_AFTER_SHIELD ?? {}) as Record<string, number>;
  const ci = (v.ci95 ?? {}) as Record<string, number[]>;
  const checks = (v.redaction_checks ?? {}) as Record<string, number>;
  const ents = (d.entity_metrics ?? []).filter((r) => r.dataset === ds);
  const cfg = (d.summary.config ?? {}) as Record<string, unknown>;
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Config <Code>{shortSha(cfg.config_sha256 as string)}</Code> · environment {String(cfg.environment)} ·
        models {String(cfg.models_enabled)}
      </Text>
      <Tabs value={ds} onChange={(x) => x && setDs(x)}>
        <Tabs.List>
          {Object.keys(datasets).map((k) => (
            <Tabs.Tab key={k} value={k}>
              {k}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatCard
          label="Residual PHI (mean)"
          value={pct(resid.mean)}
          hint={`95% CI ${pct(ci.residual_mean?.[0])} – ${pct(ci.residual_mean?.[1])}. Share of PHI characters still readable.`}
        />
        <StatCard
          label="High-risk recall"
          value={pct(v.high_risk_full_recall)}
          hint="Direct identifiers fully covered"
        />
        <StatCard label="Precision" value={pct(v.span_precision)} hint="Share of redactions that were PHI" />
        <StatCard label="Documents" value={num(v.documents)} />
        <StatCard
          label="Over-redaction"
          value={pct(v.over_redaction_char_rate)}
          hint="Non-PHI characters redacted"
        />
        <StatCard label="Docs fully sanitised" value={pct(v.complete_document_sanitization_rate)} />
        <StatCard
          label="Verbatim leaks"
          value={num(checks.verbatim_leaks)}
          color={checks.verbatim_leaks ? 'orange' : 'green'}
        />
        <StatCard
          label="Redaction errors"
          value={num((checks.output_slice_errors ?? 0) + (checks.roundtrip_failures ?? 0))}
          color={(checks.output_slice_errors ?? 0) + (checks.roundtrip_failures ?? 0) ? 'red' : 'green'}
          hint="Output slice errors + vault round-trip failures"
        />
      </SimpleGrid>
      {ents.length > 0 && (
        <Card withBorder radius="md" p={0}>
          <ScrollArea>
            <Table fz="sm" striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Entity</Table.Th>
                  <Table.Th>Risk</Table.Th>
                  <Table.Th>Support</Table.Th>
                  <Table.Th>Full-span recall (95% CI)</Table.Th>
                  <Table.Th>Precision</Table.Th>
                  <Table.Th>Residual chars</Table.Th>
                  <Table.Th>Evidence</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {ents.map((r) => (
                  <Table.Tr key={r.entity}>
                    <Table.Td>{r.entity}</Table.Td>
                    <Table.Td>{r.risk_tier}</Table.Td>
                    <Table.Td>{r.support}</Table.Td>
                    <Table.Td>
                      {pct(parseFloat(r.full_span_recall))} ({pct(parseFloat(r.full_span_recall_ci_low))}–
                      {pct(parseFloat(r.full_span_recall_ci_high))})
                    </Table.Td>
                    <Table.Td>{pct(parseFloat(r.precision))}</Table.Td>
                    <Table.Td>{r.residual_chars}</Table.Td>
                    <Table.Td>
                      {r.insufficient_evidence === 'True' ? (
                        <Badge size="xs" color="yellow">
                          insufficient
                        </Badge>
                      ) : (
                        ''
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </Card>
      )}
    </Stack>
  );
}

function ChecklistDetail({ d }: { d: RunDetail }) {
  const s = d.summary as Record<string, unknown>;
  const by = (s.by_entity_test ?? {}) as Record<string, { cases: number; passed: number; pass_rate: number }>;
  return (
    <Stack>
      <SimpleGrid cols={{ base: 2, md: 3 }}>
        <StatCard label="Cases passed" value={`${String(s.passed)}/${String(s.cases)}`} />
        <StatCard label="MFT pass rate (enabled)" value={pct(s.mft_enabled_entities_pass_rate)} />
        <StatCard
          label="Gate G5"
          value={s.gate_G5_mft_100pct ? 'Pass' : 'Fail'}
          color={s.gate_G5_mft_100pct ? 'green' : 'red'}
        />
      </SimpleGrid>
      <Card withBorder radius="md" p={0}>
        <ScrollArea h={520}>
          <Table fz="sm" striped stickyHeader>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Entity</Table.Th>
                <Table.Th>Test type</Table.Th>
                <Table.Th>Passed</Table.Th>
                <Table.Th>Rate</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {Object.entries(by).map(([k, v]) => {
                const [ent, tt] = k.split('|');
                return (
                  <Table.Tr key={k}>
                    <Table.Td>{ent}</Table.Td>
                    <Table.Td>{tt}</Table.Td>
                    <Table.Td>
                      {v.passed}/{v.cases}
                    </Table.Td>
                    <Table.Td>
                      <Badge color={v.pass_rate === 1 ? 'green' : 'red'} variant="light">
                        {pct(v.pass_rate)}
                      </Badge>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>
    </Stack>
  );
}

function SuiteDetail({ d }: { d: RunDetail }) {
  const s = d.summary as Record<string, unknown>;
  const keys: [string, string][] = [
    ['prompts', 'Prompts'],
    ['expected_entities', 'Expected PHI'],
    ['detected', 'Detected'],
    ['redacted', 'Redacted'],
    ['missed', 'Missed'],
    ['false_positives', 'False positives'],
  ];
  return (
    <SimpleGrid cols={{ base: 2, md: 4 }}>
      {keys.map(([k, l]) => (
        <StatCard key={k} label={l} value={num(s[k])} />
      ))}
      <StatCard label="Residual PHI" value={pct(s.residual_phi_pct)} />
    </SimpleGrid>
  );
}
