import { useMemo, useState } from 'react';
import {
  Accordion,
  Alert,
  Badge,
  Button,
  Card,
  Code,
  CopyButton,
  Drawer,
  FileButton,
  Grid,
  Group,
  Kbd,
  MultiSelect,
  NumberInput,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Tabs,
  TagsInput,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { getHotkeyHandler } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconCopy,
  IconDownload,
  IconFileText,
  IconShieldCheck,
  IconShieldX,
  IconSparkles,
  IconWand,
  IconX,
} from '@tabler/icons-react';
import { useAnalyze, useConfig, useRestore, useStatus } from '../api/hooks';
import type { AnalyzeIn, AnalyzeOut, DetectionOut, KnownDetails, Modality, WhatIf } from '../api/types';
import { ErrorAlert, EmptyState, KeyValue, PageHeader } from '../components/common';
import { HighlightedText } from '../components/HighlightedText';
import {
  CATEGORY_COLOR,
  CATEGORY_LABEL,
  CONFIDENCE_COLOR,
  DECISION_COLOR,
  MODALITIES,
  OPERATORS,
  entityColor,
  friendlyEntity,
} from '../lib/entities';
import { mask } from '../lib/segments';
import { usePrefs } from '../lib/prefs';

// Conspicuously fabricated example (555-01xx phone, example.com) - never real data.
const EXAMPLE =
  'Hi, this is Maria Lopez calling about my refill. My date of birth is April 12, 1985 and my member ID is ABC12345. ' +
  'You can call me back at 415-555-0100 or email maria.lopez@example.com. Dr. Kenji Watanabe at Mercy Valley Clinic ' +
  'said my next appointment is March 14. My daughter Lucia usually drives me.';

const EMPTY_KNOWN: KnownDetails = {
  member_ids: [],
  contacts: [],
  providers: [],
  facilities: [],
  staff: [],
  employers: [],
};

interface Analysis {
  text: string; // the exact text that was analysed (offsets refer to it)
  result: AnalyzeOut;
}

