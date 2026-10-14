// Tiers for the Options fold of the operation panel (design/inputs.md, per-operation
// tables). Keys are the op's args-schema properties; anything not listed is Derived or
// Hidden and never a field.

import { SOURCE_WORD, type Source } from '../model/settings';
import { SOLUTION_WARNING } from '../model/labels';
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
    course_source_repo: { tier: 'ask', label: 'From repo', widget: 'select', options: [opt('', 'Choose a repo'), ...repos.map((r) => opt(r, r))] },
    course_source_path: { tier: 'ask', label: 'Folders', reason: 'Several folders: separate them with commas.', placeholder: 'readings/week04' },
    ...releaseDest(''),
  };
}

export const HANDOUT: Tiers = {
  solution_datetime: {
    tier: 'advanced', label: 'Solution shown', widget: 'select', default: '', defaultLabel: 'default: not now',
    options: [opt('', 'Not now'), opt('now', 'Now, with the hand out')],
    reason: SOLUTION_WARNING,
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
  receipt_note: {
    tier: 'default', label: 'Post a note on each Submission receipts issue', widget: 'checkbox', default: false, defaultLabel: 'default: off',
    hint: { text: 'Every student (or team) has one GitHub issue on their assignment repo where the toolkit records what it did with their submission: received, snapshot taken, marks returned. Ticking this adds a line there saying marks are back, with the link. Off by default: the email already says so.' },
  },
  include_feedback: {
    tier: 'default', label: 'Include the feedback text in the email', widget: 'checkbox', default: false, defaultLabel: 'default: off', when: (v) => v.notify !== false,
    hint: { text: 'By default the email only says there are new marks and links to the student’s marks repo, where the feedback is. Tick this to put the feedback text itself in the email. Private notes are never included.' },
  },
};

/** The `?` on "Include marks per question". */
export const PER_QUESTION_HINT = 'On: the student’s page lists the mark per question and the per-question feedback. Off: the score, late line, adjustment and final only. Saved for this assignment, so automatic returns do the same.';

/** Return marks' options, with "Include marks per question" prefilled from the assignment's effective `marks_per_question`. */
export function returnMarksTiers(perQuestion: { value: boolean; source: Source }): Tiers {
  return {
    ...RETURN_MARKS,
    per_question: {
      tier: 'default', label: 'Include marks per question', widget: 'checkbox', default: perQuestion.value,
      defaultLabel: `${perQuestion.value ? 'on' : 'off'}, ${SOURCE_WORD[perQuestion.source]}`,
      hint: { text: PER_QUESTION_HINT },
    },
  };
}

export const RETURN_MARKS_ALWAYS = [
  { label: 'Update each student’s marks repo', note: 'always' },
  { label: 'Update the semester grades file (one CSV, every student, every assignment)', note: 'always' },
];

/** The `?` beside Return marks, one paragraph each. */
export const RETURN_MARKS_ABOUT = {
  label: 'What returning marks does',
  text: [
    'Each student gets a private marks repo, created the first time you return marks. Only that student and the teaching team can see it. Returning marks writes this assignment’s score, feedback, any late penalty and the final grade into it, next to assignments already returned, and updates the semester grades file.',
    'Each student whose marks are new or changed gets an email linking to their marks repo (untick “Email students” to skip it; tick “Include the feedback text” to put your feedback in the email too). Optionally a short note goes on their Submission receipts issue.',
    'Your private notes never leave the mark sheet. A unit that is not fully marked, or has a penalty the toolkit cannot apply, is held back and the summary says how many; fix the sheet and return again.',
    'Nothing is ever deleted. You can return marks as often as you like: a re-run touches only students whose marks changed, so correcting one mark later is safe. Preview first: it shows how many repos would change and emails go out, and writes the per-student detail to a private preview issue in semester-config.',
  ],
};

/** The `?` beside Send codes. */
export const SEND_CODES_ABOUT = {
  label: 'About Send codes',
  text: [
    'Every student on the roster gets a private enrolment code by email the moment their row is saved; they paste it on the semester’s join form to get their repos. This button sends a NEW code to everyone who has not joined yet (their old code stops working) and emails it again. Preview first to see how many that is.',
  ],
};

/** `siteExists`: the website was published before, so turning it off leaves that version up. */
export const WEBSITE_OFF_LIVE = 'Off: the site stays as last published and no longer updates.';

export function publishWebsite(repos: string[], siteExists = false): Tiers {
  return {
    enabled: {
      tier: 'ask', label: 'Keep the website updated', widget: 'checkbox', default: false,
      reason: siteExists ? WEBSITE_OFF_LIVE : 'Off: nothing is published.',
    },
    source_repo: { tier: 'ask', label: 'Source materials', widget: 'select', reason: 'The website is built from this repo. Publishing replaces the live site.', options: repos.map((r) => opt(r, r)) },
    reading_lists: { tier: 'default', label: 'Reading lists', widget: 'checkbox', default: true, defaultLabel: 'default: on', reason: 'Each session’s reading list, as text. The readings themselves are published only when Readings is In below.' },
  };
}
