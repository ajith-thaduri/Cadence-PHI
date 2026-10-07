import { useEffect, useState, type ReactNode } from 'react';
import { Button, Center, Loader, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconShieldLock } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { useLogin, useSession } from '../api/hooks';
import { ErrorAlert } from './common';

/** Shows the sign-in page when the console requires authentication and there is no valid session. */
export function SessionGate({ children }: { children: ReactNode }) {
  const { data, isLoading, error } = useSession();
  const qc = useQueryClient();
  useEffect(() => {
    const onUnauth = () => qc.invalidateQueries({ queryKey: ['session'] });
    window.addEventListener('phi-ui:unauthorized', onUnauth);
    return () => window.removeEventListener('phi-ui:unauthorized', onUnauth);
  }, [qc]);
  if (isLoading)
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  if (error)
    return (
      <Center h="100vh" p="md">
        <ErrorAlert error={error} title="The console server is not reachable" />
      </Center>
    );
  if (data && !data.user) return <Login mode={data.auth_mode} />;
  return <>{children}</>;
}

function Login({ mode }: { mode: string }) {
  const [username, setUsername] = useState('');
  const [secret, setSecret] = useState('');
  const login = useLogin();
  return (
    <Center h="100vh" p="md">
      <Paper withBorder shadow="sm" p="xl" radius="md" w={400}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            login.mutate({ username, secret });
          }}
        >
          <Stack>
            <Stack gap={4} align="center">
              <IconShieldLock size={36} />
              <Title order={3}>PHI Engine Console</Title>
              <Text size="sm" c="dimmed" ta="center">
                {mode === 'token'
                  ? 'Enter your name and the access token.'
                  : 'Sign in with your console account.'}
              </Text>
            </Stack>
            <ErrorAlert error={login.error} title="Sign-in failed" />
            <TextInput
              label={mode === 'token' ? 'Your name (for the audit trail)' : 'Username'}
              value={username}
              onChange={(e) => setUsername(e.currentTarget.value)}
              autoComplete="username"
              required={mode !== 'token'}
            />
            <PasswordInput
              label={mode === 'token' ? 'Access token' : 'Password'}
              value={secret}
              onChange={(e) => setSecret(e.currentTarget.value)}
              autoComplete="current-password"
              required
            />
            <Button type="submit" loading={login.isPending} fullWidth>
              Sign in
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
