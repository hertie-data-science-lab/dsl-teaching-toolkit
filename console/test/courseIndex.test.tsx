// The Materials and Assignment templates index screens, the materials file tree and its
// badges, the course nav's Cohorts group and the course overview's two status boxes.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { badgeFiles, buildTree } from '../src/edit/badges';
import type { GhRepo } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles, type Files } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { CourseScreen } from '../src/screens/Course';
import { MaterialsScreen, WebsiteScreen, folderKinds, resetLabel, writeHolds } from '../src/screens/CourseEdit';
import { MaterialsIndexScreen, TemplatesIndexScreen, materialsSentence, otherRepos, publicPatterns } from '../src/screens/CourseIndex';
import type { CourseProps } from '../src/screens/types';
import { Sidenav } from '../src/ui/shell';
import example from './fixtures/status.example.json';

const STATUS = example as unknown as Status;
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const OLD_ORG = 'hertie-dsl-demo-f2025';
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const old = { org: OLD_ORG, term: 'f2025', termLabel: 'Fall 2025' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort, old], meta: null };
const ready: Loaded = { kind: 'ready', status: STATUS, sha: 's', stale: [] };
const archived: Loaded = { kind: 'ready', status: { ...STATUS, problems: [], semester: { ...STATUS.semester!, org: OLD_ORG, live: false } }, sha: 's', stale: [] };
const withTemplate: Loaded = { kind: 'ready', status: { ...STATUS, assignments: [{ ...STATUS.assignments![0], template: 'assignment-3-f2026' }] }, sha: 's', stale: [] };

