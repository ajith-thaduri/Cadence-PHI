import type { ReactNode } from 'react';
import { Alert, Badge, Card, Group, Stack, Table, Text, ThemeIcon, Title, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { errorText } from '../api/client';
import type { JobStatus, Verdict } from '../api/types';

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <Group justify="space-between" align="flex-start" mb="lg" wrap="wrap" gap="sm">
      <Stack gap={4} maw={820}>
        <Title order={2}>{title}</Title>
        {description && (
          <Text c="dimmed" size="sm">
            {description}
          </Text>
        )}
      </Stack>
      {actions && <Group gap="xs">{actions}</Group>}
    </Group>
  );
}

export function ErrorAlert({
  error,
  title = 'Could not complete the request',
}: {
  error: unknown;
  title?: string;
}) {
  if (!error) return null;
  return (
    <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />} title={title} mb="md">
      {errorText(error)}
    </Alert>
  );
}

export function Hint({ children }: { children: ReactNode }) {
  return (
    <Alert color="blue" variant="light" icon={<IconInfoCircle size={18} />} mb="md">
      {children}
    </Alert>
  );
}

export function EmptyState({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <Stack align="center" gap="xs" py="xl">
      <ThemeIcon size={48} radius="xl" variant="light" color="gray">
        {icon}
      </ThemeIcon>
      <Text fw={600}>{title}</Text>
      {children && (
        <Text c="dimmed" size="sm" ta="center" maw={460}>
          {children}
        </Text>
      )}
    </Stack>
  );
}

const JOB_COLOR: Record<JobStatus, string> = {
  queued: 'gray',
  running: 'blue',
  succeeded: 'green',
  failed: 'red',
  cancelled: 'gray',
  interrupted: 'orange',
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <Badge color={JOB_COLOR[status]} variant={status === 'running' ? 'filled' : 'light'}>
      {status}
    </Badge>
  );
}

const VERDICT: Record<Verdict, { color: string; label: string }> = {
  PASS: { color: 'green', label: 'Pass' },
  FAIL: { color: 'red', label: 'Fail' },
  INSUFFICIENT_EVIDENCE: { color: 'yellow', label: 'Insufficient evidence' },
};

export function VerdictBadge({ verdict, size = 'md' }: { verdict: Verdict; size?: 'sm' | 'md' | 'lg' }) {
  const v = VERDICT[verdict] ?? { color: 'gray', label: verdict };
  return (
    <Badge color={v.color} size={size} variant="light">
      {v.label}
    </Badge>
  );
}

export function StatCard({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  color?: string;
}) {
  const body = (
    <Card withBorder padding="md" radius="md" h="100%">
      <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
        {label}
      </Text>
      <Text fw={700} size="xl" c={color} mt={4}>
        {value}
      </Text>
    </Card>
  );
  return hint ? (
    <Tooltip label={hint} multiline w={280}>
      {body}
    </Tooltip>
  ) : (
    body
  );
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <Table withRowBorders={false} verticalSpacing={4} fz="sm">
      <Table.Tbody>
        {rows.map(([k, v]) => (
          <Table.Tr key={k}>
            <Table.Td c="dimmed" w={220} style={{ verticalAlign: 'top' }}>
              {k}
            </Table.Td>
            <Table.Td>{v}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

export function Md({ children }: { children: string }) {
  // react-markdown never renders raw HTML from the source (safe by default)
  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]}>{children}</Markdown>
    </div>
  );
}