export function RedactPage() {
  const { data: status } = useStatus();
  const { data: config } = useConfig();
  const { prefs, set } = usePrefs();
  const analyze = useAnalyze();
  const [text, setText] = useState('');
  const [modality, setModality] = useState<Modality>('typed');
  const [operator, setOperator] = useState('TOKENIZE');
  const [guard, setGuard] = useState(true);
  const [known, setKnown] = useState<KnownDetails>(EMPTY_KNOWN);
  const [conversationId, setConversationId] = useState('');
  const [whatIf, setWhatIf] = useState<WhatIf>({
    disable_entities: [],
    enable_entities: [],
    disable_recognizers: [],
    thresholds: {},
  });
  const [whatIfOn, setWhatIfOn] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [selected, setSelected] = useState<DetectionOut | null>(null);

  const limit = status?.limits.max_text_chars ?? 50000;
  const tooLong = text.length > limit;
  const stale = analysis !== null && analysis.text !== text;

  const run = () => {
    if (!text.trim() || tooLong || analyze.isPending) return;
    const snapshot = text;
    analyze.mutate(
      {
        text: snapshot,
        modality,
        operator: operator as NonNullable<AnalyzeIn['operator']>,
        policy_profile: 'cadence_default',
        run_guard: guard,
        include_evidence: prefs.technical,
        known: hasKnown(known) ? known : null,
        conversation_id: conversationId || null,
        what_if: whatIfOn ? whatIf : null,
      },
      {
        onSuccess: (result) => {
          setAnalysis({ text: snapshot, result });
          setSelected(null);
        },
      },
    );
  };

  return (
    <>
      <PageHeader
        title="Redact text"
        description="Paste text, choose what kind of text it is, and get a copy with personal and health identifiers replaced. Nothing is stored; the original text never leaves this machine."
      />
      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Stack>
            <Card withBorder radius="md">
              <Group justify="space-between" mb="xs">
                <Title order={5}>1. Your text</Title>
                <Group gap={4}>
                  <FileButton
                    accept=".txt,text/plain"
                    onChange={async (f) => {
                      if (f) setText(await f.text());
                    }}
                  >
                    {(props) => (
                      <Button
                        {...props}
                        size="compact-sm"
                        variant="subtle"
                        leftSection={<IconFileText size={14} />}
                      >
                        Open .txt
                      </Button>
                    )}
                  </FileButton>
                  <Button
                    size="compact-sm"
                    variant="subtle"
                    leftSection={<IconSparkles size={14} />}
                    onClick={() => setText(EXAMPLE)}
                  >
                    Example
                  </Button>
                  <Button
                    size="compact-sm"
                    variant="subtle"
                    color="gray"
                    leftSection={<IconX size={14} />}
                    onClick={() => {
                      setText('');
                      setAnalysis(null);
                      setKnown(EMPTY_KNOWN);
                    }}
                  >
                    Clear
                  </Button>
                </Group>
              </Group>
              <Textarea
                aria-label="Text to redact"
                placeholder="Paste a call transcript, note or message…"
                autosize
                minRows={10}
                maxRows={22}
                value={text}
                onChange={(e) => setText(e.currentTarget.value)}
                onKeyDown={getHotkeyHandler([['mod+Enter', run]])}
                error={tooLong ? `Too long: the limit is ${limit.toLocaleString()} characters` : undefined}
                styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)', fontSize: 13 } }}
              />
              <Text size="xs" c="dimmed" mt={4} ta="right">
                {text.length.toLocaleString()} / {limit.toLocaleString()} characters
              </Text>
            </Card>

            <Card withBorder radius="md">
              <Title order={5} mb="xs">
                2. Options
              </Title>
              <Stack gap="sm">
                <Select
                  label="What kind of text is this?"
                  description={MODALITIES.find((m) => m.value === modality)?.hint}
                  data={MODALITIES.map((m) => ({ value: m.value, label: m.label }))}
                  value={modality}
                  onChange={(v) => v && setModality(v as Modality)}
                  allowDeselect={false}
                />
                <Select
                  label="How should identifiers be replaced?"
                  description={(() => {
                    const o = OPERATORS.find((x) => x.value === operator);
                    return o
                      ? `e.g. ${o.example}${o.reversible ? ' - reversible by authorised staff' : ' - cannot be reversed'}`
                      : '';
                  })()}
                  data={OPERATORS.map((o) => ({ value: o.value, label: o.label }))}
                  value={operator}
                  onChange={(v) => v && setOperator(v)}
                  allowDeselect={false}
                />
                <Switch
                  checked={guard}
                  onChange={(e) => setGuard(e.currentTarget.checked)}
                  label="Double-check the redacted copy before sharing"
                  description="An independent second scanner looks for anything that was missed (recommended)."
                />
              </Stack>
              <Accordion variant="separated" mt="md" multiple>
                <Accordion.Item value="known">
                  <Accordion.Control>Known details about the member (optional)</Accordion.Control>
                  <Accordion.Panel>
                    <Text size="xs" c="dimmed" mb="xs">
                      If you know who the text is about, enter their details: the engine will then find them
                      reliably, including nicknames and spoken forms. Used for this request only and discarded
                      immediately.
                    </Text>
                    <KnownForm value={known} onChange={setKnown} />
                  </Accordion.Panel>
                </Accordion.Item>
                {prefs.technical && (
                  <Accordion.Item value="conv">
                    <Accordion.Control>Conversation (token consistency)</Accordion.Control>
                    <Accordion.Panel>
                      <TextInput
                        label="Conversation id"
                        description="Requests with the same id keep the same placeholders across turns ([NAME_1] stays the same person)."
                        value={conversationId}
                        onChange={(e) =>
                          setConversationId(e.currentTarget.value.replace(/[^A-Za-z0-9_\-:.]/g, ''))
                        }
                      />
                    </Accordion.Panel>
                  </Accordion.Item>
                )}
                {status?.features.what_if && prefs.technical && config && (
                  <Accordion.Item value="whatif">
                    <Accordion.Control>What-if analysis (advanced)</Accordion.Control>
                    <Accordion.Panel>
                      <WhatIfForm on={whatIfOn} setOn={setWhatIfOn} value={whatIf} onChange={setWhatIf} />
                    </Accordion.Panel>
                  </Accordion.Item>
                )}
              </Accordion>
            </Card>

            <Button
              size="md"
              leftSection={<IconWand size={18} />}
              onClick={run}
              loading={analyze.isPending}
              disabled={!text.trim() || tooLong || !status?.permissions.analyze || !status?.engine.ready}
            >
              Redact
            </Button>
            <Text size="xs" c="dimmed" ta="center">
              Shortcut: <Kbd>Ctrl</Kbd>/<Kbd>⌘</Kbd> + <Kbd>Enter</Kbd>
              {!status?.permissions.analyze && ' · your role cannot analyse text'}
            </Text>
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 7 }}>
          <ErrorAlert error={analyze.error} />
          {!analysis ? (
            <Card withBorder radius="md" h="100%">
              <EmptyState icon={<IconWand />} title="Results appear here">
                Paste text on the left and choose Redact. Try the example to see how it works.
              </EmptyState>
            </Card>
          ) : (
            <Results
              analysis={analysis}
              stale={stale}
              selected={selected}
              onSelect={setSelected}
              maskValues={prefs.maskValues}
              setMask={(m) => set({ maskValues: m })}
              technical={prefs.technical}
              canRestore={!!status?.features.restore}
            />
          )}
        </Grid.Col>
      </Grid>
      {analysis && (
        <FindingDrawer
          analysis={analysis}
          detection={selected}
          onClose={() => setSelected(null)}
          masked={prefs.maskValues}
          technical={prefs.technical}
        />
      )}
    </>
  );
}