const MAT = 'course-materials-f2026';
const files = new StaticFiles(
  {
    [`${COURSE_ORG}/${MAT}/publish.yml`]: 'public:\n  - lectures/**/slides.html\n  - nothing-here/\n',
    [`${COURSE_ORG}/${MAT}/.releaseignore`]: '# comment\nsolutions/\n*.key\n',
    [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: 'title: Group project\ntype: group\nformats: [py]\n',
  },
  {},
  { [`${COURSE_ORG}/${MAT}`]: ['SYLLABUS.md', 'lectures/05_trees/slides.html', 'lectures/05_trees/notes.pdf', 'solutions/05.ipynb'] },
  {
    [COURSE_ORG]: [
      { name: '.github' }, { name: MAT, pushed_at: '2026-09-22T14:38:46Z' }, { name: 'assignment-3-f2026' }, { name: 'assignment-9-draft' },
      { name: 'lecture-code-f2026', html_url: 'https://github.com/x/lecture-code-f2026' }, { name: `${COURSE_ORG}.github.io` }, { name: 'old-thing', archived: true },
    ],
  },
);
const cp = (over: Partial<CourseProps> = {}): CourseProps => ({ course, loaded: { kind: 'absent' }, cohortStates: { [COHORT_ORG]: ready, [OLD_ORG]: archived }, files, now: NOW, ...over });
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('file badges', () => {
  const f = ['SYLLABUS.md', 'lectures/05/slides.html', 'lectures/05/notes.pdf', 'solutions/05.ipynb', 'lectures/05/answers.key'];
  it('marks withheld over published openly, and the rest released to students', () => {
    const b = badgeFiles(f, ['lectures/**/slides.html', '*.key'], ['solutions/', '*.key']);
    expect(b.badges).toEqual({
      'SYLLABUS.md': 'released', 'lectures/05/slides.html': 'public', 'lectures/05/notes.pdf': 'released', 'solutions/05.ipynb': 'withheld', 'lectures/05/answers.key': 'withheld',
    });
  });
  it('flags a rule that matches nothing, skipping comments and blank lines', () => {
    const b = badgeFiles(f, ['lectures/**/slides.html', 'nothing-here/'], ['# a comment', '', 'solutions/', '!missing.md']);
    expect(b.unmatched).toEqual({ public: ['nothing-here/'], withheld: ['!missing.md'] });
  });
  it('publishes a public deck’s _files bundle with it, never a denylisted path', () => {
    const paths = ['l/05/slides.html', 'l/05/slides_files/fig.png', 'l/05/slides_files/solution/key.png', 'l/05/other_files/x.png', 'l/05/solutions/a.py', 'l/05/tests/t.py', 'l/05/.env.local', 'l/grading_config.yml'];
    const b = badgeFiles(paths, ['l/**'], []);
    expect(b.badges['l/05/slides.html']).toBe('public');
    expect(b.badges['l/05/slides_files/fig.png']).toBe('public');
    expect(b.badges['l/05/other_files/x.png']).toBe('public');
    expect(b.badges['l/05/slides_files/solution/key.png']).toBe('never_public');
    expect(b.badges['l/05/solutions/a.py']).toBe('never_public');
    expect(b.badges['l/05/tests/t.py']).toBe('never_public');
    expect(b.badges['l/05/.env.local']).toBe('never_public');
    expect(b.badges['l/grading_config.yml']).toBe('never_public');
    const only = badgeFiles(['d/slides.html', 'd/slides_files/a.css', 'd/notes_files/b.css'], ['d/slides.html'], []);
    expect(only.badges).toEqual({ 'd/slides.html': 'public', 'd/slides_files/a.css': 'public', 'd/notes_files/b.css': 'released' });
  });
  it('withholds never-material files whatever the rules say', () => {
    const b = badgeFiles(['l/.gitkeep', 'l/.DS_Store', 'l/a.md'], ['l/**'], []);
    expect(b.badges).toEqual({ 'l/.gitkeep': 'withheld', 'l/.DS_Store': 'withheld', 'l/a.md': 'public' });
  });
  it('counts the rules of each list', () => {
    expect(badgeFiles(['a'], [], ['# only a comment', '']).rules).toEqual({ public: 0, withheld: 0 });
  });
  it('nests paths into folders before files', () => {
    const t = buildTree(['b.md', 'a/x.md', 'a/b/y.md']);
    expect(t.map((n) => n.name)).toEqual(['a', 'b.md']);
    expect(t[0].children!.map((n) => n.path)).toEqual(['a/b', 'a/x.md']);
  });
});

describe('other repos', () => {
  it('drops infra, the listed materials repos, templates and archived repos', () => {
    // Materials repos are the ones the course status lists (by topic, decision 0013), not
    // a name prefix: an unlisted `course-materials-*` is just another repo.
    const r = [{ name: '.github' }, { name: 'course-materials-x' }, { name: 'assignment-1-f2026' }, { name: 'org.github.io' }, { name: 'lecture-code' }, { name: 'x', archived: true }, { name: 'listed' }] as GhRepo[];
    expect(otherRepos('org', r, ['listed']).map((x) => x.name)).toEqual(['course-materials-x', 'lecture-code']);
    expect(otherRepos('org', r, ['listed', 'course-materials-x']).map((x) => x.name)).toEqual(['lecture-code']);
  });
  it('reads the public patterns of publish.yml', () => {
    expect(publicPatterns('public:\n  - a\n  - b\n')).toEqual(['a', 'b']);
    expect(publicPatterns('public:\n')).toEqual([]);
  });
});

describe('index screens', () => {
  it('lists materials with state, term, openly published, last change and Other repos', () => {
    const t = text(<MaterialsIndexScreen {...cp()} />);
    expect(t).toContain(MAT);
    expect(t).toContain('Ready');
    expect(t).toContain('Fall 2026');
    expect(t).toContain('Some files selected for the public website.');
    expect(t).toContain('Last change');
    expect(t).toContain('New materials');
    expect(t).toContain('lecture-code-f2026');
    expect(t).toContain('Can be released to a semester from the schedule.');
    expect(t).not.toContain('assignment-9-draft');
    expect(t).not.toContain('old-thing');
    expect(render(<MaterialsIndexScreen {...cp()} />)).toContain(`href="#materials-${MAT}"`);
  });
  it('says why a materials repo is not ready only when it can tell', () => {
    expect(materialsSentence('todo', 'absent')).toBe('Not ready yet: there is no publish.yml.');
    expect(materialsSentence('todo', 'ready')).toBe('Not ready yet: SYLLABUS.md is still the placeholder.');
    expect(materialsSentence('todo', 'loading')).toBe('Not ready yet.');
  });
  it('shows a template still being written neutrally, not as a problem', () => {
    const st: Loaded = { kind: 'ready', status: { ...STATUS, course: { ...STATUS.course!, templates: [{ repo: 'assignment-4-f2026', slug: 'assignment-4', state: 'todo' }] } }, sha: 's', stale: [] };
    for (const v of [<TemplatesIndexScreen {...cp({ loaded: st })} />, <CourseScreen {...cp({ loaded: st })} />]) {
      const out = render(v);
      expect(out).toContain('<span class="chip">Not written yet</span>');
      expect(out).toContain('href="#template-assignment-4">Settings');
      expect(out).not.toContain('href="#template-assignment-4">Fix');
    }
  });
  it('says so when there are no other repos', () => {
    const t = text(<MaterialsIndexScreen {...cp({ files: new StaticFiles({}, {}, {}, { [COURSE_ORG]: [{ name: '.github' }] }) })} />);
    expect(t).toContain('No other repos.');
  });
  it('lists templates with title, teams, format, verdict and the cohorts that schedule them', () => {
    const out = render(<TemplatesIndexScreen {...cp({ cohortStates: { [COHORT_ORG]: withTemplate } })} />);
    const t = text(<TemplatesIndexScreen {...cp({ cohortStates: { [COHORT_ORG]: withTemplate } })} />);
    expect(t).toContain('Assignment 3: Group project');
    expect(t).toContain('Has a problem');
    expect(t).toContain('In teams, Python files.');
    expect(t).toContain('Scheduled in Fall 2026.');
    expect(t).toContain('New assignment');
    expect(out).toContain('href="#template-assignment-3"');
  });
});

describe('materials settings file tree', () => {
  it('badges every file, flags the rule that matches nothing and links each file to its editor', () => {
    const out = render(<MaterialsScreen {...cp({ entry: MAT })} />);
    const t = text(<MaterialsScreen {...cp({ entry: MAT })} />);
    expect(t).toContain('for the public website');
    expect(t).toContain('withheld');
    expect(t).toContain('released to students');
    expect(t).toContain('nothing-here/ matches no file');
    expect(t).toContain('*.key matches no file');
    expect(out).toContain(`https://github.com/${COURSE_ORG}/${MAT}/edit/main/lectures/05_trees/slides.html`);
    expect(out).not.toContain('class="file-list"');
    expect(t).not.toContain('only part of this repo');
  });
  it('says No rules yet, and leaves out Edit links while the default branch is unknown', () => {
    const bare = new StaticFiles({}, {}, { [`${COURSE_ORG}/${MAT}`]: ['a.md'] });
    const out = render(<MaterialsScreen {...cp({ entry: MAT, files: bare })} />);
    expect(out.match(/No rules yet\./g)).toHaveLength(2);
    expect(out).not.toContain('Edit on GitHub');
    expect(out).not.toContain('/edit/main/a.md');
  });
  it('warns on a truncated tree and does not flag rules as matching nothing', () => {
    const part: Files = Object.assign(Object.create(files), { tree: (o: string, r: string) => ({ ...files.tree(o, r), truncated: true }) });
    const t = text(<MaterialsScreen {...cp({ entry: MAT, files: part })} />);
    expect(t).toContain('GitHub returned only part of this repo’s file list; badges may be incomplete.');
    expect(t).not.toContain('matches no file');
  });
});

describe('course nav and overview', () => {
  it('lists each cohort by term with its problems count, archived ones greyed', () => {
    const nav = render(<Sidenav courses={[course]} course={course} cohortStates={{ [COHORT_ORG]: ready, [OLD_ORG]: archived }} current="course" problems={0} />);
    const t = nav.replace(/<[^>]+>/g, ' ');
    expect(t.indexOf('Public website')).toBeLessThan(t.indexOf('Semesters'));
    expect(nav).toContain(`href="?cohort=${COHORT_ORG}#semester"`);
    expect(nav).toMatch(/aria-label="2 problems">2</);
    expect(nav).toContain('class="archived"');
    expect(t).toContain('Assignment templates');
    const inCohort = render(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{ [COHORT_ORG]: ready }} current="week" problems={2} />);
    expect(inCohort.match(/aria-current="page"/g)).toHaveLength(1);
  });
  it('shows the term, not the org, while a cohort status loads', () => {
    const t = text(<CourseScreen {...cp({ cohortStates: { [COHORT_ORG]: { kind: 'loading' } } })} />);
    expect(t).toContain('Fall 2026 Live Fall 2026');
    expect(t).not.toContain(OLD_ORG);
  });
  it('shows course problems only, then each cohort with its count', () => {
    const t = text(<CourseScreen {...cp({ loaded: ready })} />);
    expect(t).toContain('Course problems');
    expect(t).toContain('Marking of Assignment 3 cannot start.');
    expect(t).not.toContain('Everything automatic will happen on time');
    expect(t).toContain('Fall 2025');
    expect(t).toContain('2 problems');
    const none = text(<CourseScreen {...cp({ loaded: { kind: 'ready', status: { ...STATUS, problems: [] }, sha: 's', stale: [] } })} />);
    expect(none).toContain('No course problems.');
  });
});

