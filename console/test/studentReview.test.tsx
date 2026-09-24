// Review follow-ups on the student screens: team repos matched to their own assignment, the
// own repo winning, receipts under either label, the renamed site keys, ?join validation, the
// byte cache and the deck viewer hand-over.

import { render } from 'preact-render-to-string';
import { describe, expect, it, vi } from 'vitest';
import { GitHubClient } from '../src/github/client';
import { openDeck, type DeckDeps } from '../src/model/deckTab';
import { ownerSlug, parseGradebook, readReceipts, unitOf } from '../src/model/mine';
import { SiteSource, type SemesterAssignment, type SemesterFacts } from '../src/model/student';
import { parseSearch } from '../src/router';
import { MarksView } from '../src/screens/Student';
import { FakeGitHub, fileBody } from './fake';

const ORG = 'hertie-dsl-demo-f2026';
const text = (v: preact.VNode) => render(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const client = (f: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: f.fetch });
const repo = (name: string, push = true) => ({ name, full_name: `${ORG}/${name}`, private: true, default_branch: 'main', html_url: '', permissions: { push, pull: true } });
const asg = (slug: string, over: Partial<SemesterAssignment> = {}): SemesterAssignment => ({
  slug, title: slug, subtitle: '', handout: null, due: null, lateCutoff: null, lateRule: '', cutoffSentence: '', submitVia: 'assignment_repo', privateRepo: true, submitUrl: '', group: true, teamFormation: null, solutionShown: null, maxPoints: '', handedOut: true, ...over,
});

describe('team repos and own repos', () => {
  const slugs = ['assignment-3', 'assignment-3-project'];

  it('gives a repo to the longest slug it starts with, never by bare prefix', () => {
    expect(ownerSlug('assignment-3-project-team-x', slugs)).toBe('assignment-3-project');
    expect(ownerSlug('assignment-3-team-x', slugs)).toBe('assignment-3');
    expect(ownerSlug('assignment-3-project', slugs)).toBe('assignment-3-project');
    expect(ownerSlug('assignment-30-team', slugs)).toBeNull();
  });

  it('does not take another assignment’s team repo, or its template, as this one’s team', () => {
    const repos = [repo('assignment-3-project'), repo('assignment-3-project-team-x')];
    expect(unitOf(asg('assignment-3'), repos, 'octo', slugs)).toMatchObject({ repo: null, team: null });
    expect(unitOf(asg('assignment-3-project'), repos, 'octo', slugs)).toMatchObject({ repo: 'assignment-3-project-team-x', team: 'team-x' });
    expect(unitOf(asg('assignment-3'), [...repos, repo('assignment-3-team-y')], 'octo', slugs)).toMatchObject({ repo: 'assignment-3-team-y', team: 'team-y' });
  });

  it('a student’s own <slug>-<handle> wins on a group assignment, with no team looked for', () => {
    const repos = [repo('assignment-3-project-team-x'), repo('assignment-3-project-octo')];
    expect(unitOf(asg('assignment-3-project'), repos, 'Octo', slugs)).toEqual({ slug: 'assignment-3-project', repo: 'assignment-3-project-octo', team: null, shared: false });
  });
});

describe('Submission receipts', () => {
  const issue = (n: number, title: string, pr = false) => ({ number: n, title, state: 'open', html_url: `https://github.com/i/${n}`, created_at: '', comments: 0, labels: [], ...(pr ? { pull_request: {} } : {}) });

  it('reads the new label first, skips pull requests and prefers the Submission receipts title', async () => {
    const f = new FakeGitHub().on('GET', /labels=dsl-receipts/, [issue(9, 'Fix typo', true), issue(3, 'Other'), issue(2, 'Submission receipts')]);
    expect((await readReceipts(client(f), ORG, 'r'))?.url).toBe('https://github.com/i/2');
    expect(f.seen.some((s) => s.url.includes('dsl-feedback'))).toBe(false);
  });

  it('falls back to the old label, then to the title', async () => {
    const old = new FakeGitHub().on('GET', /labels=dsl-receipts/, []).on('GET', /labels=dsl-feedback/, [issue(1, 'Submission receipts')]);
    expect((await readReceipts(client(old), ORG, 'r'))?.url).toBe('https://github.com/i/1');
    const bare = new FakeGitHub().on('GET', /labels=/, [issue(5, 'Submission receipts', true)]).on('GET', /issues\?state=all/, [issue(5, 'Submission receipts', true), issue(4, 'Submission receipts')]);
    expect((await readReceipts(client(bare), ORG, 'r'))?.url).toBe('https://github.com/i/4');
  });
});

