import { Badge, Card, Code, Loader, ScrollArea, Table, Tabs, Text } from '@mantine/core';
import { useAudit, useLedger, useStatus } from '../api/hooks';
import { ErrorAlert, PageHeader } from '../components/common';
import { shortSha, when } from '../lib/format';

export function ActivityPage() {
  const { data: status } = useStatus();
  const canAudit = !!status?.permissions.view_audit;
  const ledger = useLedger();
  const audit = useAudit(canAudit);
  return (
    <>
      <PageHeader
        title="Activity & audit"
        description="The evaluation ledger records every evaluation with its exact code, configuration, data and whether held-out test data was accessed. The console audit trail records who did what (ids and counts only - never text)."
      />
      <Tabs defaultValue="ledger">
        <Tabs.List>
          <Tabs.Tab value="ledger">Evaluation ledger</Tabs.Tab>
          {canAudit && <Tabs.Tab value="audit">Console audit trail</Tabs.Tab>}
        </Tabs.List>
        <Tabs.Panel value="ledger" pt="md">
          <ErrorAlert error={ledger.error} />
          {ledger.isLoading ? (
            <Loader />
          ) : (
            <Card withBorder radius="md" p={0}>
              <ScrollArea>
                <Table fz="sm" striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>When</Table.Th>
                      <Table.Th>Command</Table.Th>
                      <Table.Th>Operator</Table.Th>
                      <Table.Th>Datasets</Table.Th>
                      <Table.Th>Test data</Table.Th>
                      <Table.Th>Config / code</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {(ledger.data ?? []).map((r) => (
                      <Table.Tr key={r.run_id}>
                        <Table.Td>{when(r.timestamp)}</Table.Td>
                        <Table.Td>{r.command}</Table.Td>
                        <Table.Td>{String(r.operator ?? '-')}</Table.Td>
                        <Table.Td>
                          {((r.datasets as { id: string }[]) ?? []).map((d) => d.id).join(', ') || '-'}
                        </Table.Td>
                        <Table.Td>
                          {r.test_access ? (
                            <Badge color="red">yes</Badge>
                          ) : (
                            <Badge color="gray" variant="light">
                              no
                            </Badge>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Code>{shortSha(r.config_sha256 as string)}</Code>{' '}
                          <Code>{shortSha(r.code_fingerprint as string)}</Code>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
            </Card>
          )}
        </Tabs.Panel>
        {canAudit && (
          <Tabs.Panel value="audit" pt="md">
            <ErrorAlert error={audit.error} />
            <Card withBorder radius="md" p={0}>
              <ScrollArea>
                <Table fz="sm" striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>When</Table.Th>
                      <Table.Th>User</Table.Th>
                      <Table.Th>Action</Table.Th>
                      <Table.Th>Result</Table.Th>
                      <Table.Th>Details</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {(audit.data ?? []).map((r, i) => {
                      const { ts, user, role, action, ok, ...rest } = r;
                      return (
                        <Table.Tr key={i}>
                          <Table.Td>{when(ts)}</Table.Td>
                          <Table.Td>
                            {user}{' '}
                            <Text span c="dimmed" size="xs">
                              ({role})
                            </Text>
                          </Table.Td>
                          <Table.Td>{action}</Table.Td>
                          <Table.Td>
                            {ok ? (
                              <Badge color="green" variant="light">
                                ok
                              </Badge>
                            ) : (
                              <Badge color="red">failed</Badge>
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Text size="xs" ff="monospace">
                              {Object.entries(rest)
                                .filter(([, v]) => v !== null && v !== undefined)
                                .map(
                                  ([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`,
                                )
                                .join('  ')}
                            </Text>
                          </Table.Td>
                        </Table.Tr>
                      );
                    })}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
            </Card>
          </Tabs.Panel>
        )}
      </Tabs>
    </>
  );
}
