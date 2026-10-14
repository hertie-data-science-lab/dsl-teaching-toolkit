// The Materials and Assignment templates index screens, the materials file tree and its
// badges, the course nav's Cohorts group and the course overview's two status boxes.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { badgeFiles, buildTree } from '../src/edit/badges';
import type { GhRepo } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import type { Files } from '../src/model/files';
import { StaticFiles } from './staticFiles';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { CourseScreen, MaterialsChecklist } from '../src/screens/Course';
import { materialsReadiness } from '../src/model/readiness';
import { HomeScreen } from '../src/screens/Home';
import { MaterialsScreen, WebsiteScreen, withSyllabus } from '../src/screens/CourseEdit';
import { MaterialsIndexScreen, TemplatesIndexScreen, otherRepos } from '../src/screens/CourseIndex';
import type { CourseProps } from '../src/screens/types';
import { REVIEWED_MARK, withMark } from '../src/model/materialsRepo';
import { EditFile, newFileUrl } from '../src/ui/bits';
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
    [`${COURSE_ORG}/${MAT}/.releaseignore`]: '# comment\nsolutions/\n*.key\n',
    [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: 'title: Group project\ntype: group\nformats: [py]\n',
  },
  {},
  { [`${COURSE_ORG}/${MAT}`]: ['SYLLABUS.md', 'lectures/05_trees/slides.html', 'lectures/05_trees/notes.pdf', 'solutions/05.ipynb'] },
  {
    [COURSE_ORG]: [
      { name: '.github' }, { name: MAT, pushed_at: '2026-09-22T14:38:46Z' }, { name: 'assignment-3-f2026', topics: ['dsl-assignment'] }, { name: 'assignment-9-draft', topics: ['dsl-assignment'] },
      { name: 'lecture-code-f2026', html_url: 'https://github.com/x/lecture-code-f2026' }, { name: `${COURSE_ORG}.github.io` }, { name: 'old-thing', archived: true },
    ],
  },
);
const cp = (over: Partial<CourseProps> = {}): CourseProps => ({ course, loaded: { kind: 'absent' }, cohortStates: { [COHORT_ORG]: ready, [OLD_ORG]: archived }, files, now: NOW, ...over });
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('file badges', () => {
  const f = ['SYLLABUS.md', 'lectures/05/slides.html', 'lectures/05/notes.pdf', 'solutions/05.ipynb', 'lectures/05/answers.key'];
  it('marks what the list withholds, and the rest released to students', () => {
    const b = badgeFiles(f, ['solutions/', '*.key']);
    expect(b.badges).toEqual({
      'SYLLABUS.md': 'released', 'lectures/05/slides.html': 'released', 'lectures/05/notes.pdf': 'released', 'solutions/05.ipynb': 'withheld', 'lectures/05/answers.key': 'withheld',
    });
  });
  it('flags a rule that matches nothing, skipping comments and blank lines', () => {
    const b = badgeFiles(f, ['# a comment', '', 'solutions/', '!missing.md']);
    expect(b.unmatched).toEqual(['!missing.md']);
  });
  it('counts the rules', () => {
    expect(badgeFiles(['a'], ['# only a comment', '']).rules).toBe(0);
  });
  it('nests paths into folders before files', () => {
    const t = buildTree(['b.md', 'a/x.md', 'a/b/y.md']);
    expect(t.map((n) => n.name)).toEqual(['a', 'b.md']);
    expect(t[0].children!.map((n) => n.path)).toEqual(['a/b', 'a/x.md']);
  });
});

describe('other repos', () => {
  it('drops infra, the listed materials repos, templates and archived repos', () => {
    // Materials repos are the ones the course status lists (by topic, decision 0013), and
    // templates carry the dsl-assignment topic (decision 0014), not a name prefix: an
    // unlisted `course-materials-*` or an `assignment-*` without the topic is just another repo.
    const r = [
      { name: '.github' }, { name: 'course-materials-x' }, { name: 'assignment-1-f2026', topics: ['dsl-assignment'] }, { name: 'regression', topics: ['dsl-assignment'] },
      { name: 'assignment-notes' }, { name: 'org.github.io' }, { name: 'lecture-code' }, { name: 'x', archived: true }, { name: 'listed' },
    ] as GhRepo[];
    expect(otherRepos('org', r, ['listed']).map((x) => x.name)).toEqual(['assignment-notes', 'course-materials-x', 'lecture-code']);
    expect(otherRepos('org', r, ['listed', 'course-materials-x']).map((x) => x.name)).toEqual(['assignment-notes', 'lecture-code']);
  });
});

