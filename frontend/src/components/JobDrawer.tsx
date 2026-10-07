import {
  Anchor,
  Button,
  Code,
  CopyButton,
  Drawer,
  Group,
  Progress,
  ScrollArea,
  Stack,
  Text,
} from '@mantine/core';
import { IconCopy, IconExternalLink, IconPlayerStop } from '@tabler/icons-react';
import { Link } from 'react-router';
import { useCancelJob, useJob, useJobLog } from '../api/hooks';
import { duration, when } from '../lib/format';
import { ErrorAlert, JobStatusBadge, KeyValue } from './common';

/** Live view of one job: status, the exact reproducible command(s), progress and the streaming log. */
export function JobDrawer({
  id,
  onClose,
  canCancel,
}: {
  id: string | null;
  onClose: () => void;
  canCancel: boolean;
}) {
  const { data: job } = useJob(id);
  const log = useJobLog(id);
  const cancel = useCancelJob();
  const active = job && (job.status === 'queued' || job.status === 'running');
  return (
    <Drawer opened={!!id} onClose={onClose} position="right" size="xl" title={job ? job.title : 'Job'}>
      {job && (
        <Stack>
          <Group justify="space-between">
            <JobStatusBadge status={job.status} />
            {canCancel && active && (
              <Button
                color="red"
                variant="light"
                size="xs"
                leftSection={<IconPlayerStop size={14} />}
                loading={cancel.isPending}
                onClick={() => cancel.mutate(job.id)}
              >
                Cancel
              </Button>
            )}
          </Group>
          <ErrorAlert error={cancel.error} />
          {job.progress && (
            <Progress
              value={(100 * job.progress.done) / Math.max(job.progress.total, 1)}
              striped
              animated={!!active}
            />
          )}
          <KeyValue
            rows={[
              ['Job id', <Code key="id">{job.id}</Code>],
              ['Started by', job.user],
              ['Created', when(job.created)],
              ['Duration', duration(job.started, job.finished)],
              ['Exit code', job.exit_code ?? '-'],
              ...(job.error ? ([['Error', job.error]] as [string, string][]) : []),
              ...(job.run_ref && job.status === 'succeeded'
                ? ([
                    [
                      'Results',
                      <Anchor
                        key="r"
                        component={Link}
                        to={`/results/${job.run_ref.kind}/${job.run_ref.name}`}
                      >
                        Open results <IconExternalLink size={12} />
                      </Anchor>,
                    ],
                  ] as [string, React.ReactNode][])
                : []),
            ]}
          />
          {job.commands.length > 0 && (
            <div>
              <Group justify="space-between" mb={4}>
                <Text size="sm" fw={600}>
                  Equivalent command (reproducible from the repository root)
                </Text>
                <CopyButton value={job.commands.join('\n')}>
                  {({ copied, copy }) => (
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      leftSection={<IconCopy size={12} />}
                      onClick={copy}
                    >
                      {copied ? 'Copied' : 'Copy'}
                    </Button>
                  )}
                </CopyButton>
              </Group>
              <Code block>{job.commands.join('\n')}</Code>
            </div>
          )}
          {job.type !== 'batch' && (
            <div>
              <Text size="sm" fw={600} mb={4}>
                Log
              </Text>
              <ScrollArea h={420} type="auto" className="log">
                <Code block>{log || (active ? 'Waiting for output…' : 'No output.')}</Code>
              </ScrollArea>
            </div>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
