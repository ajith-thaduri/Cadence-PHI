import { Button } from '@mantine/core';
import { IconMap2 } from '@tabler/icons-react';
import { Link } from 'react-router';
import { EmptyState } from '../components/common';

export function NotFoundPage() {
  return (
    <EmptyState icon={<IconMap2 />} title="Page not found">
      <Button component={Link} to="/" variant="light" mt="sm">
        Back to the overview
      </Button>
    </EmptyState>
  );
}