describe('index screens', () => {
  it('heads Materials and Assignment templates with one See on GitHub, filtered by topic, and no course actions', () => {
    const m = render(<MaterialsIndexScreen {...cp()} />);
    const head = (out: string) => out.slice(out.indexOf('class="page-head"'), out.indexOf('</div></div>', out.indexOf('class="page-head"')));
    expect(head(m)).toContain(`<a class="btn quiet" href="https://github.com/orgs/${COURSE_ORG}/repositories?q=topic%3Adsl-materials" target="_blank" rel="noopener">See on GitHub`);
    expect(text(<MaterialsIndexScreen {...cp()} />)).toContain('A scheduled or manual release copies their folders to a semester. This page checks that the set-up files are in place, not their content; change content by pushing to the repo’s main branch.');
    const t = render(<TemplatesIndexScreen {...cp()} />);
    expect(head(t)).toContain(`href="https://github.com/orgs/${COURSE_ORG}/repositories?q=topic%3Adsl-assignment"`);
    for (const out of [m, t]) {
      expect(out).not.toContain('New semester');
      expect(out).not.toContain('Refresh');
      expect(out).not.toContain('Course on GitHub');
      expect(out).not.toContain('Publish website');
    }
  });
  it('lists materials with state, term, last change and Other repos', () => {
    const t = text(<MaterialsIndexScreen {...cp()} />);
    expect(t).toContain(MAT);
    expect(t).toContain('Ready');
    expect(t).toContain('Fall 2026');
    expect(t).not.toContain('public website');
    expect(t).toContain('Last change');
    expect(t).toContain('Course materials repos');
    expect(t).toContain('New course materials');
    expect(t).toContain('lecture-code-f2026');
    expect(t).toContain('Not course materials, so nothing to set up here. Can be released to a semester from the schedule.');
    // Other repos get the Open button, not a GitHub link.
    const other = render(<MaterialsIndexScreen {...cp()} />).split('<li>').find((li) => li.includes('lecture-code-f2026'))!;
    expect(other).toContain('aria-label="More ways to open lecture-code-f2026"');
    expect(other).not.toContain('<a class="btn small quiet"');
    expect(other).toContain('>Treat as course materials</button>');
    expect(t).not.toContain('assignment-9-draft');
    expect(t).not.toContain('old-thing');
    expect(render(<MaterialsIndexScreen {...cp()} />)).toContain(`href="#materials-${MAT}"`);
  });
  it('says why a materials repo is not ready: its first missing needed check, and counts its suggestions', () => {
    const m = STATUS.course!.materials[0];
    // Ready: no sentence, and the open weekly plan is a suggestion.
    expect(materialsReadiness(m)).toEqual({ state: 'ready', missing: null, suggestions: 1 });
    const checks = m.checks!.map((c) => (c.id === 'syllabus' || c.id === 'kind_folder' ? { ...c, done: false, why: `${c.id} is missing.` } : c));
    const todo = { ...m, state: 'todo', checks };
    // An older status: the syllabus still blocks, so it is needed; the first missing needed check names it.
    expect(materialsReadiness(todo).missing).toBe('kind_folder is missing.');
    const st: Loaded = { kind: 'ready', status: { ...STATUS, course: { ...STATUS.course!, materials: [todo] } }, sha: 's', stale: [] };
    for (const v of [<MaterialsIndexScreen {...cp({ loaded: st })} />, <CourseScreen {...cp({ loaded: st })} />]) {
      expect(render(v)).toContain('<span class="chip notready">Not ready</span>');
      expect(render(v)).toContain('<span class="r-sub">kind_folder is missing.</span>');
    }
    // The ready fixture shows the chip and a dotted count of its suggestions.
    const out = render(<MaterialsIndexScreen {...cp()} />);
    expect(out).toContain('<span class="chip ok">Ready</span><span class="chip suggest">1 suggestion</span>');
    expect(out).not.toContain('Syllabus written');
  });
  it('shows a template still being written as Not ready, not as a problem', () => {
    const st: Loaded = { kind: 'ready', status: { ...STATUS, problems: [], course: { ...STATUS.course!, templates: [{ repo: 'assignment-regression', state: 'todo' }] } }, sha: 's', stale: [] };
    for (const v of [<TemplatesIndexScreen {...cp({ loaded: st })} />, <CourseScreen {...cp({ loaded: st })} />]) {
      const out = render(v);
      expect(out).toContain('<span class="chip notready">Not ready</span>');
      expect(out).toContain('The brief (README.md) is not written yet.');
      expect(out).toContain('href="#template-assignment-regression">Settings');
      expect(out).not.toContain('href="#template-assignment-regression">Fix');
    }
  });
  it('says so when there are no other repos', () => {
    const t = text(<MaterialsIndexScreen {...cp({ files: new StaticFiles({}, {}, {}, { [COURSE_ORG]: [{ name: '.github' }] }) })} />);
    expect(t).toContain('No other repos.');
  });
  it('lists templates with title, teams, format, verdict and the cohorts that schedule them', () => {
    const out = render(<TemplatesIndexScreen {...cp({ cohortStates: { [COHORT_ORG]: withTemplate } })} />);
    const t = text(<TemplatesIndexScreen {...cp({ cohortStates: { [COHORT_ORG]: withTemplate } })} />);
    expect(t).toContain('Group project');
    expect(t).not.toContain('Assignment 3: Group project');
    expect(t).toContain('Has a problem');
    expect(t).toContain('In teams, Python files.');
    expect(t).toContain('Used in Fall 2026');
    expect(t).toContain('New assignment');
    expect(out).toContain('href="#template-assignment-3-f2026"');
    // No semester chip: a template is reused every semester (decision 0014).
    expect(out).not.toContain('chip term');
    expect(t).toContain('Each hand-out freezes a copy in that semester');
  });
});

