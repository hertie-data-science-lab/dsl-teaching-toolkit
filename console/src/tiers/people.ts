// Staff (`classroom-config/people.yml`): design/inputs.md "Teaching team".

import { EMAIL_RE } from '../edit/csv';
import type { Tiers } from './types';

const HANDLE_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;

export const PERSON: Tiers = {
  github_handle: { tier: 'ask', label: 'GitHub handle', reason: 'Checked: the account must exist.', check: (x) => (!x ? 'Needed.' : HANDLE_RE.test(String(x)) ? null : 'Not a GitHub handle.') },
  email: { tier: 'ask', label: 'Email', widget: 'email', reason: 'Problem emails go here.', check: (x) => (!x ? 'Needed.' : EMAIL_RE.test(String(x)) ? null : 'Not an email address.') },
  role: {
    tier: 'ask', label: 'Role', widget: 'radio', reason: 'Both get the instructor buttons for this cohort.',
    options: [{ value: 'instructor', label: 'Instructor' }, { value: 'ta', label: 'Teaching assistant' }],
  },
  name: { tier: 'default', label: 'Display name', defaultLabel: 'default: from GitHub' },
  title: { tier: 'default', label: 'Title', defaultLabel: 'optional', reason: 'Shown under the name on the student site.' },
  photo: { tier: 'advanced', label: 'Photo', placeholder: 'images/handle.jpg', reason: 'A path in the student site repo, or a link. Upload the image to the site repo’s images folder first.' },
  url: { tier: 'advanced', label: 'Web page', widget: 'url', placeholder: 'https://', reason: 'The name links here on the student site.' },
  start: { tier: 'advanced', label: 'From', widget: 'date', defaultLabel: 'optional', reason: 'Access starts on this day.' },
  end: { tier: 'advanced', label: 'Until', widget: 'date', defaultLabel: 'optional', reason: 'Access ends after this day.' },
  show_email: { tier: 'advanced', label: 'Show email on the student site', widget: 'checkbox', default: false },
};
