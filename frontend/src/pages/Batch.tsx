import { useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Grid,
  Group,
  List,
  Progress,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import { IconDownload, IconFileUpload, IconFiles, IconX } from '@tabler/icons-react';
import { download, errorText } from '../api/client';
import { useBatch, useBatchSummary, useJob, useJobs, useStatus } from '../api/hooks';
import { ErrorAlert, EmptyState, Hint, JobStatusBadge, PageHeader } from '../components/common';
import { MODALITIES, OPERATORS } from '../lib/entities';
import { when } from '../lib/format';

const OK_EXT = ['.txt', '.text', '.md', '.jsonl', '.json'];

export function BatchPage() {
  const { data: status } = useStatus();
  const [files, setFiles] = useState<File[]>([]);
  const [modality, setModality] = useState('typed');
  const [operator, setOperator] = useState('TOKENIZE');
  const [jobId, setJobId] = useState<string | null>(null);
  const batch = useBatch();
  const { data: job } = useJob(jobId);
  const { data: jobs } = useJobs();
  const done = job?.status === 'succeeded';
  const { data: summary } = useBatchSummary(jobId, done);
  const maxMb = status?.limits.max_upload_mb ?? 20;
  const totalMb = files.reduce((s, f) => s + f.size, 0) / 2 ** 20;

  const submit = () => {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f, f.name));
    fd.append('modality', modality);
    fd.append('operator', operator);
    batch.mutate(fd, {
      onSuccess: (j) => {
        setJobId(j.id);
        setFiles([]);
      },
    });
  };

  return (
    <>
      <PageHeader
        title="Batch files"
        description="Redact many documents at once. Upload .txt files (one document each) or .jsonl / .json files with a “text” field per record. You get a zip with the redacted copies and a summary; original text is not kept."
      />
      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack>
            <Dropzone
              onDrop={(fs) =>
                setFiles((cur) => [
                  ...cur,
                  ...fs.filter((f) => OK_EXT.some((e) => f.name.toLowerCase().endsWith(e))),
                ])
              }
              onReject={() => notifications.show({ color: 'red', message: 'Some files were rejected' })}
              maxSize={maxMb * 2 ** 20}
              multiple
              disabled={!status?.permissions.batch}
            >
              <Stack align="center" gap={4} py="lg" style={{ pointerEvents: 'none' }}>
                <IconFileUpload size={40} stroke={1.5} />
                <Text fw={600}>Drop files here or click to choose</Text>
                <Text size="xs" c="dimmed">
                  .txt, .jsonl or .json · up to {maxMb} MB in total · {status?.limits.max_batch_files ?? 200}{' '}
                  documents
                </Text>
              </Stack>
            </Dropzone>
            {files.length > 0 && (
              <Card withBorder radius="md">
                <Group justify="space-between" mb="xs">
                  <Text fw={600}>
                    {files.length} file(s) · {totalMb.toFixed(2)} MB
                  </Text>
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="gray"
                    leftSection={<IconX size={12} />}
                    onClick={() => setFiles([])}
                  >
                    Clear
                  </Button>
                </Group>
                <List size="sm" spacing={2}>
                  {files.slice(0, 12).map((f, i) => (
                    <List.Item key={i}>{f.name}</List.Item>
                  ))}
                  {files.length > 12 && <List.Item>… and {files.length - 12} more</List.Item>}
                </List>
              </Card>
            )}
            <Card withBorder radius="md">
              <Stack gap="sm">
                <Select
                  label="Default text type"
                  description="Used when a record does not say (JSONL records may carry a “modality” field)."
                  data={MODALITIES.map((m) => ({ value: m.value, label: m.label }))}
                  value={modality}
                  onChange={(v) => v && setModality(v)}
                  allowDeselect={false}
                />
                <Select
                  label="Replacement style"
                  data={OPERATORS.map((o) => ({ value: o.value, label: `${o.label} (${o.example})` }))}
                  value={operator}
                  onChange={(v) => v && setOperator(v)}
                  allowDeselect={false}
                />
              </Stack>
            </Card>
            <ErrorAlert error={batch.error} />
            <Button
              size="md"
              leftSection={<IconFiles size={18} />}
              onClick={submit}
              loading={batch.isPending}
              disabled={!files.length || totalMb > maxMb || !status?.permissions.batch}
            >
              Redact {files.length || ''} file(s)
            </Button>
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 7 }}>
          {!job ? (
            <Card withBorder radius="md">
              <EmptyState icon={<IconFiles />} title="No batch running">
                Results of the batch you start appear here. Earlier batches are listed below.
              </EmptyState>
            </Card>
          ) : (
            <Card withBorder radius="md">
              <Group justify="space-between" mb="sm">
                <Title order={5}>Batch {job.id}</Title>
                <JobStatusBadge status={job.status} />
              </Group>
              {job.progress && (
                <Progress
                  value={(100 * job.progress.done) / Math.max(job.progress.total, 1)}
                  animated={job.status === 'running'}
                  mb="xs"
                />
              )}
              <Text size="sm" c="dimmed" mb="sm">
                {job.progress
                  ? `${job.progress.done} of ${job.progress.total} documents`
                  : 'Waiting to start…'}
              </Text>
              {done && (
                <Button
                  leftSection={<IconDownload size={16} />}
                  onClick={() =>
                    download(`/jobs/${job.id}/download`, `redacted_${job.id}.zip`).catch((e) =>
                      notifications.show({ color: 'red', message: errorText(e) }),
                    )
                  }
                  mb="sm"
                >
                  Download results (.zip)
                </Button>
              )}
              {summary && <SummaryTable rows={summary.rows} />}
            </Card>
          )}
          <Hint>
            The zip contains <b>sanitized/</b> (redacted text only), <b>detections.jsonl</b> (positions and
            types - no text) and <b>summary.csv</b>. Treat it as you would any de-identified output: check the
            summary for documents that were not processed.
          </Hint>
          <PreviousBatches jobs={(jobs ?? []).filter((j) => j.type === 'batch')} onOpen={setJobId} />
        </Grid.Col>
      </Grid>
    </>
  );
}

