// Presentation helpers for entity types: colour by catalog category (always paired with a text label, never
// colour alone) and plain-language names for non-technical users.
import type { EntityInfo } from '../api/types';

export const CATEGORY_COLOR: Record<string, string> = {
  person: 'blue',
  contact: 'teal',
  identifier: 'red',
  date: 'grape',
  location: 'orange',
  organization: 'indigo',
  other: 'gray',
};

export const CATEGORY_LABEL: Record<string, string> = {
  person: 'People',
  contact: 'Contact details',
  identifier: 'Identifiers',
  date: 'Dates',
  location: 'Places',
  organization: 'Organisations',
  other: 'Other',
};

export function friendlyEntity(id: string): string {
  const special: Record<string, string> = {
    DOB: 'Date of birth',
    MRN: 'Medical record number',
    NPI: 'Provider NPI',
    SSN: 'Social Security number',
    URL: 'Web address',
    IP_ADDRESS: 'IP address',
    ZIP: 'ZIP code',
    AGE_OVER_89: 'Age over 89',
    LICENSE_CERT_ID: 'Licence / certificate number',
    VEHICLE_ID: 'Vehicle identifier',
    DEVICE_ID: 'Device identifier',
    OTHER_UNIQUE_ID: 'Other identifier',
    GEOGRAPHIC_CONTEXT: 'Place / landmark',
    GEO_COORDINATES: 'GPS coordinates',
  };
  if (special[id]) return special[id];
  const s = id.toLowerCase().replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function entityColor(id: string, catalog?: EntityInfo[]): string {
  const cat = catalog?.find((e) => e.id === id)?.category;
  return CATEGORY_COLOR[cat ?? 'other'] ?? 'gray';
}

export const MODALITIES: { value: string; label: string; hint: string }[] = [
  { value: 'typed', label: 'Typed text / chat', hint: 'Messages, emails, typed notes' },
  { value: 'note', label: 'Clinical / care note', hint: 'Care-management or clinical documentation' },
  {
    value: 'telephone',
    label: 'Phone call transcript',
    hint: 'Speech-to-text of a call - spoken numbers and dates are understood',
  },
  { value: 'dictation', label: 'Dictation', hint: 'Dictated notes' },
  { value: 'asr', label: 'Raw speech-to-text', hint: 'Unedited ASR output' },
  { value: 'face_to_face', label: 'In-person conversation', hint: 'Transcript of a face-to-face visit' },
  { value: 'imported_text', label: 'Imported document text', hint: 'Text extracted from another system' },
];

export const OPERATORS: { value: string; label: string; example: string; reversible: boolean }[] = [
  { value: 'TOKENIZE', label: 'Numbered placeholders', example: '[NAME_1], [PHONE_1]', reversible: true },
  { value: 'REDACT', label: 'Category labels', example: '[NAME], [PHONE]', reversible: false },
  { value: 'PSEUDONYMIZE', label: 'Keyed pseudonyms', example: 'NAME-3f9a2c', reversible: true },
  { value: 'ENCRYPT', label: 'Encrypted values', example: 'AES ciphertext', reversible: true },
];

export const DECISION_COLOR: Record<string, string> = { REDACT: 'red', REVIEW: 'yellow', KEEP: 'gray' };
export const CONFIDENCE_COLOR: Record<string, string> = { high: 'green', medium: 'yellow', low: 'orange' };
