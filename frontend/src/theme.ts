import { createTheme } from '@mantine/core';

export const theme = createTheme({
  primaryColor: 'blue',
  defaultRadius: 'md',
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  fontFamilyMonospace: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  headings: { fontWeight: '650' },
  components: {
    Button: { defaultProps: { radius: 'md' } },
    Card: { defaultProps: { shadow: 'xs' } },
    Tooltip: { defaultProps: { withArrow: true, openDelay: 250 } },
  },
});
