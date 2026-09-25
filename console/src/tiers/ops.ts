// Tiers for the Options fold of the operation panel (design/inputs.md, per-operation
// tables). Keys are the op's args-schema properties; anything not listed is Derived or
// Hidden and never a field.

import { DEFAULT_DEST_REPO } from '../model/policy';
import { opt, type Tiers } from './types';

/** An unscheduled release's destination: Advanced, defaulting to the institution's release repo and the source path. */
function releaseDest(sourcePath: string): Tiers {
  return {
    semester_dest_repo: { tier: 'advanced', label: 'To repo', default: DEFAULT_DEST_REPO, defaultLabel: `default: ${DEFAULT_DEST_REPO}`, reason: `In the semester. Blank means ${DEFAULT_DEST_REPO}.`, placeholder: DEFAULT_DEST_REPO },
    semester_dest_path: { tier: 'advanced', label: 'To path', defaultLabel: 'default: same as the folder', placeholder: sourcePath },
  };
}

export function releaseAdhoc(repos: string[]): Tiers {
  return {
    course_source_repo: { tier: 'ask', label: 'From repo', widget: 'select', options: [opt('', 'Choose a materials repo'), ...repos.map((r) => opt(r, r))] },
    course_source_path: { tier: 'ask', label: 'Folders', reason: 'Several folders: separate them with commas.', placeholder: 'readings/week04' },
    ...releaseDest(''),
  };
}

export const HANDOUT: Tiers = {
  solution_datetime: {
    tier: 'advanced', label: 'Solution shown', widget: 'select', default: '', defaultLabel: 'default: not now',
    options: [opt('', 'Not now'), opt('now', 'Now, with the hand out')],
    reason: 'Pushes the model answer and rubric into every student’s repo. This is not returning marks, and it cannot be undone for reuse.',
  },
};

export function updateCopies(files: string[]): Tiers {
  return {
    path: { tier: 'ask', label: 'File to push', widget: 'select', reason: 'Picked from the assignment template.', options: [opt('', files.length ? 'Choose a file' : 'Reading the assignment template…'), ...files.map((f) => opt(f, f))] },
    overwrite: { tier: 'default', label: 'Overwrite students’ own edits to this file', widget: 'checkbox', default: false, defaultLabel: 'default: off, so their work is kept' },
  };
}

export const RETURN_MARKS: Tiers = {
  notify: { tier: 'default', label: 'Email students whose marks changed', widget: 'checkbox', default: true, defaultLabel: 'the email links to their marks repo; default on' },
  receipt_note: { tier: 'default', label: 'Post a note on each Submission receipts issue', widget: 'checkbox', default: false, defaultLabel: 'default: off' },
  include_feedback: { tier: 'default', label: 'Include the feedback text in the email', widget: 'checkbox', default: false, defaultLabel: 'default: off', when: (v) => v.notify !== false },
};

export const RETURN_MARKS_ALWAYS = [
  { label: 'Update each student’s marks repo', note: 'always' },
  { label: 'Update the registrar export', note: 'always' },
];

export function publishWebsite(repos: string[]): Tiers {
  return {
    source_repo: { tier: 'ask', label: 'Source materials', widget: 'select', reason: 'Publishing replaces the live public site, if there is one.', defaultLabel: 'default: newest', options: repos.map((r) => opt(r, r)) },
    readings_mode: {
      tier: 'default', label: 'Readings', widget: 'select', default: 'reading-list', defaultLabel: 'default: reading list', reason: 'Publishing the readings themselves needs their licences.',
      options: [opt('reading-list', 'Reading list only'), opt('actual-readings', 'The readings themselves'), opt('none', 'None')],
    },
    include_lectures: { tier: 'default', label: 'Include lectures', widget: 'checkbox', default: true, defaultLabel: 'default: on' },
  };
}