describe('materials settings file tree', () => {
  it('heads the settings with the checklist by need, ticks included, and the chips in the page head', () => {
    const out = render(<MaterialsScreen {...cp({ entry: MAT })} />);
    expect(out).toContain('<span class="chip ok">Ready</span><span class="chip suggest">1 suggestion</span>');
    expect(out).toContain('<p class="sub-h">Needed</p>');
    expect(out).toContain('<p class="sub-h">Suggested</p>');
    expect((out.match(/<li class="done">/g) ?? []).length).toBe(2);
    // The syllabus and its weekly plan are one row (decision 0034).
    expect(out).toContain('<li class="open suggested"><span class="s-mark" aria-hidden="true"></span><span class="s-name">Syllabus<span class="s-opt">optional</span>');
    expect(out).toContain('<span class="s-why">The weekly plan (written from the schedule) is not in SYLLABUS.md yet.</span>');
    expect(out).not.toContain('required');
  });
  it('saves a withhold list that withholds nothing with the reviewed mark, Save enabled', () => {
    expect(withMark('')).toBe(`${REVIEWED_MARK}\n`);
    expect(withMark('# seeded comment\n\n')).toBe(`# seeded comment\n${REVIEWED_MARK}\n`);
    expect(withMark('solutions/\n')).toBe('solutions/\n');
    expect(withMark(`# c\n${REVIEWED_MARK}\n`)).toBe(`# c\n${REVIEWED_MARK}\n`);
    const seeded = new StaticFiles({ [`${COURSE_ORG}/${MAT}/.releaseignore`]: '# seeded comment\n' }, {}, { [`${COURSE_ORG}/${MAT}`]: ['a.md'] });
    // The withhold list's own Save, after its heading.
    const saveOf = (f: Files) => { const out = render(<MaterialsScreen {...cp({ entry: MAT, files: f })} />); return /<button[^>]*>Save<\/button>/.exec(out.slice(out.indexOf('Withheld from students')))?.[0] ?? ''; };
    // Unchanged but withholding nothing: Save is on, and would write the mark.
    expect(saveOf(seeded)).toContain('>Save<');
    expect(saveOf(seeded)).not.toContain('disabled');
    // The fixture's list has patterns and is unchanged: nothing to save.
    expect(saveOf(files)).toContain('disabled');
  });
  it('has Syllabus and Withheld only: no Folder kinds, no kind pickers (decision 0038)', () => {
    const out = render(<MaterialsScreen {...cp({ entry: MAT })} />);
    expect(out).not.toContain('Folder kinds');
    expect(out).not.toContain('aria-label="Kind of');
    expect(out).not.toContain('Kinds found');
    expect(out.match(/<ul class="file-tree">/g)?.length).toBe(1);
    expect([...out.matchAll(/<h2>([^<]+)/g)].map((m) => m[1].trim())).toEqual(['Syllabus', 'Withheld from students']);
  });
  it('badges every file and flags the rule that matches nothing, with no edit link per row', () => {
    const out = render(<MaterialsScreen {...cp({ entry: MAT })} />);
    const t = text(<MaterialsScreen {...cp({ entry: MAT })} />);
    expect(t).not.toContain('for the public website');
    expect(t).toContain('withheld');
    expect(t).toContain('released to students');
    expect(t).toContain('*.key matches no file');
    // Decision 0024 rule 10: the tree only withholds; editing is the Open button's job.
    expect(out).not.toContain(`/edit/main/lectures/05_trees/slides.html`);
    expect(out).not.toContain('Edit on GitHub');
    expect(out).not.toContain('class="file-list"');
    expect(t).not.toContain('only part of this repo');
  });
  it('says No rules yet, and leaves out Edit links while the default branch is unknown', () => {
    const bare = new StaticFiles({}, {}, { [`${COURSE_ORG}/${MAT}`]: ['a.md'] });
    const out = render(<MaterialsScreen {...cp({ entry: MAT, files: bare })} />);
    expect(out.match(/No rules yet\./g)).toHaveLength(1);
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
    const nav = render(<Sidenav courses={[course]} course={course} cohortStates={{ [COHORT_ORG]: ready, [OLD_ORG]: archived }} current="course" />);
    const t = nav.replace(/<[^>]+>/g, ' ');
    expect(t.indexOf('Public website')).toBeLessThan(t.indexOf('Semesters'));
    expect(nav).toContain(`href="?cohort=${COHORT_ORG}#dashboard"`);
    // The engine dates s5 later (beyond the horizon): only the template problem counts.
    expect(nav).toMatch(/aria-label="1 problem">1</);
    expect(nav).toMatch(/class="past"[^>]*><span class="node-name">Fall 2025<span class="n-soon"> archived<\/span>/);
    expect(t).toContain('Assignment templates');
    const inCohort = render(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{ [COHORT_ORG]: ready }} current="dashboard" />);
    expect(inCohort.match(/aria-current="page"/g)).toHaveLength(1);
  });
  it('shows the term, not the org, while a cohort status loads', () => {
    const t = text(<CourseScreen {...cp({ cohortStates: { [COHORT_ORG]: { kind: 'loading' } } })} />);
    expect(t).toContain('Fall 2026 Live Fall 2026');
    expect(t).not.toContain(OLD_ORG);
  });
  it('lists the course’s problems and gives each semester its count, now or soon only', () => {
    const t = text(<CourseScreen {...cp({ loaded: ready })} />);
    expect(t).toContain('Problems');
    expect(t).toContain('Marking of Assignment 3 cannot start.');
    expect(t).not.toContain('No problems.');
    expect(t).toContain('Fall 2025');
    expect(t).toMatch(/\d problems?/);
    const calm: Loaded = { kind: 'ready', status: { ...STATUS, problems: [] }, sha: 's', stale: [] };
    const none = text(<CourseScreen {...cp({ loaded: calm, cohortStates: { [COHORT_ORG]: calm, [OLD_ORG]: archived } })} />);
    expect(none).toContain('No problems on the course.');
  });
  it('a semester with no problems reads No problems, with no count badge, on the course page and Home', () => {
    const row = render(<CourseScreen {...cp()} />).split('<li>').find((li) => li.includes('Fall 2025'))!;
    expect(row).toContain('<span class="probs none">No problems</span>');
    expect(row).not.toContain('count-badge');
    const calm: Loaded = { kind: 'ready', status: { ...STATUS, problems: [] }, sha: 's', stale: [] };
    const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: '' };
    const home = render(<HomeScreen courses={[{ ...course, cohorts: [cohort] }]} semesters={[]} cohortStates={{ [COHORT_ORG]: calm }} now={0} user={user} />);
    expect(home).toContain('<span class="probs none">No problems</span>');
    expect(home).not.toContain('count-badge');
  });
});

