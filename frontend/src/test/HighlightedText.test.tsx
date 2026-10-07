import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MantineProvider } from '@mantine/core';
import { HighlightedText } from '../components/HighlightedText';
import type { DetectionOut } from '../api/types';

const det = (
  id: string,
  start: number,
  end: number,
  entity_type: string,
  decision = 'REDACT',
): DetectionOut => ({
  id,
  entity_type,
  start,
  end,
  decision,
  confidence_class: 'high',
  decision_score: 0.99,
  threshold: 0.5,
  reason: 'test',
  recognizers: ['rule_phone'],
  sources: ['Pattern rules'],
  action: 'TOKENIZE',
  rejected: [],
});

function renderIt(masked = false, onSelect = vi.fn()) {
  render(
    <MantineProvider>
      <HighlightedText
        text="Call Maria at 415-555-0100."
        detections={[det('d1', 5, 10, 'NAME'), det('d2', 14, 26, 'PHONE'), det('d3', 0, 4, 'DATE', 'KEEP')]}
        masked={masked}
        onSelect={onSelect}
      />
    </MantineProvider>,
  );
  return onSelect;
}

describe('HighlightedText', () => {
  it('marks findings with a visible type label (not colour alone) and skips KEEP decisions', () => {
    renderIt();
    const marks = screen.getAllByRole('button');
    expect(marks).toHaveLength(2);
    expect(marks[0]).toHaveTextContent('MariaNAME');
    expect(marks[1]).toHaveTextContent('415-555-0100PHONE');
    expect(screen.getByTestId('highlighted-text')).toHaveTextContent('Call');
  });
  it('masks values when asked', () => {
    renderIt(true);
    expect(screen.getByTestId('highlighted-text')).not.toHaveTextContent('Maria');
  });
  it('selects a finding by click and keyboard', async () => {
    const onSelect = renderIt();
    const user = userEvent.setup();
    await user.click(screen.getAllByRole('button')[0]);
    screen.getAllByRole('button')[1].focus();
    await user.keyboard('{Enter}');
    expect(onSelect.mock.calls.map((c) => c[0].id)).toEqual(['d1', 'd2']);
  });
});
