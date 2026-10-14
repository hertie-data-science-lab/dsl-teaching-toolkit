// The console says what the student site says: the schedule's first column and an assignment's
// due-row sentence are the site template's own words (templates/site/_includes/schedule_row.html),
// its labels the policy's kinds (the site's `_data/kinds.yml`).

import { describe, expect, it } from 'vitest';
import ROW from '../../templates/site/_includes/schedule_row.html?raw';
import LAYOUT from '../../templates/site/_layouts/schedule.html?raw';
import { KIND_LABEL, dueSubmit, siteRowWord } from '../src/model/format';

describe('the site’s words', () => {
  it('names each row kind as its site template does', () => {
    // An assignment's two rows append to the assignment kind's label; a special event is the fallback kind's.
    expect(ROW).toContain('include.label | append: " out"');
    expect(ROW).toContain('include.label | append: " due"');
    expect(LAYOUT).toContain('{% elsif key == "special_event" %}{% assign key = "other" %}');
    expect(siteRowWord('handout')).toBe(`${KIND_LABEL.assignment} out`);
    expect(siteRowWord('assignment')).toBe('Assignment out');
    expect(siteRowWord('due')).toBe('Assignment due');
    expect(siteRowWord('special_event')).toBe(KIND_LABEL.other);
    // Every other row takes its kind's policy label.
    expect(siteRowWord('exam')).toBe('Exam');
    expect(siteRowWord('semester')).toBe('Semester');
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
    for (const words of ['Handed in outside GitHub', '- see the brief', 'Submit via', '(public)', '(yours to publish)', 'folder)']) expect(ROW).toContain(words);
  });
});
