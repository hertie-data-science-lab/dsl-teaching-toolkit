// Decision 0035 rules 6-8 (WP SC-D): the Assignments tab's cards and "Your marks", and each
// assignment's own page `#assignment-<slug>` with the 0.9.0 page's blocks, the team steps and
// the mark, under the role guards; the route and the links that land on it.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import type { Semester } from '../src/model/discovery';
import { parseGradebook, type Mine } from '../src/model/mine';
import type { SemesterAssignment, SemesterFacts } from '../src/model/student';
import { weekItems } from '../src/model/week';
import { parseHash, studentNavKey } from '../src/router';
import { StudentScreen, studentScreen } from '../src/screens/Student';
import { AssignmentPage, stateWord } from '../src/screens/StudentAssignment';
import { AssignmentsView } from '../src/screens/StudentAssignments';

const ORG = 'hertie-dsl-demo-f2026';
const LOGIN = 'octo';
const TZ = 'Europe/Berlin';
const NOW = Date.parse('2026-10-08T12:00:00+02:00');
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&rsquo;/g, '’').replace(/\s+/g, ' ');

const A: SemesterAssignment = {
  slug: 'a1', title: 'Assignment 1', subtitle: 'Regression', handout: '2026-09-28T09:00:00', due: '2026-10-12T23:59:00', lateCutoff: '2026-10-22T23:59:00',
  lateRule: '10% per day, up to 10 days', cutoffSentence: 'What is on main at the grading cutoff is what is marked.', submitVia: 'assignment_repo', privateRepo: true,
  submitUrl: '', group: false, teamFormation: null, solutionShown: null, maxPoints: '20', handedOut: true, brief: 'Fit a **model**.', shape: 'assignment-repo-private',
  shapeNote: 'NB: this repo is private - only you and the teaching team can read it.', tbc: false, teams: [],
};
const facts = (assignments: SemesterAssignment[]): SemesterFacts => ({
  courseName: 'Deep Learning', timezone: TZ, rows: [], assignments, instructors: [], archive: null, latePolicy: [], materialsRepos: ['materials'], homeMarkdown: '', announcements: [], syllabus: null,
});
const mine = (over: Partial<Mine> = {}): Mine => ({ units: { a1: { slug: 'a1', repo: 'a1-octo', team: null, members: null, shared: false } }, gradebook: null, auditor: false, ...over });
const page = (a: SemesterAssignment, m: Mine | null, over: { studentView?: boolean; unknownRole?: boolean; now?: number } = {}) =>
  <AssignmentPage slug={a.slug} org={ORG} facts={facts([a])} mine={m} now={over.now ?? NOW} studentView={over.studentView ?? false} unknownRole={over.unknownRole} receipts={{}} login={LOGIN} hint="About this assignment." />;
const GROUP: SemesterAssignment = {
  ...A, slug: 'p', title: 'Project', subtitle: '', group: true, teamFormation: { closes: '2026-10-20T23:59:00', cap: 3 },
  teams: [{ name: 'team-x', members: 2, cap: 3 }, { name: 'team-y', members: 3, cap: 3 }],
};

