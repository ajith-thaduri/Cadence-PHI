import { Card, Grid, Loader, NavLink, ScrollArea, Text } from '@mantine/core';
import { Link, useParams } from 'react-router';
import { useDoc, useDocsIndex } from '../api/hooks';
import { ErrorAlert, Md, PageHeader } from '../components/common';

export function DocsPage() {
  const { slug = 'ui-guide' } = useParams();
  const { data: index } = useDocsIndex();
  const { data: doc, error, isLoading } = useDoc(slug);
  return (
    <>
      <PageHeader
        title="Help & documentation"
        description="Guides and reference documents shipped with this repository."
      />
      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Card withBorder radius="md" p="xs">
            {(index ?? []).map((d) => (
              <NavLink
                key={d.slug}
                component={Link}
                to={`/docs/${d.slug}`}
                label={d.title}
                description={d.path}
                active={d.slug === slug}
              />
            ))}
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 9 }}>
          <ErrorAlert error={error} />
          <Card withBorder radius="md">
            {isLoading ? (
              <Loader />
            ) : doc ? (
              <ScrollArea.Autosize mah="calc(100vh - 220px)">
                <Text size="xs" c="dimmed" mb="xs">
                  {doc.path}
                </Text>
                <Md>{doc.markdown}</Md>
              </ScrollArea.Autosize>
            ) : null}
          </Card>
        </Grid.Col>
      </Grid>
    </>
  );
}
