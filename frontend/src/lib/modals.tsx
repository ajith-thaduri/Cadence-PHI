// Minimal confirmation dialog without an extra dependency: renders a Mantine Modal into a detached root.
import { Button, Group, MantineProvider, Modal, Text } from '@mantine/core';
import { createRoot } from 'react-dom/client';
import { theme } from '../theme';

function confirm(title: string, body: string, onConfirm: () => void) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  const close = () => {
    root.unmount();
    host.remove();
  };
  root.render(
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Modal opened onClose={close} title={title} centered>
        <Text size="sm">{body}</Text>
        <Group justify="flex-end" mt="lg">
          <Button variant="default" onClick={close}>
            Cancel
          </Button>
          <Button
            color="orange"
            onClick={() => {
              close();
              onConfirm();
            }}
          >
            Continue
          </Button>
        </Group>
      </Modal>
    </MantineProvider>,
  );
}

export const modals = { confirm };