function hasKnown(k: KnownDetails): boolean {
  return Object.values(k).some((v) => (Array.isArray(v) ? v.length > 0 : !!v));
}

function KnownForm({ value, onChange }: { value: KnownDetails; onChange: (k: KnownDetails) => void }) {
  const f = (k: keyof KnownDetails) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ ...value, [k]: e.currentTarget.value || null });
  const t = (k: keyof KnownDetails) => (v: string[]) => onChange({ ...value, [k]: v });
  return (
    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
      <TextInput label="Full name" value={value.name ?? ''} onChange={f('name')} />
      <TextInput label="Date of birth" placeholder="YYYY-MM-DD" value={value.dob ?? ''} onChange={f('dob')} />
      <TagsInput label="Member IDs" value={value.member_ids ?? []} onChange={t('member_ids')} />
      <TextInput label="MRN" value={value.mrn ?? ''} onChange={f('mrn')} />
      <TextInput label="Phone" value={value.phone ?? ''} onChange={f('phone')} />
      <TextInput label="Email" value={value.email ?? ''} onChange={f('email')} />
      <TextInput label="Street address" value={value.address ?? ''} onChange={f('address')} />
      <Group grow gap="xs">
        <TextInput label="City" value={value.city ?? ''} onChange={f('city')} />
        <TextInput label="ZIP" value={value.zip ?? ''} onChange={f('zip')} />
      </Group>
      <TagsInput label="Relatives / caregivers" value={value.contacts ?? []} onChange={t('contacts')} />
      <TagsInput label="Providers" value={value.providers ?? []} onChange={t('providers')} />
      <TagsInput label="Facilities" value={value.facilities ?? []} onChange={t('facilities')} />
      <TagsInput label="Care team" value={value.staff ?? []} onChange={t('staff')} />
    </SimpleGrid>
  );
}

