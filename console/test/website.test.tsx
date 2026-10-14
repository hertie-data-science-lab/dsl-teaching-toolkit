// The Public website tab's writer for course.yml `open_course_site:` (decisions 0016, 0040).

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { YamlText } from '../src/edit/yamlText';
import { PUBLISH_DEFAULT } from '../src/model/selection';
import { folderKindOf, newestRepo, websiteFileAfter, websiteOf } from '../src/screens/CourseEdit';
import { SelectionEditor, shownFiles } from '../src/ui/SelectionEditor';
import { hostFileAfter } from '../src/screens/Site';

describe('the public website settings', () => {
  const FILE = '# INSTRUCTOR-OWNED\ncourse:\n  name: X\nopen_course_site:   # the site\n  enabled: false   # off\n  source_repo:\n  publish:\n    kinds: [lectures, labs]\n';
  const site = (text: string) => (new YamlText(text).toJS() as Record<string, Record<string, unknown>>).open_course_site;

  it('defaults the source to the newest materials repo, not the first listed', () => {
    expect(newestRepo(['course-materials-f2025', 'course-materials-f2026', 'course-materials-s2026'])).toBe('course-materials-f2026');
    expect(newestRepo(['course-materials-f2025', 'course-materials-s2026'])).toBe('course-materials-s2026');
    expect(newestRepo(['slides', 'code'])).toBe('slides');
    expect(newestRepo([])).toBeUndefined();
  });

  it('reads the defaults the engine fills in', () => {
    expect(websiteOf({})).toEqual({ enabled: false, source_repo: '', publish: {}, reading_lists: true });
    expect(websiteOf(site(FILE)).publish).toEqual({ kinds: ['lectures', 'labs'] });
  });

  it('writes only what changed, under open_course_site, and keeps the comments', () => {
    const before = websiteOf(site(FILE));
    const out = websiteFileAfter(FILE, before, { ...before, enabled: true, source_repo: 'course-materials-f2026', publish: { kinds: ['lectures'], exclude: ['labs/'] }, reading_lists: false });
    expect('text' in out).toBe(true);
    const text = 'text' in out ? out.text : '';
    expect(text).toContain('open_course_site:   # the site\n  enabled: true    # off\n');
    expect(text.startsWith('# INSTRUCTOR-OWNED\ncourse:\n  name: X\n')).toBe(true);
    expect(site(text)).toEqual({ enabled: true, source_repo: 'course-materials-f2026', publish: { kinds: ['lectures'], exclude: ['labs/'], reading_lists: false } });
  });

  it('refuses to turn the website on without a source repo', () => {
    const before = websiteOf({});
    expect(websiteFileAfter(FILE, before, { ...before, enabled: true })).toEqual({ error: 'Choose the source materials before turning the website on.' });
  });

  it('takes a folder’s kind from the institution’s kinds, else supporting files', () => {
    expect(folderKindOf('lectures/01/a.pdf')).toBe('lectures');
    expect(folderKindOf('lab/01/a.pdf')).toBe('labs');
    expect(folderKindOf('data/x.csv')).toBe('assets');
  });
});

describe('the selection editor', () => {
  const files = ['lectures/01/slides.pdf', 'readings/01/paper.pdf', 'labs/01/solutions/a.ipynb', 'lectures/01/draft.pdf'];

  it('shows each file’s verdict from the engine’s rule, and chips the kinds with their counts', () => {
    const value = { kinds: ['lectures', 'readings'], exclude: ['draft.pdf'] };
    expect(shownFiles({ files, value, defaults: PUBLISH_DEFAULT, kindOf: folderKindOf })).toEqual(['lectures/01/slides.pdf', 'readings/01/paper.pdf']);
    const out = render(<SelectionEditor files={files} value={value} onChange={() => {}} defaults={PUBLISH_DEFAULT} kindOf={folderKindOf} inWord="published" />);
    // Readings shown: their chip is red.
    expect(out).toContain('<button type="button" class="chip bad" aria-pressed="true"');
    expect(out).toMatch(/Readings: In · 1/);
    expect(out).toMatch(/Lecture: In · 1/);
    expect(out).toMatch(/Lab: Out/);
    expect(out).toContain('never');
  });

  it('is the withhold tree without chips when there are no defaults', () => {
    const out = render(<SelectionEditor files={files} value={{ exclude: ['draft.pdf'] }} onChange={() => {}} inWord="released to students" outWord="withheld" />);
    expect(out).not.toContain('aria-label="Kinds"');
    expect(out).toContain('withheld');
  });
});

describe('the student site’s hosted copies', () => {
  it('writes student_site.host into semester.yml, and an empty one removes the key', () => {
    const text = '# INSTRUCTOR-OWNED\nstudent_site:\n  syllabus: SYLLABUS.md\n';
    const out = hostFileAfter(text, { kinds: ['lectures', 'readings'] });
    expect('text' in out && new YamlText(out.text).toJS()).toEqual({ student_site: { syllabus: 'SYLLABUS.md', host: { kinds: ['lectures', 'readings'] } } });
    const back = hostFileAfter('text' in out ? out.text : '', {});
    expect('text' in back && new YamlText(back.text).toJS()).toEqual({ student_site: { syllabus: 'SYLLABUS.md' } });
    expect(hostFileAfter(text, { kinds: ['lecture'] })).toHaveProperty('error');
  });
});
