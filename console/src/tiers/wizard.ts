// The wizards' fields (design/inputs.md "Set up a course", "New materials repo", "New
// assignment"). New assignment's steps 2 and 3 are the template Settings fields
// (tiers/grading.ts) with the two differences creation makes: visibility can still be chosen
// (and says it cannot be changed later), and tests are refused for the formats that have
// nothing to run.

import { settingsTiers } from './grading';
import { opt, type FieldTier, type Tiers } from './types';
import { ORG_NAME_RE } from '../model/policy';
import { autogradeBlock, parseSource, templateRepo, termLabel } from '../wizards/model';

const pick = (t: Tiers, keys: string[]): Tiers => Object.fromEntries(keys.map((k) => [k, t[k]]));

export function orgField(why: string): FieldTier {
  return {
    tier: 'default', label: 'Org name', defaultLabel: 'derived; you can edit it', reason: why,
    check: (x) => (!x ? 'Needed.' : ORG_NAME_RE.test(String(x)) ? null : 'Letters, digits and dashes, up to 39.'),
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

/** New assignment, step 1: the name. No number and no semester (decision 0014). */
export function assignmentName(): Tiers {
  return {
    name: {
      tier: 'ask', label: 'Name', reason: 'The assignment’s title.',
      check: (x) => (!x || !String(x).trim() ? 'Needed.' : templateRepo(x) ? null : 'Needs a word or a number besides “assignment”.'),
    },
  };
}

/** New assignment, step 1: what it starts from. */
export function assignmentStart(templates: string[]): Tiers {
  return {
    start: {
      tier: 'default', label: 'Start from', widget: 'radio', default: 'fresh', defaultLabel: 'default: fresh',
      options: [
        opt('fresh', 'Fresh', 'Starter files for the formats you choose.'),
        { value: 'template', label: 'A template of this course', sub: 'Choose the files to copy.', off: templates.length ? undefined : 'The course has no assignment templates yet.' },
        opt('repo', 'A repo you can read', 'Choose the files to copy.'),
      ],
    },
    source_template: {
      tier: 'conditional', under: 'start', label: 'Template', widget: 'select', when: (v) => v.start === 'template',
      options: [opt('', 'Choose a template'), ...templates.map((r) => opt(r, r))],
      check: (x, v) => (v.start === 'template' && !x ? 'Choose a template.' : null),
    },
    source_repo: {
      tier: 'conditional', under: 'start', label: 'Repo', placeholder: 'owner/repo, or its GitHub link', when: (v) => v.start === 'repo',
      check: (x, v) => (v.start !== 'repo' ? null : !x ? 'Needed.' : parseSource(x) ? null : 'Give it as owner/repo, or paste its GitHub link.'),
    },
  };
}

/** How students work on it: the template's own keys. How each semester runs it (teams,
 * max team size, visibility, the submit link, the late rule) is that semester's
 * assignments.yml, asked when the assignment is added to a semester's schedule (decision 0009). */
export function assignmentWork(): Tiers {
  return pick(settingsTiers(), ['type', 'submit_via']);
}

export function assignmentMarking(): Tiers {
  const s = settingsTiers();
  return {
    autograde: {
      ...s.autograde,
      forced: (v) => {
        const why = autogradeBlock(v);
        return why ? { value: 'false', reason: why } : null;
      },
    },
    tests: { ...s.tests, when: (v) => v.autograde === 'true' && !autogradeBlock(v) },
    ...pick(s, ['completion_check', 'grader_pdf']),
  };
}

// ------------------------------------------------------------------ New materials


export function newMaterials(terms: string[], repos: string[]): Tiers {
  return {
    term: {
      tier: 'default', label: 'Semester', widget: 'select', defaultLabel: 'default: the newest semester',
      reason: 'Materials are usually per semester. Seeds the repo name and the syllabus header.', options: terms.map((t) => opt(t, termLabel(t))),
    },
    copy_from: {
      tier: 'advanced', label: 'Copy an existing materials repo', widget: 'select', default: '', defaultLabel: 'default: fresh starter',
      reason: 'Starts from an existing repo: every branch and its history.', options: [opt('', 'No, a fresh starter'), ...repos.map((r) => opt(r, r))],
    },
  };
}
