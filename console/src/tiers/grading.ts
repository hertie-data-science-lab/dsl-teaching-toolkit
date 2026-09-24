// A template's `grading_config.yml` (on its `solution` branch) as the Settings form reads
// and writes it: design/inputs.md "Assignment Settings", revision brief v3 section 3. The
// form works on a flat model of strings and booleans; `toConfig` turns it back into the
// file's shape.

import { opt, type Tiers, type Values } from './types';

export const FORMATS: [string, string][] = [
  ['ipynb', 'Jupyter notebook'], ['py', 'Python files'], ['rmd', 'R Markdown'], ['qmd', 'Quarto'], ['latex', 'LaTeX'], ['none', 'No starter file'],
];
export const SUBMIT: [string, string, string][] = [
  ['assignment_repo', 'Their own repo', 'Private to the student and staff.'],
  ['shared_dropbox_repo', 'A shared drop box', 'One repo for the class; each student has a folder.'],
  ['external', 'Elsewhere', 'Moodle, Kaggle or in class. The repo carries the brief only.'],
];
export const VISIBILITY: Record<string, string> = { private: 'Private', public: 'Public', student_choice: 'Student’s choice' };

export interface CourseDefaults {
  lateDays: string;
  latePct: string;
  teamSize: string;
}

const bool = (v: unknown) => (v === true ? 'true' : v === false ? 'false' : v == null ? undefined : String(v));
const isDrop = (v: Values) => v.submit_via === 'shared_dropbox_repo';

/** grading_config.yml -> the form's values. */
export function fromConfig(cfg: Record<string, unknown>): Values {
  const s = (k: string) => (cfg[k] == null ? undefined : String(cfg[k]));
  return {
    title: s('title'),
    type: s('type') ?? 'individual',
    team_formation: s('team_formation'),
    max_team_size: cfg.max_team_size ?? undefined,
    submit_via: s('submit_via') === 'github' ? 'assignment_repo' : (s('submit_via') ?? 'assignment_repo'),
    submit_url: s('submit_url'),
    visibility: s('visibility') ?? 'private',
    format: s('format'),
    autograde: bool(cfg.autograde),
    tests: s('tests'),
    completion_check: cfg.completion_check === true ? 'on' : cfg.completion_check === false ? 'off' : 'auto',
    grader_pdf: cfg.grader_pdf === true,
    late_window_days: cfg.late_window_days ?? undefined,
    late_penalty_per_day: s('late_penalty_per_day'),
  };
}

/** The form's values -> the keys of grading_config.yml, undefined for "leave it out". */
export function toConfig(v: Values): Record<string, unknown> {
  const group = v.type === 'group';
  const auto = v.autograde === 'true' ? true : v.autograde === 'false' ? false : v.autograde;
  return {
    title: v.title || undefined,
    type: v.type === 'group' ? 'group' : v.type === 'individual' ? 'individual' : undefined,
    team_formation: group ? v.team_formation || undefined : undefined,
    max_team_size: group ? v.max_team_size ?? undefined : undefined,
    submit_via: v.submit_via,
    submit_url: v.submit_via === 'external' ? v.submit_url || undefined : undefined,
    visibility: v.visibility,
    format: v.format || undefined,
    autograde: isDrop(v) ? undefined : auto,
    tests: auto === true && v.tests && v.tests !== 'tests' ? v.tests : undefined,
    completion_check: isDrop(v) ? undefined : v.completion_check === 'on' ? true : v.completion_check === 'off' ? false : undefined,
    grader_pdf: isDrop(v) ? undefined : v.grader_pdf ? true : undefined,
    late_window_days: v.late_window_days ?? undefined,
    late_penalty_per_day: v.late_penalty_per_day || undefined,
  };
}

const PENALTY = /^(\d+(\.\d+)?%|0?\.\d+|0|1(\.0+)?)$/;