describe('materials settings: syllabus file and folder kinds', () => {
  const tree = ['E1282.pdf', 'lectures/01/a.pdf', 'Tutorials/01/b.ipynb', 'quiz/q1.md', 'datasets/x.csv'];
  const withYml = new StaticFiles({ [`${COURSE_ORG}/${MAT}/materials.yml`]: 'syllabus: E1282.pdf\nkinds:\n  quiz: exam\n' }, {}, { [`${COURSE_ORG}/${MAT}`]: tree });

  it('gives each top-level folder its kind and says where it came from', () => {
    expect(folderKinds(['lectures', 'Tutorials', 'quiz', 'datasets'], { quiz: 'exam' })).toEqual([
      { folder: 'datasets', kind: 'lecture', from: 'default' },
      { folder: 'lectures', kind: 'lecture', from: 'name' },
      { folder: 'quiz', kind: 'exam', from: 'declared' },
      { folder: 'Tutorials', kind: 'lab', from: 'name' },
    ]);
  });
  it('shows the kinds and the declared syllabus, and edits that file', () => {
    const out = render(<MaterialsScreen {...cp({ entry: MAT, files: withYml })} />);
    const t = text(<MaterialsScreen {...cp({ entry: MAT, files: withYml })} />);
    expect(t).toContain('quiz/ Exam set here');
    expect(t).toContain('Tutorials/ Lab from its name');
    expect(t).toContain('datasets/ Lecture the default');
    expect(out).toContain('value="E1282.pdf"');
    expect(out).toContain(`/edit/main/E1282.pdf`);
    expect(out).toContain('<span class="ft-name">Tutorials/</span><span class="chip">Lab</span>');
  });
  it('names the kind a folder falls back to, and why', () => {
    expect(resetLabel('quiz')).toBe('Lecture (the default)');
    expect(resetLabel('Tutorials')).toBe('Lab (from its name)');
  });
  it('writes materials.yml with only what is declared', () => {
    expect(writeHolds(null, { syllabus: 'E1282.pdf', kinds: { quiz: 'exam' } })).toBe(
      '# INSTRUCTOR-OWNED - yours. What the folder names cannot say: the syllabus file and folder kinds.\nsyllabus: E1282.pdf\nkinds:\n  quiz: exam\n',
    );
    expect(writeHolds('syllabus: a.pdf\nkinds:\n  quiz: exam\n', { syllabus: '', kinds: {} })).toBe('');
  });
  it('the public website says publish.yml is not used yet', () => {
    const t = text(<WebsiteScreen {...cp()} />);
    expect(t).not.toContain('Public patterns');
    expect(t).toContain('follows the settings here for now');
    expect(t).toContain('until then it is only recorded');
  });
});
