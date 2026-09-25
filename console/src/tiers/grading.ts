// A template's `grading_config.yml` (on its `solution` branch) as the Settings form reads
// and writes it: design/inputs.md "Assignment Settings", revision brief v3 section 3. The
// template holds what the task IS (decision 0009 rule 2): how each semester runs it (teams,
// visibility, the late rule, the submit link) is that semester's assignments.yml. The form
// works on a flat model; `toConfig` turns it back into the file's shape.

import { questionFile, questionPoints, questionsFromRows } from '../model/marks';
import { FORMAT_KEYS } from '../model/policy';
import { opt, type Tiers, type Values } from './types';

export { VISIBILITY } from '../model/cascade';

const FORMAT_LABEL: Record<string, string> = {
  ipynb: 'Jupyter notebook', py: 'Python files', rmd: 'R Markdown', qmd: 'Quarto', latex: 'LaTeX', none: 'No starter file',
};
/** Every format a template may list (the schema's), with its label. */
export const FORMATS: [string, string][] = FORMAT_KEYS.map((k) => [k, FORMAT_LABEL[k] ?? k]);
export const formatWord = (f: string) => FORMAT_LABEL[f] ?? f;

export const SUBMIT: [string, string, string][] = [
  ['assignment_repo', 'Their own repo', 'Private to the student and instructors.'],
  ['shared_dropbox_repo', 'A shared drop box', 'One repo for the class; each student has a folder.'],
  ['external', 'Elsewhere', 'Moodle, Kaggle or in class. The repo carries the brief only.'],
];

/** A `formats:` value as a list: the file may spell a list or one comma-separated string. */
export function formatsList(v: unknown): string[] {
  const all = Array.isArray(v) ? v.map(String) : typeof v === 'string' ? v.split(',') : [];
  return all.map((x) => x.trim()).filter(Boolean);
}

const bool = (v: unknown) => (v === true ? 'true' : v === false ? 'false' : v == null ? undefined : String(v));
const isDrop = (v: Values) => v.submit_via === 'shared_dropbox_repo';

/** grading_config.yml -> the form's values. */
export function fromConfig(cfg: Record<string, unknown>): Values {
  const s = (k: string) => (cfg[k] == null ? undefined : String(cfg[k]));
  return {
    title: s('title'),
    type: s('type') ?? 'individual',
    submit_via: s('submit_via') === 'github' ? 'assignment_repo' : (s('submit_via') ?? 'assignment_repo'),
    formats: formatsList(cfg.formats),
    autograde: bool(cfg.autograde),
    tests: s('tests'),
    completion_check: cfg.completion_check === true ? 'on' : cfg.completion_check === false ? 'off' : 'auto',
    grader_pdf: cfg.grader_pdf === true,
  };
}

/** The form's values -> the keys of grading_config.yml, undefined for "leave it out". */
export function toConfig(v: Values): Record<string, unknown> {
  const auto = v.autograde === 'true' ? true : v.autograde === 'false' ? false : v.autograde;
  const formats = (v.formats as string[] | undefined) ?? [];
  return {
    title: v.title || undefined,
    type: v.type === 'group' ? 'group' : v.type === 'individual' ? 'individual' : undefined,
    submit_via: v.submit_via,
    formats: formats.length ? formats : undefined,
    autograde: isDrop(v) ? undefined : auto,
    tests: auto === true && v.tests && v.tests !== 'tests' ? v.tests : undefined,
    completion_check: isDrop(v) ? undefined : v.completion_check === 'on' ? true : v.completion_check === 'off' ? false : undefined,
    grader_pdf: isDrop(v) ? undefined : v.grader_pdf ? true : undefined,
  };
}

/** The template's own fields; `formats` is the format picker beside them, not a tier. */
export function settingsTiers(): Tiers {
  return {
    title: { tier: 'default', label: 'Title', reason: 'Shown to students on the site and in their repo.' },
    type: {
      tier: 'ask', label: 'Alone or in teams', widget: 'radio', defaultLabel: 'default: alone', reason: 'This choice changes downstream options.',
      options: [opt('individual', 'Alone', 'One repo per student. The default.'), opt('group', 'In teams', 'One repo per team; teams form before hand out.')],
    },
    submit_via: {
      tier: 'default', label: 'Where students submit', widget: 'radio', default: 'assignment_repo', defaultLabel: 'default: their own repo', reason: 'Decides what marking reads.',
      options: SUBMIT.map(([v, l, s]) => opt(v, l, v === 'assignment_repo' ? `${s} The default.` : s)),
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
      defaultLabel: 'default: auto (on for a notebook, off otherwise)', reason: 'Flags submissions with unanswered questions in the mark sheet. Reads the runnable format.',
      options: [opt('auto', 'Auto', 'The default.'), opt('on', 'On'), opt('off', 'Off')],
      forced: (v) => (isDrop(v) ? { value: 'auto', reason: 'Off: not available for a shared drop box.' } : null),
    },
    grader_pdf: {
      tier: 'advanced', label: 'Marker PDF', widget: 'checkbox', default: false, defaultLabel: 'a PDF of each submission for markers; default off',
      forced: (v) => (isDrop(v) ? { value: false, reason: 'Off: not available for a shared drop box.' } : null),
    },
  };
}

// ------------------------------------------------------------------ questions

export interface QuestionRow {
  name: string;
  points: string;
  file: string;
}

/** `questions:` as the table's rows. */
export function questionRows(q: unknown): QuestionRow[] {
  if (!q || typeof q !== 'object') return [];
  return Object.entries(q as Record<string, unknown>).map(([name, v]) => ({ name, points: questionPoints(v) == null ? '' : String(questionPoints(v)), file: questionFile(v) ?? '' }));
}

/** The table's rows as `questions:` (model/marks `questionsFromRows`, with the file column). */
export function questionsValue(rows: QuestionRow[], was: unknown): Record<string, unknown> | undefined {
  return questionsFromRows(rows.map((r) => [r.name, r.points, r.file]), was);
}
