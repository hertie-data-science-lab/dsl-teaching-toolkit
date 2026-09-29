// The Public website tab's writer for opencourse.yml (decision 0016).

import { describe, expect, it } from 'vitest';
import { YamlText } from '../src/edit/yamlText';
import { newestRepo, websiteFileAfter, websiteOf } from '../src/screens/CourseEdit';

describe('the public website settings', () => {
  const FILE = '# INSTRUCTOR-OWNED\nenabled: false   # off\nsource_repo:\nreadings_mode: reading-list\ninclude_lectures: true\nwithhold: []\n';

  it('defaults the source to the newest materials repo, not the first listed', () => {
    expect(newestRepo(['course-materials-f2025', 'course-materials-f2026', 'course-materials-s2026'])).toBe('course-materials-f2026');
    expect(newestRepo(['course-materials-f2025', 'course-materials-s2026'])).toBe('course-materials-s2026');
    expect(newestRepo(['slides', 'code'])).toBe('slides');
    expect(newestRepo([])).toBeUndefined();
  });

  it('reads the defaults the engine fills in', () => {
    expect(websiteOf({})).toEqual({ enabled: false, source_repo: '', readings_mode: 'reading-list', include_lectures: true, withhold: '' });
  });

  it('writes only what changed and keeps the comments', () => {
    const before = websiteOf(new YamlText(FILE).toJS() as Record<string, unknown>);
    const out = websiteFileAfter(FILE, before, { ...before, enabled: true, source_repo: 'course-materials-f2026', withhold: 'labs/\n\n*.key' });
    expect('text' in out && out.text).toBe('# INSTRUCTOR-OWNED\nenabled: true    # off\nsource_repo: course-materials-f2026\nreadings_mode: reading-list\ninclude_lectures: true\nwithhold:\n  - labs/\n  - "*.key"\n');
  });

  it('refuses to turn the website on without a source repo', () => {
    const before = websiteOf({});
    expect(websiteFileAfter(FILE, before, { ...before, enabled: true })).toEqual({ error: 'Choose the source materials before turning the website on.' });
  });

  it('creates the file when the course has none', () => {
    const out = websiteFileAfter(null, websiteOf({}), { ...websiteOf({}), enabled: true, source_repo: 'm' });
    expect('text' in out && out.text.startsWith('# INSTRUCTOR-OWNED')).toBe(true);
    expect('text' in out && new YamlText(out.text).toJS()).toEqual({ enabled: true, source_repo: 'm', readings_mode: 'reading-list', include_lectures: true, withhold: [] });
  });
});