describe('the Assignments tab', () => {
  it('opens a card only while the assignment is open to the student, its title linking the page', () => {
    const closed = { ...A, slug: 'a0', title: 'Assignment 0', handout: '2026-09-01T09:00:00', due: '2026-09-10T23:59:00', lateCutoff: '2026-09-12T23:59:00' };
    const late = { ...A, slug: 'a2', title: 'Assignment 2', due: '2026-10-05T23:59:00', lateCutoff: '2026-10-15T23:59:00' };
    const pending = { ...A, slug: 'a3', title: 'Assignment 3', handout: '2026-11-01T09:00:00', due: '2026-11-15T23:59:00', handedOut: false, brief: '' };
    const out = render(<AssignmentsView org={ORG} facts={facts([pending, A, closed, late])} mine={mine()} now={NOW} studentView={false} receipts={{}} login={LOGIN} />);
    const cards = [...out.matchAll(/<details class="panel section a-card"( open)? aria-label="([^"]+)"/g)].map((m) => `${m[2]}:${m[1] ? 'open' : 'closed'}`);
    // Due order; open and late window open, marking and not handed out folded.
    expect(cards).toEqual(['Assignment 0:closed', 'Assignment 2:open', 'Assignment 1:open', 'Assignment 3:closed']);
    expect(out).toContain(`<a href="?semester=${ORG}#assignment-a1">Assignment 1</a>`);
    expect(text(<AssignmentsView org={ORG} facts={facts([A])} mine={mine()} now={NOW} studentView={false} receipts={{}} login={LOGIN} />)).toMatch(/Assignment 1 Regression open Due Mon 12 Oct 23:59/);
    expect(out).not.toContain('Late work in this course');
  });

  it('puts "Your marks" first once a mark is returned, and the chip reads the mark', () => {
    const gb = parseGradebook("total: '18'\nassignments:\n  a1:\n    final_grade: '18'\n    max_points: '20'\n    penalty: '-10%'\n    feedback: Good.\n");
    const t = text(<AssignmentsView org={ORG} facts={facts([A])} mine={mine({ gradebook: gb })} now={NOW} studentView={false} receipts={{}} login={LOGIN} />);
    expect(t).toMatch(/^ Your marks Assignment Mark Late penalty Assignment 1: Regression 18 \/ 20 -10% Semester total: 18 Your gradebook on GitHub/);
    expect(t).toContain('returned 18 / 20');
    expect(t).toContain('Mark 18 / 20 Late penalty -10% Feedback Good.');
    expect(stateWord('returned', gb!.entries.a1)).toBe('returned 18 / 20');
    expect(stateWord('open', undefined)).toBe('open');
    // No mark yet: no table; the Student view says whose marks show here; an auditor gets nothing.
    expect(text(<AssignmentsView org={ORG} facts={facts([A])} mine={mine()} now={NOW} studentView={false} receipts={{}} login={LOGIN} />)).not.toContain('Your marks');
    expect(text(<AssignmentsView org={ORG} facts={facts([A])} mine={null} now={NOW} studentView receipts={{}} login={LOGIN} />)).toContain('A student’s marks show here');
    const aud = text(<AssignmentsView org={ORG} facts={facts([A])} mine={mine({ auditor: true, gradebook: gb })} now={NOW} studentView={false} receipts={{}} login={LOGIN} />);
    expect(aud).not.toContain('Your marks');
    expect(aud).not.toContain('returned');
  });
});