function SummaryTable({ rows }: { rows: Record<string, string>[] }) {
  const notOk = rows.filter((r) => r.status !== 'ok').length;
  return (
    <>
      {notOk > 0 && (
        <Badge color="red" mb="xs">
          {notOk} document(s) not processed - their redacted copies are absent
        </Badge>
      )}
      <ScrollArea h={360}>
        <Table striped fz="sm" stickyHeader>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Document</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Characters</Table.Th>
              <Table.Th>Findings</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={r.document}>
                <Table.Td>{r.document}</Table.Td>
                <Table.Td>
                  <Badge color={r.status === 'ok' ? 'green' : 'red'} variant="light">
                    {r.status}
                    {r.failure ? ` · ${r.failure}` : ''}
                  </Badge>
                </Table.Td>
                <Table.Td>{r.characters}</Table.Td>
                <Table.Td>{r.detections}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
    </>
  );
}

function PreviousBatches({
  jobs,
  onOpen,
}: {
  jobs: { id: string; created: string; status: string; params: Record<string, unknown> }[];
  onOpen: (id: string) => void;
}) {
  if (!jobs.length) return null;
  return (
    <Card withBorder radius="md">
      <Title order={6} mb="xs">
        Earlier batches
      </Title>
      <Table fz="sm" highlightOnHover>
        <Table.Tbody>
          {jobs.slice(0, 15).map((j) => (
            <Table.Tr key={j.id} style={{ cursor: 'pointer' }} onClick={() => onOpen(j.id)}>
              <Table.Td>{when(j.created)}</Table.Td>
              <Table.Td>{String(j.params.documents ?? '?')} documents</Table.Td>
              <Table.Td>
                <Badge variant="light">{j.status}</Badge>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Card>
  );
}
