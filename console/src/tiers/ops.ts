// Tiers for the Options fold of the operation panel (design/inputs.md, per-operation
// tables). Keys are the op's args-schema properties; anything not listed is Derived or
// Hidden and never a field.

import { opt, type Tiers } from './types';

/** A release's destination: Advanced, defaulting to materials and the source path. */
export function releaseDest(sourcePath: string): Tiers {
  return {
    cohort_dest_repo: { tier: 'advanced', label: 'To repo', default: 'materials', defaultLabel: 'default: materials', reason: 'In the cohort. Blank means materials.', placeholder: 'materials' },
    cohort_dest_path: { tier: 'advanced', label: 'To path', defaultLabel: 'default: same as the folder', placeholder: sourcePath },
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
  include_solution: { tier: 'advanced', label: 'Include the solution branch', widget: 'checkbox', default: false, defaultLabel: 'default: off' },
};

export function updateCopies(files: string[]): Tiers {
  return {
    path: { tier: 'ask', label: 'File to push', widget: 'select', reason: 'Picked from the template.', options: [opt('', files.length ? 'Choose a file' : 'Reading the template…'), ...files.map((f) => opt(f, f))] },
    overwrite: { tier: 'default', label: 'Overwrite students’ own edits to this file', widget: 'checkbox', default: false, defaultLabel: 'default: off, so their work is kept' },
  };
}

export const RETURN_MARKS: Tiers = {
  notify: { tier: 'default', label: 'Email students whose marks changed', widget: 'checkbox', default: true, defaultLabel: 'the email links to their marks repo; default on' },
  receipt_note: { tier: 'default', label: 'Post a note on the receipts thread', widget: 'checkbox', default: false, defaultLabel: 'default: off' },
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