describe('the assignment page', () => {
  it('has the 0.9.0 head and blocks in order: dates, callout, brief, shape note', () => {
    const out = render(page(A, mine()));
    expect(out).toMatch(/<p class="kicker">Assignment 1<\/p><h2 class="h1">Regression /);
    expect(out).toContain('<span class="chip asg">open</span>');
    const t = text(page(A, mine()));
    expect(t).toContain('Released on Mon 28 Sep Due Mon 12 Oct 23:59 Worth 20 points');
    expect(t).toContain('Your work goes in your private repo a1-octo . Clone it, commit as you go, and push to main - that push is your submission. What is on main at the grading cutoff is what is marked. Late work: 10% per day, up to 10 days (until Thu 22 Oct 23:59).');
    expect(out).toContain(`href="https://github.com/${ORG}/a1-octo"`);
    expect(out).toContain('aria-label="More ways to open a1-octo"');
    // The brief is shown, not folded, and the shape note is italic under it.
    expect(out).toMatch(/<article class="a-brief">.*model.*<\/article><p class="shape-note"><em>NB: this repo is private/);
    // Without a subtitle the title is the heading.
    expect(render(page({ ...A, subtitle: '' }, mine()))).toMatch(/<h2 class="h1">Assignment 1 /);
  });

  it('says each shape’s sentence', () => {
    const pub = text(page({ ...A, shape: 'assignment-repo-public', privateRepo: false }, mine()));
    expect(pub).toContain('Your work goes in your repo a1-octo .');
    const box = text(page({ ...A, submitVia: 'shared_dropbox_repo', shape: 'shared-dropbox-repo', privateRepo: false }, mine({ units: { a1: { slug: 'a1', repo: 'a1-submissions', team: null, members: null, shared: true } } })));
    expect(box).toContain('Push your work into the octo/ folder of a1-submissions - that push is your submission.');
    const ext = { ...A, submitVia: 'external' as const, shape: 'external', submitUrl: 'https://moodle.example.org/x', cutoffSentence: '', shapeNote: '' };
    const e = render(page(ext, mine({ units: {} })));
    expect(e).toContain('href="https://moodle.example.org/x"');
    expect(text(page(ext, mine({ units: {} })))).toContain('Submit on moodle.example.org Handed in outside GitHub.');
    expect(text(page({ ...ext, submitUrl: '' }, mine({ units: {} })))).toContain('Handed in outside GitHub. See the brief.');
    expect(text(page(ext, mine({ units: {} })))).not.toContain('Late work');
  });

  it('says the 0.9.0 pending sentence before the hand-out', () => {
    const before = { ...A, handedOut: false, brief: '', handout: '2026-10-20T09:00:00', due: '2026-11-01T23:59:00' };
    const t = text(page(before, mine({ units: {} })));
    expect(t).toContain('Hands out on Tue 20 Oct');
    expect(t).toContain('Assignment 1 is not yet released - your private a1-octo repo appears when it is.');
    expect(t).not.toContain('Clone it');
    expect(text(page({ ...before, submitVia: 'external', shape: 'external' }, null))).toContain('Assignment 1 is not yet released - the brief appears here when it is.');
    expect(text(page({ ...before, submitVia: 'shared_dropbox_repo', shape: 'shared-dropbox-repo' }, null))).toContain('the a1-submissions drop box appears when it is.');
    expect(text(page(before, null, { studentView: true }))).toContain('your private a1-<your-handle> repo appears when it is.');
  });

  it('promises nothing in the Student view, to an auditor, or when the role could not be read', () => {
    const sv = text(page(A, null, { studentView: true }));
    expect(sv).toContain('a1-<your-handle>');
    expect(sv).toContain('A student’s repo, team and receipts show here.');
    expect(sv).not.toContain(LOGIN);
    const aud = render(page(A, mine({ auditor: true })));
    expect(aud).toContain('As an auditor you hand in no work for this assignment.');
    expect(aud).not.toContain('a1-octo');
    expect(aud).not.toContain('class="chip');
    expect(aud).not.toContain('shape-note');
    const unknown = text(page(A, null, { unknownRole: true }));
    expect(unknown).toContain('Could not read your role');
    expect(unknown).not.toContain('Clone it');
  });

  it('shows the mark and its feedback once returned', () => {
    const gb = parseGradebook("assignments:\n  a1:\n    final_grade: '18'\n    max_points: '20'\n    submitted: 12 Oct 20:00\n    score: {Q1: '8', Q2: '10'}\n    feedback: Good.\n");
    const out = render(page(A, mine({ gradebook: gb })));
    expect(out).toContain('<span class="chip ok">returned 18 / 20</span>');
    expect(text(page(A, mine({ gradebook: gb })))).toMatch(/Mark 18 \/ 20 Submitted 12 Oct 20:00 Feedback Good\. By question Q1 8 Q2 10 $/);
    expect(text(page(A, null, { studentView: true }))).not.toContain('Mark');
  });

  it('lists the files its rows carry with their button row, before the brief', () => {
    const link = { name: 'data.csv', repo: 'materials', path: 'a1/data.csv', url: `https://github.com/${ORG}/materials/blob/main/a1/data.csv` };
    const row = { id: 'a1:handout', kind: 'assignment', when: '2026-09-28T09:00:00', allDay: false, title: 'Assignment 1', subtitle: '', details: '', assignment: 'a1', released: true, links: [link], tbc: false, readings: [], readingList: '', readingsPending: false, tabs: ['assignment'] };
    const out = render(<AssignmentPage slug="a1" org={ORG} facts={{ ...facts([A]), rows: [row] }} mine={mine()} now={NOW} studentView={false} receipts={{}} login={LOGIN} hint="" />);
    expect(out).toMatch(/<ul class="session-files">.*data\.csv.*source.*<\/ul><article class="a-brief">/);
    expect(render(page(A, mine()))).not.toContain('session-files');
  });

  it('names an unknown assignment and links back to the list', () => {
    const out = render(<AssignmentPage slug="nope" org={ORG} facts={facts([A])} mine={null} now={NOW} studentView={false} receipts={{}} login={LOGIN} hint="" />);
    expect(out).toContain('This semester has no assignment nope.');
    expect(out).toContain(`href="?semester=${ORG}#assignments"`);
  });
});

