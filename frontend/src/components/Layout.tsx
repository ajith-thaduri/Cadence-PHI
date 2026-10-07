import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
  Group,
  Menu,
  NavLink,
  ScrollArea,
  SegmentedControl,
  Text,
  Tooltip,
  useMantineColorScheme,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconActivity,
  IconBook,
  IconChecklist,
  IconFiles,
  IconGauge,
  IconHeartbeat,
  IconHome,
  IconLogout,
  IconMoon,
  IconRocket,
  IconSettings,
  IconShieldCheck,
  IconSun,
  IconUser,
  IconWand,
} from '@tabler/icons-react';
import { Link, Outlet, useLocation } from 'react-router';
import { useLogout, useStatus } from '../api/hooks';
import { usePrefs } from '../lib/prefs';

const NAV = [
  { to: '/', label: 'Overview', icon: IconHome, section: 'Use' },
  { to: '/redact', label: 'Redact text', icon: IconWand, section: 'Use' },
  { to: '/batch', label: 'Batch files', icon: IconFiles, section: 'Use' },
  { to: '/checks', label: 'Checks & evaluations', icon: IconChecklist, section: 'Assure' },
  { to: '/results', label: 'Results', icon: IconGauge, section: 'Assure' },
  { to: '/gates', label: 'Release readiness', icon: IconRocket, section: 'Assure' },
  { to: '/monitoring', label: 'Monitoring', icon: IconHeartbeat, section: 'Operate' },
  { to: '/config', label: 'Configuration', icon: IconSettings, section: 'Operate' },
  { to: '/activity', label: 'Activity & audit', icon: IconActivity, section: 'Operate' },
  { to: '/docs', label: 'Help & documentation', icon: IconBook, section: 'Help' },
];

const ENV_COLOR: Record<string, string> = { prod: 'red', dev: 'blue', test: 'gray', base: 'teal' };

export function Layout() {
  const [opened, { toggle, close }] = useDisclosure();
  const { pathname } = useLocation();
  const { data: status } = useStatus();
  const { prefs, set } = usePrefs();
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const logout = useLogout();
  const ready = status?.engine.ready;
  const sections = [...new Set(NAV.map((n) => n.section))];

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 250, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="lg"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger
              opened={opened}
              onClick={toggle}
              hiddenFrom="sm"
              size="sm"
              aria-label="Toggle navigation"
            />
            <IconShieldCheck size={26} color="var(--mantine-color-blue-filled)" />
            <Text fw={700} visibleFrom="xs">
              PHI Engine Console
            </Text>
            {status && (
              <Tooltip label="Engine configuration environment">
                <Badge color={ENV_COLOR[status.engine.environment] ?? 'gray'} variant="light">
                  {status.engine.environment}
                </Badge>
              </Tooltip>
            )}
            <Badge
              variant="dot"
              color={ready ? 'green' : status?.engine.error ? 'red' : 'yellow'}
              title={
                ready ? 'Engine ready' : status?.engine.error ? 'Engine failed to start' : 'Engine starting'
              }
            >
              {ready ? 'ready' : status?.engine.error ? 'failed' : 'starting'}
            </Badge>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Tooltip label="Simple view hides scores, recognizer ids and timings">
              <SegmentedControl
                size="xs"
                value={prefs.technical ? 'technical' : 'simple'}
                onChange={(v) => set({ technical: v === 'technical' })}
                data={[
                  { label: 'Simple', value: 'simple' },
                  { label: 'Technical', value: 'technical' },
                ]}
                visibleFrom="xs"
              />
            </Tooltip>
            <ActionIcon
              variant="subtle"
              aria-label="Toggle colour scheme"
              onClick={() => setColorScheme(colorScheme === 'dark' ? 'light' : 'dark')}
            >
              {colorScheme === 'dark' ? <IconSun size={18} /> : <IconMoon size={18} />}
            </ActionIcon>
            {status && (
              <Menu position="bottom-end" withinPortal>
                <Menu.Target>
                  <ActionIcon variant="subtle" aria-label="Account">
                    <IconUser size={18} />
                  </ActionIcon>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>
                    {status.user.name} · {status.user.role}
                  </Menu.Label>
                  {status.auth_mode !== 'none' && (
                    <Menu.Item leftSection={<IconLogout size={14} />} onClick={() => logout.mutate()}>
                      Sign out
                    </Menu.Item>
                  )}
                  {status.auth_mode === 'none' && <Menu.Item disabled>Local single-user mode</Menu.Item>}
                </Menu.Dropdown>
              </Menu>
            )}
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        <ScrollArea>
          {sections.map((s) => (
            <div key={s}>
              <Text size="xs" c="dimmed" fw={700} tt="uppercase" px="sm" mt="sm" mb={4}>
                {s}
              </Text>
              {NAV.filter((n) => n.section === s).map((n) => (
                <NavLink
                  key={n.to}
                  component={Link}
                  to={n.to}
                  label={n.label}
                  leftSection={<n.icon size={18} />}
                  active={n.to === '/' ? pathname === '/' : pathname.startsWith(n.to)}
                  onClick={close}
                />
              ))}
            </div>
          ))}
        </ScrollArea>
      </AppShell.Navbar>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