function WhatIfForm({
  on,
  setOn,
  value,
  onChange,
}: {
  on: boolean;
  setOn: (b: boolean) => void;
  value: WhatIf;
  onChange: (w: WhatIf) => void;
}) {
  const { data: config } = useConfig();
  const [thrKey, setThrKey] = useState<string | null>(null);
  const [thrVal, setThrVal] = useState<number | string>(0.5);
  if (!config) return null;
  const enabledEnts = config.entities.filter((e) => e.enabled).map((e) => e.id);
  const disabledEnts = config.entities.filter((e) => !e.enabled).map((e) => e.id);
  const thrOptions = [
    ...config.entities.map((e) => ({ value: `entity:${e.id}`, label: `Entity ${e.id}` })),
    ...config.models.map((m) => ({ value: `model:${m.id}`, label: `Model ${m.id}` })),
  ];
  return (
    <Stack gap="xs">
      <Switch
        checked={on}
        onChange={(e) => setOn(e.currentTarget.checked)}
        label="Apply these temporary overrides to the next run"
      />
      <Text size="xs" c="dimmed">
        Overrides apply only to your request; the deployed configuration is unchanged. Results are marked as
        what-if.
      </Text>
      <MultiSelect
        label="Turn off entity types"
        data={enabledEnts}
        value={value.disable_entities ?? []}
        onChange={(v) => onChange({ ...value, disable_entities: v })}
        searchable
        clearable
      />
      {disabledEnts.length > 0 && (
        <MultiSelect
          label="Turn on entity types"
          data={disabledEnts}
          value={value.enable_entities ?? []}
          onChange={(v) => onChange({ ...value, enable_entities: v })}
          searchable
          clearable
        />
      )}
      <MultiSelect
        label="Turn off recognizers"
        data={config.recognizers.filter((r) => r.enabled).map((r) => r.id)}
        value={value.disable_recognizers ?? []}
        onChange={(v) => onChange({ ...value, disable_recognizers: v })}
        searchable
        clearable
      />
      <Select
        label="Contextual model"
        data={[
          { value: '__cfg', label: 'As configured' },
          { value: '__none', label: 'None (rules, conversation rules and known details only)' },
          ...config.models.map((m) => ({ value: m.id, label: `Only ${m.id}` })),
        ]}
        value={value.models == null ? '__cfg' : value.models.length === 0 ? '__none' : value.models[0]}
        onChange={(v) =>
          onChange({ ...value, models: v === '__cfg' || !v ? null : v === '__none' ? [] : [v] })
        }
        allowDeselect={false}
      />
      <Group align="flex-end" gap="xs">
        <Select
          label="Threshold override"
          data={thrOptions}
          value={thrKey}
          onChange={setThrKey}
          searchable
          style={{ flex: 1 }}
        />
        <NumberInput
          value={thrVal}
          onChange={setThrVal}
          min={0}
          max={1}
          step={0.05}
          decimalScale={2}
          w={90}
        />
        <Button
          variant="light"
          disabled={!thrKey}
          onClick={() =>
            thrKey &&
            onChange({ ...value, thresholds: { ...(value.thresholds ?? {}), [thrKey]: Number(thrVal) } })
          }
        >
          Add
        </Button>
      </Group>
      <Group gap={4}>
        {Object.entries(value.thresholds ?? {}).map(([k, v]) => (
          <Badge
            key={k}
            variant="light"
            rightSection={
              <IconX
                size={10}
                style={{ cursor: 'pointer' }}
                onClick={() => {
                  const next = { ...(value.thresholds ?? {}) };
                  delete next[k];
                  onChange({ ...value, thresholds: next });
                }}
              />
            }
          >
            {k} = {v}
          </Badge>
        ))}
      </Group>
    </Stack>
  );
}