describe('the renamed site', () => {
  it('reads `kind` over `type` and the semester-archived row', async () => {
    const f = new FakeGitHub()
      .on('GET', `/repos/${ORG}/${ORG}.github.io/contents/_events`, [{ name: 'semester-archived.md', path: '_events/semester-archived.md', sha: 'a', type: 'file' }, { name: 'term-start.md', path: '_events/term-start.md', sha: 'b', type: 'file' }])
      .on('GET', `/repos/${ORG}/${ORG}.github.io/contents/_events/semester-archived.md`, fileBody('x', '---\nkind: special_event\ntype: special_event\ndate: 2027-01-12T09:00:00\ntitle: "Semester archived"\n---\n'))
      .on('GET', `/repos/${ORG}/${ORG}.github.io/contents/_events/term-start.md`, fileBody('x', '---\nkind: term_date\ntype: special_event\ndate: 2026-08-04T09:00:00\ntitle: "Semester starts"\n---\n'));
    const facts = (await new SiteSource(client(f)).facts(ORG))!;
    expect(facts.archive).toBe('2027-01-12T09:00:00');
    expect(facts.rows.find((r) => r.id === 'term-start')?.kind).toBe('term_date');
  });
});

describe('Marks', () => {
  const facts = { timezone: 'Europe/Berlin', rows: [], assignments: [], instructors: [], archive: null, latePolicy: [], materialsRepos: [] } as SemesterFacts;

  it('shows the semester total over an empty gradebook, and says no marks yet', () => {
    const t = text(<MarksView org={ORG} login="octo" facts={facts} gradebook={parseGradebook('total: 88\nassignments: {}\n')} studentView={false} />);
    expect(t).toContain('Semester total: 88');
    expect(t).toContain('No marks yet');
    expect(t).not.toContain('Term total');
  });
});

describe('?join', () => {
  it('takes only an organisation name', () => {
    expect(parseSearch('?join=hertie-dsl-demo-f2026').join).toBe('hertie-dsl-demo-f2026');
    for (const bad of ['?join=-x', '?join=a/b', '?join=a%20b', '?join=', '?join=%3Cscript%3E']) expect(parseSearch(bad).join).toBeUndefined();
  });
});

describe('the byte cache', () => {
  it('reads a blob once by sha, however often it is opened', async () => {
    const f = new FakeGitHub().on('GET', '/repos/o/r/contents/a.css', fileBody('a.css', 'x{}', 's1'));
    const c = client(f);
    await c.getBytes('o', 'r', 'a.css', 's1', 3);
    await c.getBytes('o', 'r', 'a.css', 's1', 3);
    expect(f.seen.length).toBe(1);
    c.clearCache();
    await c.getBytes('o', 'r', 'a.css', 's1', 3);
    expect(f.seen.length).toBe(2);
  });
});

describe('the deck viewer hand-over', () => {
  it('opens deck.html with noopener and a random fragment, answers one ready with the deck, and closes', () => {
    vi.useFakeTimers();
    const sent: unknown[] = [];
    const ch = { onmessage: null as ((e: MessageEvent) => void) | null, postMessage: (m: unknown) => sent.push(m), close: vi.fn() };
    const opened: string[][] = [];
    const deps: DeckDeps = { open: (...a) => opened.push(a), channel: (n) => (expect(n).toBe('dsl-deck-00ff'), ch), random: () => '00ff', base: '/dsl-teaching-toolkit/' };
    openDeck('<html>deck</html>', 'deck.html', deps);
    expect(opened).toEqual([['/dsl-teaching-toolkit/deck.html#00ff', '_blank', 'noopener']]);
    ch.onmessage!({ data: { nope: 1 } } as MessageEvent);
    expect(sent).toEqual([]);
    ch.onmessage!({ data: { ready: true } } as MessageEvent);
    ch.onmessage!({ data: { ready: true } } as MessageEvent);
    expect(sent).toEqual([{ html: '<html>deck</html>', title: 'deck.html' }]);
    expect(ch.close).toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('closes the channel when no viewer asks', () => {
    vi.useFakeTimers();
    const ch = { onmessage: null, postMessage: vi.fn(), close: vi.fn() };
    openDeck('x', 'x', { open: () => null, channel: () => ch, random: () => 'aa', base: '/' });
    vi.advanceTimersByTime(30001);
    expect(ch.close).toHaveBeenCalled();
    expect(ch.postMessage).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
