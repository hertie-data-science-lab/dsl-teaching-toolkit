// The wizards' fields (design/inputs.md "Set up a course", "New materials repo", "New
// assignment"). New assignment's steps 2 and 3 are the template Settings fields
// (tiers/grading.ts) with the two differences creation makes: visibility can still be chosen
// (and says it cannot be changed later), and tests are refused for the formats that have
// nothing to run.

import { settingsTiers, type CourseDefaults } from './grading';
import { opt, type FieldTier, type Tiers } from './types';
import { ORG_RE, autogradeBlock, termLabel } from '../wizards/model';

const pick = (t: Tiers, keys: string[]): Tiers => Object.fromEntries(keys.map((k) => [k, t[k]]));

export function orgField(why: string): FieldTier {
  return {
    tier: 'default', label: 'Org name', defaultLabel: 'derived; you can edit it', reason: why,
    check: (x) => (!x ? 'Needed.' : ORG_RE.test(String(x)) ? null : 'Letters, digits and dashes, up to 39.'),
  };
}

/** New course, step 1: what the org name is derived from, and the name itself. */
export const COURSE_ORG: Tiers = {
  course_name: { tier: 'ask', label: 'Course name', reason: 'Used to derive the org name.', check: (x) => (x ? null : 'Needed.') },
  course_code: { tier: 'ask', label: 'Course code', reason: 'Upper case here; lower case in the org name.', check: (x) => (x ? null : 'Needed.') },
  org: orgField('hertie-, the course name, the code; lower case, no year. Name it after the course, not the semester.'),
};

export function cohortOrg(terms: string[]): Tiers {
  return {
    term: { tier: 'default', label: 'Semester', widget: 'select', defaultLabel: 'default: next semester', reason: 'Used to derive the org name.', options: terms.map((t) => opt(t, termLabel(t))) },
    org: orgField('hertie-, the course’s short name, the semester (f2026). One org per semester; students join this org, never the course.'),
  };
}

// ------------------------------------------------------------------ New assignment

export function assignmentWhat(terms: string[], next: number, templates: string[]): Tiers {
  return {
    name: { tier: 'ask', label: 'Name', reason: 'Becomes the title students see, and part of their repo name.', check: (x) => (x && String(x).trim() ? null : 'Needed.') },
    number: {
      tier: 'default', label: 'Number', widget: 'number', defaultLabel: `next free: ${next}`, reason: 'Must be free this semester.',
      check: (x) => (x === undefined ? 'Needed.' : Number.isInteger(x) && (x as number) >= 1 && (x as number) <= 999 ? null : 'A whole number from 1 to 999.'),
    },
    term: { tier: 'default', label: 'Semester', widget: 'select', defaultLabel: 'default: newest semester', reason: 'The semester that first uses this assignment template.', options: terms.map((t) => opt(t, termLabel(t))) },
    copy_from: {
      tier: 'advanced', label: 'Copy an existing assignment template', widget: 'select', default: '', defaultLabel: 'default: start fresh',
      reason: 'Copying takes its settings too, so the next two questions are skipped.',
      options: [opt('', 'No, start fresh'), ...templates.map((r) => opt(r, r))],
    },
  };
}

const VIS_WHY: Record<string, string> = {
  shared_dropbox_repo: 'Private: a shared drop box is always private.',
  external: 'Private: the repo holds the brief only.',
};

export function assignmentWork(d: CourseDefaults): Tiers {
  const s = settingsTiers(d);
  return {
    ...pick(s, ['type', 'team_formation', 'max_team_size', 'submit_via', 'submit_url']),
    visibility: {
      tier: 'advanced', label: 'Who can see each student’s repo', widget: 'radio', default: 'private', irreversible: true,
      reason: 'Set once, at creation. Choose private unless you are sure.',
      options: [opt('private', 'Private', 'The student and instructors. The default.'), opt('public', 'Public', 'Anyone on the internet. The solution cannot be shown automatically.'), opt('student_choice', 'Student’s choice', 'Each student decides for their own repo.')],
      forced: (v) => (v.submit_via && v.submit_via !== 'assignment_repo' ? { value: 'private', reason: VIS_WHY[String(v.submit_via)] ?? 'Private.' } : null),
    },
  };
}

export function assignmentMarking(d: CourseDefaults): Tiers {
  const s = settingsTiers(d);
  return {
    autograde: {
      ...s.autograde,
      forced: (v) => {
        const why = autogradeBlock(v);
        return why ? { value: 'false', reason: why } : null;
      },
    },
    tests: { ...s.tests, when: (v) => v.autograde === 'true' && !autogradeBlock(v) },
    ...pick(s, ['completion_check', 'grader_pdf', 'late_window_days', 'late_penalty_per_day']),
  };
}

// ------------------------------------------------------------------ New materials

export const PUBLIC_DIRS = ['lectures', 'everything except readings', 'everything'];
export const PUBLIC_TYPES = ['html', 'html + pdf', 'all files'];

export function newMaterials(terms: string[], repos: string[]): Tiers {
  const copying = (v: Record<string, unknown>) => !!v.copy_from;
  return {
    term: {
      tier: 'default', label: 'Semester', widget: 'select', defaultLabel: 'default: the newest semester',
      reason: 'Materials are usually per semester. Seeds the repo name and the syllabus header.', options: terms.map((t) => opt(t, termLabel(t))),
    },
    open: {
      tier: 'default', label: 'Publish some of it openly', widget: 'checkbox', default: false, defaultLabel: 'default: off keeps everything private to enrolled students',
      forced: (v) => (copying(v) ? { value: false, reason: 'Copying takes its publish settings too, so there is nothing to choose here.' } : null),
    },
    public_dirs: {
      tier: 'conditional', under: 'open', when: (v) => v.open === true && !copying(v), label: 'Which folders', widget: 'select', default: 'lectures', defaultLabel: 'default: lectures',
      reason: 'Seeds publish.yml; you can change it on the repo’s settings later.', options: PUBLIC_DIRS.map((x) => opt(x, x)),
    },
    public_types: {
      tier: 'conditional', under: 'open', when: (v) => v.open === true && !copying(v), label: 'Which file types', widget: 'select', default: 'html + pdf', defaultLabel: 'default: html + pdf',
      options: PUBLIC_TYPES.map((x) => opt(x, x)),
    },
    copy_from: {
      tier: 'advanced', label: 'Copy an existing materials repo', widget: 'select', default: '', defaultLabel: 'default: fresh starter',
      reason: 'Copying takes its publish settings too, so the choices above are ignored.', options: [opt('', 'No, a fresh starter'), ...repos.map((r) => opt(r, r))],
    },
  };
}