describe('materials settings: the syllabus file is the newest semester’s semester.yml (decision 0039)', () => {
  const tree = ['E1282.pdf', 'Syllabus.md', 'lectures/01/a.pdf'];
  const SEM = `${COHORT_ORG}/semester-config/semester.yml`;
  const withSem = new StaticFiles({ [SEM]: '# INSTRUCTOR-OWNED\nstudent_site:\n  syllabus: Syllabus.md\n' }, {}, { [`${COURSE_ORG}/${MAT}`]: tree });

  it('shows the semester’s syllabus selected, the repo’s top-level Markdown files as the choices, and the file it is saved in', () => {
    const out = render(<MaterialsScreen {...cp({ entry: MAT, files: withSem })} />);
    expect(out).toContain('<option value="Syllabus.md" selected>Syllabus.md</option>');
    expect(out).toContain('<option value="SYLLABUS.md">SYLLABUS.md</option>');
    expect(out).not.toContain('value="E1282.pdf"');
    expect(text(<MaterialsScreen {...cp({ entry: MAT, files: withSem })} />)).toContain('saved for Fall 2026');
    expect(out).toContain(`/edit/main/semester.yml`);
    expect(out).not.toContain('materials.yml');
  });
  it('writes student_site.syllabus, and the default as no key', () => {
    expect(withSyllabus('# c\n', 'Syllabus.md')).toEqual({ text: '# c\nstudent_site:\n  syllabus: Syllabus.md\n' });
    expect(withSyllabus('# c\nstudent_site:\n  syllabus: Syllabus.md\n', 'SYLLABUS.md')).toEqual({ text: '# c\n' });
    expect(withSyllabus('people: [\n', 'x.md')).toEqual({ error: 'semester.yml does not parse; fix it with Edit the file directly.' });
  });
  it('the public website shows its settings from course.yml’s open_course_site, off when there is none', () => {
    const meta = new StaticFiles({ [`${COURSE_ORG}/.github/course.yml`]: 'course:\n  name: ML\n  description: About ML.\n' });
    const t = text(<WebsiteScreen {...cp({ files: meta })} />);
    expect(t).not.toContain('publish.yml');
    expect(t).not.toContain('opencourse.yml');
    expect(t).toContain('The website is off: Publish refuses until it is on and saved.');
    expect(t).toContain('Description About ML.');
    expect(t).toContain('Not set');
  });
});

