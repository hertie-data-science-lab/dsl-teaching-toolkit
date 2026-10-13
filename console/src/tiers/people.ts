// Instructors (`instructors.yml` in the semester's config repo): design/inputs.md "Teaching team".

import { EMAIL_RE } from '../edit/csv';
import { blank } from '../edit/yamlText';
import { kebab } from '../model/format';
import { HANDLE_RE } from '../model/policy';
import type { Tiers } from './types';

/**
 * A person with a name and no handle is display only: the engine gives them a card on the
 * student site and nothing else (`sync_faculty`), so the handle and email are not needed.
 * The console's instructors.schema.json still requires both; the engine reads the file.
 */
export function displayOnly(v: Record<string, unknown>): boolean {
  return blank(v.github_handle) && !blank(v.name);
}

export const PERSON: Tiers = {
  github_handle: { tier: 'ask', label: 'GitHub handle', reason: 'Checked: the account must exist. Leave it blank, with a name, for a card on the student site only.', check: (x, v) => (blank(x) ? (displayOnly(v) ? null : 'Needed, or a display name for a card only.') : HANDLE_RE.test(String(x)) ? null : 'Not a GitHub handle.') },
  email: { tier: 'ask', label: 'Email', widget: 'email', reason: 'Problem emails go here.', check: (x, v) => (blank(x) ? (displayOnly(v) ? null : 'Needed.') : EMAIL_RE.test(String(x)) ? null : 'Not an email address.') },
  role: {
    tier: 'ask', label: 'Role', widget: 'radio', reason: 'Both get the instructor buttons for this semester.',
    options: [{ value: 'instructor', label: 'Instructor' }, { value: 'teaching_assistant', label: 'Teaching assistant' }],
  },
  name: { tier: 'default', label: 'Display name', defaultLabel: 'default: from GitHub' },
  title: { tier: 'default', label: 'Title', defaultLabel: 'optional', placeholder: 'Professor · Lecturer · Teaching assistant', reason: 'Shown under the name on the student site.' },
  photo: { tier: 'advanced', label: 'Photo', placeholder: '/_images/pp/handle.jpg or https://…', reason: 'A URL link, or a path in the student site repo.' },
  url: { tier: 'advanced', label: 'Web page', widget: 'url', placeholder: 'https://', reason: 'The name links here on the student site.' },
  start: { tier: 'advanced', label: 'From', widget: 'date', defaultLabel: 'optional', reason: 'Access starts on this day.' },
  end: { tier: 'advanced', label: 'Until', widget: 'date', defaultLabel: 'optional', reason: 'Access ends after this day.' },
  show_email: { tier: 'advanced', label: 'Show email on the student site', widget: 'checkbox', default: false },
};

/** Where uploaded instructor photos go in the semester's site repo (`templates/semester-config/instructors.yml`). */
export const PHOTO_DIR = '_images/pp';

const PHOTO_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
const PHOTO_MAX = 2 * 1024 * 1024;

/** Why an image cannot be uploaded as a photo, or null. */
export function photoError(f: { type: string; size: number }): string | null {
  if (!PHOTO_TYPES.includes(f.type)) return 'Not uploaded: a photo must be a PNG, JPEG or WebP image.';
  if (f.size > PHOTO_MAX) return 'Not uploaded: the image is over 2 MB.';
  return null;
}

/** An uploaded photo's file name: the stem kebab-cased, the extension lowercased (` Jane Doe (1).JPG` -> `jane-doe-1.jpg`). */
export function photoName(file: string): string {
  const dot = file.lastIndexOf('.');
  const stem = kebab(dot > 0 ? file.slice(0, dot) : file) || 'photo';
  return dot > 0 ? `${stem}.${file.slice(dot + 1).toLowerCase()}` : stem;
}
