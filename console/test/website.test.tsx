// The Public website tab's writer for course.yml's `open_course_site:` (decisions 0016, 0039).

import { describe, expect, it } from 'vitest';
import { YamlText } from '../src/edit/yamlText';
import { newestRepo, websiteFileAfter, websiteOf } from '../src/screens/CourseEdit';

describe('the public website settings', () => {
  const FILE = '# INSTRUCTOR-OWNED\ncourse:\n  name: ML\nopen_course_site:\n  enabled: false   # off\n  source_repo:\n  readings_mode: reading-list\n  include_lectures: true\n  withhold: []\n';

  it('defaults the source to the newest materials repo, not the first listed', () => {
    expect(newestRepo(['course-materials-f2025', 'course-materials-f2026', 'course-materials-s2026'])).toBe('course-materials-f2026');
    expect(newestRepo(['course-materials-f2025', 'course-materials-s2026'])).toBe('course-materials-s2026');
    expect(newestRepo(['slides', 'code'])).toBe('slides');
    expect(newestRepo([])).toBeUndefined();
  });

  it('reads the defaults the engine fills in', () => {
    expect(websiteOf({})).toEqual({ enabled: false, source_repo: '', readings_mode: 'reading-list', include_lectures: true, withhold: '' });
    expect(websiteOf({ open_course_site: { enabled: true } }).enabled).toBe(true);
  });

  it('writes only what changed, in its block, and keeps the comments', () => {
    const before = websiteOf(new YamlText(FILE).toJS() as Record<string, unknown>);
    const out = websiteFileAfter(FILE, before, { ...before, enabled: true, source_repo: 'course-materials-f2026', withhold: 'labs/\n\n*.key' });
    expect('text' in out && out.text).toBe('# INSTRUCTOR-OWNED\ncourse:\n  name: ML\nopen_course_site:\n  enabled: true    # off\n  source_repo: course-materials-f2026\n  readings_mode: reading-list\n  include_lectures: true\n  withhold:\n    - labs/\n    - "*.key"\n');
  });

  it('refuses to turn the website on without a source repo', () => {
    const before = websiteOf({});
    expect(websiteFileAfter(FILE, before, { ...before, enabled: true })).toEqual({ error: 'Choose the source materials before turning the website on.' });
  });

  it('writes the whole block into a course.yml that has none, the rest kept', () => {
    const out = websiteFileAfter('course:\n  name: ML\n', websiteOf({}), { ...websiteOf({}), enabled: true, source_repo: 'm' });
    expect('text' in out && out.text.startsWith('course:\n  name: ML\n')).toBe(true);
    expect('text' in out && new YamlText(out.text).toJS()).toEqual({ course: { name: 'ML' }, open_course_site: { enabled: true, source_repo: 'm', readings_mode: 'reading-list', include_lectures: true, withhold: [] } });
  });
});
