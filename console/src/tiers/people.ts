// Instructors (`instructors.yml` in the semester's config repo): design/inputs.md "Teaching team".

import { EMAIL_RE } from '../edit/csv';
import type { Tiers } from './types';

const HANDLE_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;

const blank = (x: unknown) => !String(x ?? '').trim();

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
  title: { tier: 'default', label: 'Title', defaultLabel: 'optional', reason: 'Shown under the name on the student site.' },
  photo: { tier: 'advanced', label: 'Photo', placeholder: 'images/handle.jpg', reason: 'A path in the student site repo, or a link. Upload the image to the site repo’s images folder first.' },
  url: { tier: 'advanced', label: 'Web page', widget: 'url', placeholder: 'https://', reason: 'The name links here on the student site.' },
  start: { tier: 'advanced', label: 'From', widget: 'date', defaultLabel: 'optional', reason: 'Access starts on this day.' },
  end: { tier: 'advanced', label: 'Until', widget: 'date', defaultLabel: 'optional', reason: 'Access ends after this day.' },
  show_email: { tier: 'advanced', label: 'Show email on the student site', widget: 'checkbox', default: false },
};