function Results(props: {
  analysis: Analysis;
  stale: boolean;
  selected: DetectionOut | null;
  onSelect: (d: DetectionOut) => void;
  maskValues: boolean;
  setMask: (b: boolean) => void;
  technical: boolean;
  canRestore: boolean;
}) {
  const { analysis, stale, maskValues, technical } = props;
  const r = analysis.result;
  const { data: config } = useConfig();
  const findings = r.detections.filter((d) => d.decision !== 'KEEP');
  const byCategory = useMemo(() => {
    const m: Record<string, number> = {};
    for (const d of findings) {
      const cat = config?.entities.find((e) => e.id === d.entity_type)?.category ?? 'other';
      m[cat] = (m[cat] ?? 0) + 1;
    }
    return m;
  }, [findings, config]);

  if (r.status !== 'ok') {
    return (
      <Alert color="red" icon={<IconShieldX size={20} />} title="Not processed - do not share this text">
        The engine failed closed ({r.failure?.failure_class as string} at {r.failure?.stage as string}): no
        redacted copy is produced when any check cannot complete. Code:{' '}
        <Code>{r.failure?.code as string}</Code>
      </Alert>
    );
  }
  return (
    <Stack>
      {stale && (
        <Alert color="yellow" icon={<IconAlertTriangle size={18} />}>
          The text has changed since this result was produced. Run Redact again before using it.
        </Alert>
      )}
      {r.notices.map((n) => (
        <Alert key={n} color="blue" variant="light">
          {n}
        </Alert>
      ))}
      {r.guard &&
        (r.guard.allowed ? (
          <Alert color="green" icon={<IconShieldCheck size={20} />} title="Ready to share">
            The independent double-check found no remaining identifiers in the redacted copy.
          </Alert>
        ) : (
          <Alert color="red" icon={<IconShieldX size={20} />} title="Do not share yet">
            The double-check still found {r.guard.hits.filter((h) => h.blocking).length} identifier(s) in the
            redacted copy:{' '}
            {[
              ...new Set(r.guard.hits.filter((h) => h.blocking).map((h) => friendlyEntity(h.entity_type))),
            ].join(', ')}
            . Review the text manually or use “Category labels”.
          </Alert>
        ))}
      <Group gap="xs">
        <Text fw={600}>{findings.length} finding(s)</Text>
        {Object.entries(byCategory).map(([c, n]) => (
          <Badge key={c} color={CATEGORY_COLOR[c] ?? 'gray'} variant="light">
            {CATEGORY_LABEL[c] ?? c}: {n}
          </Badge>
        ))}
        {findings.some((d) => d.decision === 'REVIEW') && (
          <Badge color="yellow">includes items needing review</Badge>
        )}
      </Group>
      <Tabs defaultValue="redacted" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="redacted">Redacted copy</Tabs.Tab>
          <Tabs.Tab value="highlight">Highlighted original</Tabs.Tab>
          <Tabs.Tab value="findings">Findings ({findings.length})</Tabs.Tab>
          {technical && <Tabs.Tab value="technical">Technical</Tabs.Tab>}
          {props.canRestore && r.vault_id && <Tabs.Tab value="restore">Re-identify a reply</Tabs.Tab>}
        </Tabs.List>
        <Tabs.Panel value="redacted" pt="sm">
          <Card withBorder radius="md">
            <Group justify="flex-end" mb="xs" gap="xs">
              <CopyButton value={r.sanitized_text ?? ''}>
                {({ copied, copy }) => (
                  <Button size="xs" variant="light" leftSection={<IconCopy size={14} />} onClick={copy}>
                    {copied ? 'Copied' : 'Copy'}
                  </Button>
                )}
              </CopyButton>
              <Button
                size="xs"
                variant="light"
                leftSection={<IconDownload size={14} />}
                onClick={() => {
                  const url = URL.createObjectURL(new Blob([r.sanitized_text ?? ''], { type: 'text/plain' }));
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = 'redacted.txt';
                  a.click();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}
              >
                Download
              </Button>
            </Group>
            <ScrollArea.Autosize mah={520}>
              <pre className="output" data-testid="sanitized-text">
                {r.sanitized_text}
              </pre>
            </ScrollArea.Autosize>
          </Card>
        </Tabs.Panel>
        <Tabs.Panel value="highlight" pt="sm">
          <Card withBorder radius="md">
            <Group justify="space-between" mb="xs">
              <Group gap={6}>
                {Object.keys(CATEGORY_COLOR).map((c) => (
                  <Badge key={c} color={CATEGORY_COLOR[c]} variant="light" size="sm">
                    {CATEGORY_LABEL[c]}
                  </Badge>
                ))}
              </Group>
              <Switch
                size="xs"
                label="Mask values"
                checked={maskValues}
                onChange={(e) => props.setMask(e.currentTarget.checked)}
              />
            </Group>
            <ScrollArea.Autosize mah={520}>
              <HighlightedText
                text={analysis.text}
                detections={r.detections}
                catalog={config?.entities}
                masked={maskValues}
                selectedId={props.selected?.id}
                onSelect={props.onSelect}
              />
            </ScrollArea.Autosize>
            <Text size="xs" c="dimmed" mt="xs">
              Click a highlighted item to see why it was found. Dashed outline = flagged for review (still
              redacted).
            </Text>
          </Card>
        </Tabs.Panel>
        <Tabs.Panel value="findings" pt="sm">
          <FindingsTable
            analysis={analysis}
            masked={maskValues}
            technical={technical}
            onSelect={props.onSelect}
          />
        </Tabs.Panel>
        {technical && (
          <Tabs.Panel value="technical" pt="sm">
            <TechnicalPanel r={r} />
          </Tabs.Panel>
        )}
        {props.canRestore && r.vault_id && (
          <Tabs.Panel value="restore" pt="sm">
            <RestorePanel vaultId={r.vault_id} />
          </Tabs.Panel>
        )}
      </Tabs>
    </Stack>
  );
}

function FindingsTable({
  analysis,
  masked,
  technical,
  onSelect,
}: {
  analysis: Analysis;
  masked: boolean;
  technical: boolean;
  onSelect: (d: DetectionOut) => void;
}) {
  const { data: config } = useConfig();
  const rows = analysis.result.detections;
  if (!rows.length) return <EmptyState icon={<IconShieldCheck />} title="No identifiers found" />;
  return (
    <Card withBorder radius="md" p={0}>
      <ScrollArea>
        <Table striped highlightOnHover fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Type</Table.Th>
              <Table.Th>Value</Table.Th>
              <Table.Th>Outcome</Table.Th>
              <Table.Th>Confidence</Table.Th>
              <Table.Th>Found by</Table.Th>
              {technical && <Table.Th>Score / threshold</Table.Th>}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((d) => {
              const v = analysis.text.slice(d.start, d.end);
              return (
                <Table.Tr key={d.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(d)}>
                  <Table.Td>
                    <Badge color={entityColor(d.entity_type, config?.entities)} variant="light">
                      {friendlyEntity(d.entity_type)}
                    </Badge>
                  </Table.Td>
                  <Table.Td ff="monospace">{masked ? mask(v) : v}</Table.Td>
                  <Table.Td>
                    <Badge color={DECISION_COLOR[d.decision] ?? 'gray'} variant="dot">
                      {d.decision === 'REDACT'
                        ? 'Redacted'
                        : d.decision === 'REVIEW'
                          ? 'Redacted · review'
                          : 'Kept'}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={CONFIDENCE_COLOR[d.confidence_class] ?? 'gray'} variant="light">
                      {d.confidence_class}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{d.sources.join(', ')}</Table.Td>
                  {technical && (
                    <Table.Td ff="monospace">
                      {d.decision_score.toFixed(2)} / {d.threshold ?? '-'}
                    </Table.Td>
                  )}
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </ScrollArea>
    </Card>
  );
}

function TechnicalPanel({ r }: { r: AnalyzeOut }) {
  const t = r.timings as Record<string, number>;
  return (
    <Stack>
      <Card withBorder radius="md">
        <Title order={6} mb="xs">
          Timing (ms)
        </Title>
        <KeyValue
          rows={[
            'normalization_ms',
            'recognizer_ms',
            'model_ms',
            'policy_ms',
            'reconciliation_ms',
            'redaction_ms',
            'total_ms',
          ].map((k) => [k.replace('_ms', ''), (t[k] ?? 0).toFixed(1)])}
        />
      </Card>
      <Card withBorder radius="md">
        <Title order={6} mb="xs">
          Provenance
        </Title>
        <KeyValue
          rows={[
            ['Request id', <Code key="r">{r.request_id}</Code>],
            ['Config hash', <Code key="c">{r.config_sha256}</Code>],
            ['Operator', r.operator ?? '-'],
            ['Candidates', String((r.summary as Record<string, unknown>).candidates)],
            [
              'Stamps',
              <Code key="s" block>
                {JSON.stringify(r.stamps, null, 1)}
              </Code>,
            ],
          ]}
        />
      </Card>
      {r.evidence.length > 0 && (
        <Card withBorder radius="md" p={0}>
          <ScrollArea>
            <Table fz="xs" striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Span</Table.Th>
                  <Table.Th>Entity</Table.Th>
                  <Table.Th>Recognizer</Table.Th>
                  <Table.Th>Score</Table.Th>
                  <Table.Th>Threshold (source)</Table.Th>
                  <Table.Th>Outcome</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.evidence.map((e) => (
                  <Table.Tr key={e.id}>
                    <Table.Td ff="monospace">
                      {e.start}-{e.end}
                      {e.view !== 'original' ? ` (${e.view})` : ''}
                    </Table.Td>
                    <Table.Td>{e.entity_type}</Table.Td>
                    <Table.Td ff="monospace">{e.recognizer_id}</Table.Td>
                    <Table.Td ff="monospace">
                      {e.raw_score.toFixed(3)} {e.score_type}
                    </Table.Td>
                    <Table.Td ff="monospace">
                      {e.threshold_used ?? '-'} {e.threshold_source ? `(${e.threshold_source})` : ''}
                    </Table.Td>
                    <Table.Td>
                      {e.accepted ? (
                        <Badge color="green" size="xs">
                          accepted
                        </Badge>
                      ) : (
                        <Badge color="gray" size="xs">
                          {e.rejection_reason}
                        </Badge>
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

function RestorePanel({ vaultId }: { vaultId: string }) {
  const [reply, setReply] = useState('');
  const restore = useRestore();
  return (
    <Card withBorder radius="md">
      <Alert color="orange" variant="light" mb="sm" icon={<IconAlertTriangle size={18} />}>
        Re-identification puts real values back. Use it only inside the protected environment, for the person
        this text is about. Every use is recorded in the audit trail.
      </Alert>
      <Textarea
        label="Paste a reply that contains placeholders (e.g. an assistant's answer)"
        autosize
        minRows={4}
        value={reply}
        onChange={(e) => setReply(e.currentTarget.value)}
      />
      <Group mt="sm">
        <Button
          onClick={() => restore.mutate({ vault_id: vaultId, text: reply })}
          loading={restore.isPending}
          disabled={!reply.trim()}
        >
          Put original values back
        </Button>
      </Group>
      <ErrorAlert error={restore.error} />
      {restore.data && (
        <Stack mt="sm" gap={4}>
          <Text size="sm" c="dimmed">
            {restore.data.replaced} placeholder(s) replaced
          </Text>
          <pre className="output">{restore.data.text}</pre>
        </Stack>
      )}
    </Card>
  );
}

function FindingDrawer({
  analysis,
  detection,
  onClose,
  masked,
  technical,
}: {
  analysis: Analysis;
  detection: DetectionOut | null;
  onClose: () => void;
  masked: boolean;
  technical: boolean;
}) {
  const { data: config } = useConfig();
  const d = detection;
  const info = d ? config?.entities.find((e) => e.id === d.entity_type) : undefined;
  const value = d ? analysis.text.slice(d.start, d.end) : '';
  const related = d ? analysis.result.evidence.filter((e) => e.start < d.end && d.start < e.end) : [];
  return (
    <Drawer opened={!!d} onClose={onClose} position="right" size="md" title="Why was this found?">
      {d && (
        <Stack>
          <Group>
            <Badge size="lg" color={entityColor(d.entity_type, config?.entities)}>
              {friendlyEntity(d.entity_type)}
            </Badge>
            <Badge color={CONFIDENCE_COLOR[d.confidence_class]} variant="light">
              {d.confidence_class} confidence
            </Badge>
          </Group>
          <Code block>{masked ? mask(value) : value}</Code>
          {info && <Text size="sm">{info.description}</Text>}
          <KeyValue
            rows={[
              [
                'Outcome',
                d.decision === 'REDACT'
                  ? 'Redacted'
                  : d.decision === 'REVIEW'
                    ? 'Redacted, flagged for review'
                    : 'Kept',
              ],
              ['Found by', d.sources.join(', ')],
              ['Risk tier', info?.risk_tier ?? '-'],
              ['HIPAA Safe Harbor', info?.safe_harbor.join(', ') || '-'],
              ['Reason', d.reason],
              ...(technical
                ? ([
                    ['Recognizers', <Code key="r">{d.recognizers.join(', ')}</Code>],
                    ['Score / threshold', `${d.decision_score} / ${d.threshold ?? '-'}`],
                    ['Offsets', `${d.start}-${d.end}`],
                  ] as [string, React.ReactNode][])
                : []),
            ]}
          />
          {technical && related.length > 0 && (
            <>
              <Title order={6}>All evidence for this span</Title>
              {related.map((e) => (
                <Card key={e.id} withBorder p="xs">
                  <Group justify="space-between">
                    <Code>{e.recognizer_id}</Code>
                    {e.accepted ? (
                      <Badge color="green" size="xs">
                        accepted
                      </Badge>
                    ) : (
                      <Badge color="gray" size="xs">
                        {e.rejection_reason}
                      </Badge>
                    )}
                  </Group>
                  <Text size="xs" c="dimmed">
                    {e.entity_type} · {e.raw_score.toFixed(3)} {e.score_type} · threshold{' '}
                    {e.threshold_used ?? '-'}
                    {e.threshold_source ? ` (${e.threshold_source})` : ''}
                  </Text>
                </Card>
              ))}
            </>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
