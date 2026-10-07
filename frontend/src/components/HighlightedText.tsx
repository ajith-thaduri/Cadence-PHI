import { Box, Tooltip } from '@mantine/core';
import type { DetectionOut, EntityInfo } from '../api/types';
import { entityColor, friendlyEntity } from '../lib/entities';
import { mask, segments, type Span } from '../lib/segments';

interface Props {
  text: string;
  detections: DetectionOut[];
  catalog?: EntityInfo[];
  masked?: boolean;
  selectedId?: string | null;
  onSelect?: (d: DetectionOut) => void;
}

type DSpan = Span & { d: DetectionOut };

/** The ORIGINAL text with each finding marked and labelled (label text, not colour alone). */
export function HighlightedText({ text, detections, catalog, masked, selectedId, onSelect }: Props) {
  const spans: DSpan[] = detections
    .filter((d) => d.decision !== 'KEEP')
    .map((d) => ({ start: d.start, end: d.end, key: d.id, d }));
  return (
    <Box className="highlighted" data-testid="highlighted-text">
      {segments(text, spans).map((seg, i) => {
        if (!seg.span) return <span key={i}>{seg.text}</span>;
        const d = seg.span.d;
        const color = entityColor(d.entity_type, catalog);
        return (
          <Tooltip
            key={i}
            label={`${friendlyEntity(d.entity_type)} · ${d.decision === 'REVIEW' ? 'needs review' : 'redacted'} · ${d.confidence_class} confidence`}
          >
            <mark
              className={`finding${d.decision === 'REVIEW' ? ' review' : ''}${selectedId === d.id ? ' selected' : ''}`}
              style={{
                background: `var(--mantine-color-${color}-light)`,
                borderColor: `var(--mantine-color-${color}-filled)`,
              }}
              tabIndex={0}
              role="button"
              aria-label={`${friendlyEntity(d.entity_type)} finding`}
              onClick={() => onSelect?.(d)}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect?.(d)}
            >
              {masked ? mask(seg.text) : seg.text}
              <span className="finding-label" style={{ color: `var(--mantine-color-${color}-filled)` }}>
                {d.entity_type}
              </span>
            </mark>
          </Tooltip>
        );
      })}
    </Box>
  );
}