/** `teamsHref`: where "You assign them" links to, the newest cohort's Teams page when there is one. */
export function settingsTiers(d: CourseDefaults, teamsHref?: string): Tiers {
  return {
    title: { tier: 'default', label: 'Title', reason: 'Shown to students on the site and in their repo.' },
    type: {
      tier: 'ask', label: 'Alone or in teams', widget: 'radio', defaultLabel: 'default: alone', reason: 'This choice changes downstream options.',
      options: [opt('individual', 'Alone', 'One repo per student. The default.'), opt('group', 'In teams', 'One repo per team; teams form before hand out.')],
    },
    team_formation: {
      tier: 'conditional', under: 'type', when: (v) => v.type === 'group', label: 'How teams form', widget: 'radio', default: 'self_select', defaultLabel: 'default: students choose',
      options: [
        opt('self_select', 'Students form their own', 'On the student site. The default.'),
        { ...opt('assigned', 'You assign them', 'You assign them on the cohort’s Teams page once hand-out is scheduled.'), href: teamsHref },
      ],
    },
    max_team_size: {
      tier: 'conditional', under: 'type', when: (v) => v.type === 'group', label: 'Max team size', widget: 'number', defaultLabel: `course default: ${d.teamSize}`,
      placeholder: d.teamSize, reason: 'Students cannot join a team that is full.',
      check: (x) => (x !== undefined && (!Number.isInteger(x) || (x as number) < 1) ? 'A whole number, 1 or more.' : null),
    },
    submit_via: {
      tier: 'default', label: 'Where students submit', widget: 'radio', default: 'assignment_repo', defaultLabel: 'default: their own repo', reason: 'Decides what marking reads.',
      options: SUBMIT.map(([v, l, s]) => opt(v, l, v === 'assignment_repo' ? `${s} The default.` : s)),
    },
    submit_url: {
      tier: 'conditional', under: 'submit_via', when: (v) => v.submit_via === 'external', label: 'Link to where they submit', widget: 'url', placeholder: 'https://',
      reason: 'Shown on the student site beside the due date.',
      check: (x) => (!x ? 'Needed when students submit elsewhere.' : /^https:\/\/\S+$/.test(String(x)) ? null : 'The link must start with https://.'),
    },
    visibility: {
      tier: 'default', label: 'Who can see each student’s repo', widget: 'radio', default: 'private', defaultLabel: 'default: private',
      reason: 'Applies to copies handed out after this change; existing copies keep theirs.',
      options: Object.entries(VISIBILITY).map(([v, l]) => opt(v, l)),
      forced: (v) =>
        v.submit_via === 'shared_dropbox_repo' ? { value: 'private', reason: 'Private: a shared drop box is always private.' }
        : v.submit_via === 'external' ? { value: 'private', reason: 'Private: the repo holds the brief only.' }
        : null,
    },
    format: {
      tier: 'default', label: 'What students hand in', widget: 'radio', default: 'ipynb', defaultLabel: 'default: Jupyter notebook', reason: 'Seeds the starter files and decides how markers see submissions.',
      options: FORMATS.map(([v, l]) => opt(v, l, v === 'ipynb' ? 'The default.' : undefined)),
    },
    autograde: {
      tier: 'default', label: 'Run automatic tests on submissions', widget: 'radio', default: 'false', defaultLabel: 'default: off',
      options: [opt('true', 'On', 'Tests suggest a mark. Marker has final discretion.'), opt('false', 'Off', 'Marked purely by hand. The default')],
      forced: (v) => (isDrop(v) ? { value: undefined, reason: 'A shared drop box holds every student’s work in one repo, so tests cannot run per student.' } : null),
      check: (x) => (x === undefined || x === 'true' || x === 'false' ? null : `The file says “${String(x)}”. Choose on or off.`),
    },
    tests: {
      tier: 'conditional', under: 'autograde', when: (v) => v.autograde === 'true' && !isDrop(v), label: 'Tests folder', default: 'tests', defaultLabel: 'default: tests',
      reason: 'On the solution branch; students never see it.',
    },
    completion_check: {
      tier: 'advanced', label: 'Completion check', widget: 'radio', default: 'auto',
      defaultLabel: 'default: auto (on for a notebook, off otherwise)', reason: 'Flags submissions with unanswered questions in the mark sheet.',
      options: [opt('auto', 'Auto', 'The default.'), opt('on', 'On'), opt('off', 'Off')],
      forced: (v) => (isDrop(v) ? { value: 'auto', reason: 'Off: not available for a shared drop box.' } : null),
    },
    grader_pdf: {
      tier: 'advanced', label: 'Marker PDF', widget: 'checkbox', default: false, defaultLabel: 'a PDF of each submission for markers; default off',
      forced: (v) => (isDrop(v) ? { value: false, reason: 'Off: not available for a shared drop box.' } : null),
    },
    late_window_days: {
      tier: 'advanced', label: 'Late work for this assignment, days', widget: 'number', placeholder: d.lateDays,
      defaultLabel: `course default: ${d.latePct} per day, up to ${d.lateDays} days`, reason: 'Empty means the course default.',
      check: (x, v) => (x !== undefined && !v.late_penalty_per_day ? 'Set both or neither; one alone is ignored and the course default applies.' : null),
    },
    late_penalty_per_day: {
      tier: 'advanced', label: 'Late penalty per day', placeholder: d.latePct, defaultLabel: `course default: ${d.latePct}`, reason: 'Write 10% or 0.1.',
      check: (x, v) =>
        x !== undefined && !PENALTY.test(String(x)) ? 'Write a percentage (10%) or a fraction (0.1).'
        : x !== undefined && v.late_window_days === undefined ? 'Set both or neither; one alone is ignored and the course default applies.' : null,
    },
  };
}
