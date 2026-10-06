// The live demo's people.yml and dsl-course.yml, which the console's generated schemas
// refuse but the engine reads (research 07 section 5): the console saves them with a note.
// Shapes are the demo's; names and addresses are made up.

import { describe, expect, it } from 'vitest';
import { fieldErrors } from '../src/forms/Form';
import { courseFileAfter, detailsOf } from '../src/screens/CourseEdit';
import { YamlText, obj } from '../src/edit/yamlText';
import { PERSON } from '../src/tiers/people';

const COURSE = `course_name: Deep Learning (Demo)
course_code: E1234
central_ref: preview
people:
  course_admins:
    - github_handle: "a-admin"
  instructors:
    - name: "Prof. A. Example"
      title: "Professor"
      start: "2026-08-01"
      end: "2027-07-31"
  teaching_assistants:
    - name: "B. Sample"
      start: "2026-08-01"
`;

describe('the live demo files', () => {
  const meta = obj(new YamlText(COURSE).toJS());
  const before = detailsOf(meta);

  it('saves course details over card dates and course-level assistants: the schema accepts what the site reads', () => {
    const out = courseFileAfter(COURSE, before, { ...before, about: { ...before.about, course_code: 'E1235' } }, meta);
    expect('text' in out && out.text).toContain('course_code: E1235');
  });

  it('still refuses what the engine would not read', () => {
    const bad = `${COURSE}  lecturers: []\n`;
    const out = courseFileAfter(bad, before, { ...before, about: { ...before.about, course_code: 'E1235' } }, obj(new YamlText(bad).toJS()));
    expect('error' in out && out.error).toContain('must NOT have additional properties');
  });

  it('lets a staff entry with a name and no handle or email be saved, as display only', () => {
    expect(fieldErrors(null, PERSON, { role: 'ta', name: 'B. Sample' })).toEqual({});
    expect(fieldErrors(null, PERSON, { role: 'instructor', name: 'Prof. A. Example', email: 'a@example.org' })).toEqual({});
    expect(fieldErrors(null, PERSON, { role: 'ta' }).github_handle).toBe('Needed, or a display name for a card only.');
    expect(fieldErrors(null, PERSON, { role: 'ta', github_handle: 'b-sample' }).email).toBe('Needed.');
  });
});
