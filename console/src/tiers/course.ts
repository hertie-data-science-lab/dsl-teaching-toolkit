// Course details (`.github/dsl-course.yml`): design/inputs.md "Set up a course". The admins
// list is edited as rows beside this form. "Defaults for this course's assignments" is the
// course layer of the cascade (decision 0009 rule 5): each field shows the institution's
// value in grey, the one that applies when it is left empty.

import { COURSE_RUN_KEYS, institutionLayer, resolve, SOURCES, type Layers } from '../model/cascade';
import { DEFAULT_FORMATS, DEFAULT_TIMEZONE } from '../model/policy';
import { FORMATS, SUBMIT, formatWord } from './grading';
import { runTiers } from './runSettings';
import type { Tiers } from './types';

const opt = (value: string, label: string) => ({ value, label });

/** Timezones the schedule offers: the institution's first. */
export const TIMEZONES = [...new Set([DEFAULT_TIMEZONE, 'Europe/London', 'Europe/Paris', 'UTC', 'America/New_York'])];

export const ABOUT: Tiers = {
  course_name: { tier: 'ask', label: 'Course name', reason: 'Appears on every semester’s student site.' },
  course_code: { tier: 'ask', label: 'Course code', reason: 'Upper case here; lower case in the org name.' },
  course_description: { tier: 'default', label: 'Description', widget: 'markdown', reason: 'One paragraph. Students see it on every semester’s student site home page.' },
};


const onlyInstitution = (): Layers => Object.fromEntries(SOURCES.map((s) => [s, s === 'institution' ? institutionLayer() : {}])) as Layers;

/** Defaults for this course's assignments: the run keys over the institution's values, then the two template keys New assignment starts from. */
export function courseDefaultTiers(): Tiers {
  const inst = onlyInstitution();
  return {
    ...runTiers((k) => resolve(k, inst), COURSE_RUN_KEYS),
    formats: {
      tier: 'default', label: 'Format a new assignment starts with', widget: 'select', defaultLabel: `institution default: ${formatWord(DEFAULT_FORMATS[0])}`,
      options: [opt('', `Default (${formatWord(DEFAULT_FORMATS[0])})`), ...FORMATS.map(([v, l]) => opt(v, l))],
    },
    submit_via: { tier: 'default', label: 'Where a new assignment’s students submit', widget: 'select', options: [opt('', `Default (${SUBMIT[0][1].toLowerCase()})`), ...SUBMIT.map(([v, l]) => opt(v, l))] },
  };
}