describe('materials checklist and edit links (decision 0024 rules 8 and 9)', () => {
  const checks = STATUS.course!.materials[0].checks!;

  it('gives the syllabus and the withheld patterns a ?, with no Kinds found fold', () => {
    const current = checks.filter((c) => c.id !== 'kind_folder');
    const out = render(<MaterialsChecklist checks={current} />);
    // The syllabus (one check with its weekly plan) has its own ?.
    expect(out.match(/aria-label="About this check"/g)).toHaveLength(1);
    expect(out.match(/aria-label="About the syllabus"/g)).toHaveLength(1);
    expect(out).toContain('Saving the list once, even empty, marks it reviewed.');
    expect(out).not.toContain('Kinds found');
    // The suggested: the syllabus row (its open part, the plan, is a suggestion), the withheld patterns.
    const t = text(<MaterialsChecklist checks={current} />);
    expect(t.indexOf('Syllabus')).toBeLessThan(t.indexOf('Withheld patterns reviewed'));
    expect(t).not.toContain('Weekly plan in the syllabus');
    // An older status's folder check carries its kinds: no fold for them any more.
    expect(render(<MaterialsChecklist checks={checks} />)).not.toContain('Kinds found');
  });

  it('points an edit link at GitHub’s new-file page while the file does not exist', () => {
    expect(newFileUrl('o', 'r', 'materials.yml', 'trunk')).toBe('https://github.com/o/r/new/trunk?filename=materials.yml');
    const absent = render(<EditFile org="o" repo="r" path="materials.yml" exists={false} />);
    expect(absent).toContain('href="https://github.com/o/r/new/main?filename=materials.yml"');
    expect(absent).toContain('Create the file on GitHub');
    expect(render(<EditFile org="o" repo="r" path="materials.yml" />)).toContain('href="https://github.com/o/r/edit/main/materials.yml"');
  });

  it('keeps two short file links, with the full org/repo/path as the tooltip', () => {
    const absent = render(<EditFile org="o" repo="r" path="materials.yml" exists={false} />);
    const present = render(<EditFile org="o" repo="r" path="materials.yml" />);
    expect(absent).toContain('title="o/r/materials.yml"');
    expect(present).toContain('title="o/r/materials.yml"');
    expect(present).toContain('target="_blank"');
    expect(present).toContain('Edit the file directly');
    expect(absent).not.toContain('Create o/r');
    expect(present).not.toContain('Lives in');
  });

  it('on the settings screen: new-file links for an absent semester.yml, the editor for a present .releaseignore', () => {
    const out = render(<MaterialsScreen {...cp({ entry: MAT })} />);
    expect(out).toContain(`href="https://github.com/${COHORT_ORG}/semester-config/new/main?filename=semester.yml"`);
    expect(out).not.toContain('materials.yml');
    expect(out).toContain(`href="https://github.com/${COURSE_ORG}/${MAT}/edit/main/.releaseignore"`);
    // SYLLABUS.md is in the tree, though its text is not loaded: it exists.
    expect(out).toContain(`/edit/main/SYLLABUS.md`);
  });
});
