// The console says what the student site says: the schedule's first column and an assignment's
// due-row sentence are the site templates' own words (templates/site/_includes/schedule_row_*.html).

import { describe, expect, it } from 'vitest';
import DUE from '../../templates/site/_includes/schedule_row_due.html?raw';
import OUT from '../../templates/site/_includes/schedule_row_assignment.html?raw';
import EXAM from '../../templates/site/_includes/schedule_row_exam.html?raw';
import EVENT from '../../templates/site/_includes/schedule_row_special_event.html?raw';
import TERM from '../../templates/site/_includes/schedule_row_term_date.html?raw';
import { dueSubmit, siteRowWord } from '../src/model/format';

const column = (html: string) => /data-label="Event">([^<]+)<\/div>/.exec(html)![1];

describe('the site’s words', () => {
  it('names each row kind as its site template does', () => {
    expect(siteRowWord('handout')).toBe(column(OUT));
    expect(siteRowWord('assignment')).toBe(column(OUT));
    expect(siteRowWord('due')).toBe(column(DUE));
    expect(siteRowWord('exam')).toBe(column(EXAM));
    expect(siteRowWord('special_event')).toBe(column(EVENT));
    expect(siteRowWord('term')).toBe(column(TERM));
    // A release takes its kind's policy label, as schedule_row_lecture.html is given it.
    expect(siteRowWord('lecture')).toBe('Lecture');
    expect(siteRowWord('readings')).toBe('Readings');
  });

  it('says where to submit for each shape, in the due row template’s words', () => {
    const say = (shape: string, group = false, url?: string) => {
      const s = dueSubmit({ shape, slug: 'quiz-1', group, url })!;
      return `${s.lead}${s.code ?? ''}${s.tail ?? ''}${s.link?.host ?? ''}`;
    };
    expect(say('external')).toBe('Handed in outside GitHub - see the brief');
    expect(say('external', false, 'https://moodle.example.org/x')).toBe('Handed in outside GitHub - moodle.example.org');
    expect(say('assignment-repo-private')).toBe('Submit via quiz-1-<your-handle>');
    expect(say('assignment-repo-private', true)).toBe('Submit via quiz-1-<your-team>');
    expect(say('assignment-repo-public')).toBe('Submit via quiz-1-<your-handle> (public)');
    expect(say('assignment-repo-student-choice')).toBe('Submit via quiz-1-<your-handle> (yours to publish)');
    expect(say('shared-dropbox-repo')).toBe('Submit via quiz-1-submissions (<your-handle>/ folder)');
    expect(dueSubmit({ shape: '', slug: 'quiz-1', group: false })).toBeNull();
    for (const words of ['Handed in outside GitHub', '- see the brief', 'Submit via', '(public)', '(yours to publish)', 'folder)']) expect(DUE).toContain(words);
  });
});