describe('the team steps', () => {
  it('while formation is open: step 1 with the teams table and the form button, step 2 dimmed until in a team', () => {
    const none = mine({ units: { p: { slug: 'p', repo: null, team: null, members: null, shared: false } } });
    const out = render(page(GROUP, none));
    const t = text(page(GROUP, none));
    expect(t).toContain('1 Form your team Not in a team yet You pick your own team for this assignment, of up to 3 people. Start a team or join one with Join or create a team - team formation closes on Tue 20 Oct 23:59. Your team’s submission repo appears once the team does.');
    expect(t).toContain('Teams so far Team Members Places left team-x 2 of 3 1 team-y 3 of 3 full');
    expect(out).toContain('aria-expanded="false">Join or create a team</button>');
    // The form is folded: no assignment dropdown anywhere, no request sent.
    expect(out).not.toContain('<select');
    expect(out).toMatch(/<h3 class="a-step" aria-disabled="true"><span class="step-num">2<\/span> Open your submission repo<span class="footnote a-step-hint"> Form or join a team first\.<\/span>/);
    expect(t).toContain('Your work goes in your private repo p-<your-team> .');
    const inTeam = mine({ units: { p: { slug: 'p', repo: 'p-team-x', team: 'team-x', members: [LOGIN, 'b'], shared: false } } });
    const it2 = render(page(GROUP, inTeam));
    expect(text(page(GROUP, inTeam))).toContain('✓ You’re in team team-x');
    expect(text(page(GROUP, inTeam))).toContain('team-x (your team) 2 of 3 1');
    expect(it2).toContain('<h3 class="a-step"><span class="step-num">2</span>');
    expect(text(page(GROUP, inTeam))).toContain('Contributions Fill in CONTRIBUTIONS.md');
  });

  it('with no team yet invites the first; once formation closes the teams stay, without the form', () => {
    expect(text(page({ ...GROUP, teams: [] }, mine({ units: {} })))).toContain('No teams yet - be the first: choose Create a new team .');
    const after = Date.parse('2026-10-21T12:00:00+02:00');
    const closed = render(page(GROUP, mine({ units: {} }), { now: after }));
    expect(closed).not.toContain('Form your team');
    expect(closed).not.toContain('Join or create a team');
    expect(text(page(GROUP, mine({ units: {} }), { now: after }))).toContain('Teams so far Team Members Places left team-x 2 of 3 1');
    // An individual assignment has neither.
    expect(render(page(A, mine()))).not.toContain('Teams so far');
  });

  it('in the Student view shows the steps without the form or anyone’s team', () => {
    const out = render(page(GROUP, null, { studentView: true }));
    expect(out).toContain('Form your team');
    expect(out).not.toContain('team-found');
    expect(out).not.toContain('aria-expanded');
    expect(out).toContain('A student joins or creates a team here');
    expect(out).not.toContain('aria-disabled');
  });
});

describe('the route', () => {
  it('opens #assignment-<slug> as the page, lit under Assignments; with no slug, the list', () => {
    expect(parseHash('#assignment-a1', true)).toEqual({ screen: 'assignment', entry: 'a1' });
    expect(studentScreen('assignment')).toBe('assignment');
    expect(studentNavKey('assignment')).toBe('assignments');
    expect(studentNavKey('week')).toBe('week');
    const sem: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'c', courseName: 'Deep Learning', archived: false, role: 'student' };
    expect(render(<StudentScreen semester={sem} screen="assignment" studentView={false} now={NOW} />)).toMatch(/<h2 class="h1">Assignments /);
    expect(render(<StudentScreen semester={{ ...sem, archived: true }} screen="assignment" entry="a1" studentView={false} now={NOW} />)).toMatch(/<h2 class="h1">Assignments /);
  });

  it('This week’s lines about one assignment open its page', () => {
    const f = { ...facts([GROUP]), rows: [{ id: 'p:due', kind: 'due', when: '2026-10-12T23:59:00', allDay: false, title: 'Project', subtitle: '', details: '', assignment: 'p', released: true, links: [], tbc: false, readings: [], readingList: '', readingsPending: false, tabs: ['due'] }] };
    const gb = parseGradebook("assignments:\n  p:\n    final_grade: '9'\n", '2026-10-07T08:00:00Z');
    const lines = weekItems(f, mine({ units: {}, gradebook: gb }), NOW, [{ slug: 'p', when: '2026-10-08T08:00:00Z' }]);
    expect(Object.fromEntries(lines.map((l) => [l.kind, l.screen]))).toEqual({ due: 'assignment-p', teams: 'assignment-p', marks: 'assignment-p', patch: 'assignment-p' });
  });
});
