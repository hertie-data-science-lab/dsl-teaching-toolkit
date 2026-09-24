// Course details (`.github/dsl-course.yml`): design/inputs.md "Set up a course" and the
// revision brief's section 2 defaults. The admins list is edited as rows beside this form.

import { FORMATS, SUBMIT, VISIBILITY } from './grading';
import type { Tiers, Values } from './types';

const opt = (value: string, label: string) => ({ value, label });
const PENALTY = /^(\d+(\.\d+)?%|0?\.\d+|0|1(\.0+)?)$/;

export const TIMEZONES = ['Europe/Berlin', 'Europe/London', 'Europe/Paris', 'UTC', 'America/New_York'];

export const ABOUT: Tiers = {
  course_name: { tier: 'ask', label: 'Course name', reason: 'Appears on every semester’s student site.' },
  course_code: { tier: 'ask', label: 'Course code', reason: 'Upper case here; lower case in the org name.' },
  course_description: { tier: 'default', label: 'Description', widget: 'markdown', reason: 'One paragraph. Students see it on every semester’s student site home page.' },
  org_name: { tier: 'advanced', label: 'Org display name', reason: 'The name GitHub shows for the course org.' },
};

export const ASSIGNMENT_DEFAULTS: Tiers = {
  late_window_days: {
    tier: 'default', label: 'Late window, days', widget: 'number', default: 10, defaultLabel: 'default: 10', reason: 'Late work is accepted for this many days after the due date.',
    check: (x, v) => (x !== undefined && !v.late_penalty_per_day ? 'Set both or neither.' : null),
  },
  late_penalty_per_day: {
    tier: 'default', label: 'Late penalty per day', default: '10%', defaultLabel: 'default: 10%',
    check: (x, v) => (x !== undefined && !PENALTY.test(String(x)) ? 'Write a percentage (10%) or a fraction (0.1).' : x !== undefined && v.late_window_days === undefined ? 'Set both or neither.' : null),
  },
  max_team_size: { tier: 'default', label: 'Max team size', widget: 'number', default: 5, defaultLabel: 'default: 5' },
  formats: { tier: 'default', label: 'Default format', widget: 'select', options: [opt('', 'Jupyter notebook (the toolkit’s default)'), ...FORMATS.map(([v, l]) => opt(v, l))] },
  submit_via: { tier: 'default', label: 'Default place to submit', widget: 'select', options: [opt('', 'Their own repo (the toolkit’s default)'), ...SUBMIT.map(([v, l]) => opt(v, l))] },
  team_formation: { tier: 'default', label: 'Default team formation', widget: 'select', options: [opt('', 'Students form their own (the toolkit’s default)'), opt('self_select', 'Students form their own'), opt('assigned', 'You assign them')] },
  visibility: { tier: 'default', label: 'Default visibility', widget: 'select', options: [opt('', 'Private (the toolkit’s default)'), ...Object.entries(VISIBILITY).map(([v, l]) => opt(v, l))] },
};

export const SEMESTER_DEFAULTS: Tiers = {
  timezone: { tier: 'default', label: 'Timezone', widget: 'select', options: [opt('', 'Europe/Berlin (the toolkit’s default)'), ...TIMEZONES.map((t) => opt(t, t))], reason: 'Written into each new semester’s schedule.' },
  archive_auto: { tier: 'default', label: 'Archive automatically after the semester ends', widget: 'checkbox', default: true },
  grace_days: { tier: 'conditional', under: 'archive_auto', when: (v: Values) => v.archive_auto !== false, label: 'Days after the semester ends', widget: 'number', placeholder: '60' },
};
