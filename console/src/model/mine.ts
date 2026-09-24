// A student's own facts in one semester, read from GitHub with their own token: which
// assignment repos they hold (their own `<slug>-<handle>`, their team's `<slug>-<team>`, the
// shared drop box), their team (from the repo, or from their GitHub teams where the shape
// makes no team repo) and its members, whether they audit, the Submission receipts thread in
// each repo, and their gradebook `grades-<handle>/grades.yml`. Nothing here reads another student's data:
// GitHub shows a student only the repos they were granted, and a team repo counts as theirs
// only where they can push to it (a demo org's public repos are readable by anyone).

import { parse } from 'yaml';
import type { GhIssue, GhRepo, GhTeam, GitHubClient } from '../github/client';
import type { SemesterAssignment } from './student';
import type { PatchLine } from './week';

/** The receipts issue's labels, newest first: `dsl-receipts` since the rename, `dsl-feedback` on older issues (course.RECEIPTS_ISSUE_LABELS). */
export const RECEIPTS_LABELS = ['dsl-receipts', 'dsl-feedback'];
export const RECEIPTS_TITLE = 'Submission receipts';
/** The semester's read-only role team (course.AUDITORS_TEAM). Secret, but a member may read their own membership. */
export const AUDITORS_TEAM = 'auditors';

export interface MyUnit {
  slug: string;
  /** The repo the student submits to, or null (external, or not handed out to them yet). */
  repo: string | null;
  /** The team name for a group assignment. */
  team: string | null;
  /** The team's members, when GitHub lets the student read the team. */
  members: string[] | null;
  /** The whole semester pushes into this repo, each unit into its own folder. */
  shared: boolean;
}

export interface MarkEntry {
  finalGrade: string;
  maxPoints: string;
  /** Per question ({Q1: "14"}) or one value; null when absent. */
  score: string | Record<string, string> | null;
  feedback: string;
  /** Feedback per question, where the marker wrote any (WP-B3). */
  questionFeedback: Record<string, string>;
  submitted: string;
  daysLate: string;
  penalty: string;
  team: string;
  teamFeedback: string;
}

export interface Gradebook {
  entries: Record<string, MarkEntry>;
  /** A total over the term, when the gradebook carries one. */
  total: string;
  /** When grades.yml last changed (its newest commit), or null. */
  updated: string | null;
}

export interface Mine {
  units: Record<string, MyUnit>;
  /** null: no gradebook yet (none has been returned, or the account has none: an auditor). */
  gradebook: Gradebook | null;
  /** The person audits this semester: materials and schedule, no repos, teams or marks. */
  auditor: boolean;
}

const flat = (v: unknown): string => (v === null || v === undefined ? '' : typeof v === 'object' ? '' : String(v).trim());

function mapOf(v: unknown): Record<string, string> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
  return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, flat(x)]).filter(([, x]) => x));
}

/** One assignment's gradebook block, whichever of today's and WP-B3's spellings it uses; absent fields are ''. */
export function markEntry(v: unknown): MarkEntry {
  const b = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>;
  const fb = b.feedback;
  const score = b.score && typeof b.score === 'object' ? mapOf(b.score) : flat(b.score) || null;
  return {
    finalGrade: flat(b.final_grade),
    maxPoints: flat(b.max_points),
    score,
    feedback: typeof fb === 'object' ? flat((fb as Record<string, unknown>)?.overall) : flat(fb),
    questionFeedback: { ...(typeof fb === 'object' ? mapOf(fb) : {}), ...mapOf(b.question_feedback), ...mapOf(b.feedback_per_question) },
    submitted: flat(b.submitted),
    daysLate: flat(b.days_late),
    penalty: flat(b.penalty),
    team: flat(b.team),
    teamFeedback: flat(b.team_feedback),
  };
}

/** grades.yml as the student reads it; null when it does not parse. */
export function parseGradebook(text: string, updated: string | null = null): Gradebook | null {
  let v: unknown;
  try {
    v = parse(text);
  } catch {
    return null;
  }
  if (!v || typeof v !== 'object') return null;
  const doc = v as Record<string, unknown>;
  const list = doc.assignments && typeof doc.assignments === 'object' ? (doc.assignments as Record<string, unknown>) : {};
  const entries = Object.fromEntries(Object.entries(list).map(([k, x]) => {
    const e = markEntry(x);
    delete e.questionFeedback.overall;
    return [k, e];
  }));
  return { entries, total: flat(doc.total ?? doc.term_total), updated };
}

/** Whether marks for `slug` have come back. */
export const isMarked = (g: Gradebook | null, slug: string) => !!g?.entries[slug]?.finalGrade;

/**
 * The assignment a repo name belongs to: the LONGEST known slug it equals or starts with
 * (`assignment-3-project-team-x` is assignment-3-project's, not assignment-3's), or null.
 */
export function ownerSlug(name: string, slugs: string[]): string | null {
  const n = name.toLowerCase();
  let best: string | null = null;
  for (const s of slugs) {
    const l = s.toLowerCase();
    if ((n === l || n.startsWith(`${l}-`)) && (!best || l.length > best.length)) best = s;
  }
  return best;
}

/**
 * The repo among `repos` that is the student's for assignment `a`, and its team. `slugs` is
 * every assignment of the semester, so a team repo is matched to its own assignment and never
 * by a bare prefix. The student's own `<slug>-<handle>` wins wherever it exists, a group
 * assignment included (no team is looked for then).
 */
export function unitOf(a: SemesterAssignment, repos: GhRepo[], login: string, slugs: string[] = [a.slug]): Omit<MyUnit, 'members'> {
  const lc = (s: string) => s.toLowerCase();
  const none = { slug: a.slug, repo: null, team: null, shared: false };
  if (a.submitVia === 'external') return none;
  if (a.submitVia === 'shared_dropbox_repo') {
    const box = repos.find((r) => lc(r.name) === lc(`${a.slug}-submissions`));
    return { ...none, repo: box?.name ?? null, shared: true };
  }
  const own = repos.find((r) => lc(r.name) === lc(`${a.slug}-${login}`));
  if (own) return { ...none, repo: own.name };
  if (!a.group) return none;
  // A team's repo: `<slug>-<team>` of THIS assignment, one the student can push to (their team was granted push).
  const all = slugs.includes(a.slug) ? slugs : [...slugs, a.slug];
  const team = repos.find((r) => {
    const n = lc(r.name);
    return n !== lc(a.slug) && ownerSlug(r.name, all) === a.slug && !n.endsWith('-submissions') && r.permissions?.push === true;
  });
  return team ? { ...none, repo: team.name, team: team.name.slice(a.slug.length + 1) } : none;
}

/**
 * The student's team for group assignment `a` from their GitHub teams in `org`: the team
 * `<slug>-<team>` (teams.team_slug) of THIS assignment, matched to the longest known slug.
 * This is how a drop-box or external group finds its team: those shapes make no team repo.
 */
export function teamOf(a: SemesterAssignment, teams: GhTeam[], org: string, slugs: string[] = [a.slug]): { team: string; slug: string } | null {
  const all = slugs.includes(a.slug) ? slugs : [...slugs, a.slug];
  const t = teams.find((x) => x.organization.login.toLowerCase() === org.toLowerCase() && x.slug.toLowerCase() !== a.slug.toLowerCase() && ownerSlug(x.slug, all) === a.slug);
  if (!t) return null;
  const pre = `${a.slug}-`.toLowerCase();
  return { team: t.name.toLowerCase().startsWith(pre) ? t.name.slice(pre.length) : t.slug.slice(pre.length), slug: t.slug };
}

const teamLists = new WeakMap<GitHubClient, Promise<GhTeam[]>>();

/** The person's GitHub teams across every org (`/user/teams`), read once per session, not once per semester. A failed read is not kept. */
export function myTeams(client: GitHubClient): Promise<GhTeam[]> {
  let p = teamLists.get(client);
  if (!p) {
    p = client.listMyTeams();
    teamLists.set(client, p);
    p.catch(() => teamLists.delete(client));
  }
  return p;
}

/** Drop the session's team list (sign-out). */
export const forgetMyTeams = (client: GitHubClient) => teamLists.delete(client);

/** Everything the student's own screens need for one semester (a few calls: the repo list, the gradebook, the role, their teams, each team's members). */
export async function readMine(client: GitHubClient, org: string, login: string, assignments: SemesterAssignment[]): Promise<Mine> {
  const book = `grades-${login}`;
  const groups = assignments.some((a) => a.group);
  const [repos, grades, updated, audit, teams] = await Promise.all([
    client.listOrgRepos(org),
    client.getContents(org, book, 'grades.yml').catch(() => null),
    client.lastCommitDate(org, book, 'grades.yml').catch(() => null),
    client.getTeamMembership(org, AUDITORS_TEAM, login).catch(() => null),
    groups ? myTeams(client).catch(() => [] as GhTeam[]) : ([] as GhTeam[]),
  ]);
  const slugs = assignments.map((x) => x.slug);
  const units: Record<string, MyUnit> = {};
  await Promise.all(assignments.map(async (a) => {
    const u = unitOf(a, repos, login, slugs);
    const found = a.group && !u.team ? teamOf(a, teams, org, slugs) : null;
    const team = u.team ?? found?.team ?? null;
    const members = team ? await client.listTeamMembers(org, found?.slug ?? `${a.slug}-${team}`.toLowerCase()) : null;
    units[a.slug] = { ...u, team, members };
  }));
  return { units, gradebook: grades ? parseGradebook(grades.text, updated) : null, auditor: audit === 'active' };
}

/** What a comment on the receipts thread is: a receipt, a note that the instructors updated files, the marks-returned note, or anyone's comment. */
export type ThreadKind = 'receipt' | 'patch' | 'marks' | 'comment';

export interface ThreadEntry {
  kind: ThreadKind;
  text: string;
  when: string;
  url: string;
}

export interface Receipts {
  url: string;
  /** The newest receipt (the engine's comment), its hidden marks removed; null before the deadline. */
  last: { text: string; when: string } | null;
  /** The issue's own text: the due date, the late rule and, for a team, the CONTRIBUTIONS.md ask. */
  body: string;
  /** Every comment, oldest first. */
  thread: ThreadEntry[];
}

/** A comment's kind from the engine's hidden mark on it (course.receipt_marker, assign.PATCH_MARKER, course.marks_returned_marker). */
export function threadKind(body: string): ThreadKind {
  if (/<!-- dsl-receipt:/.test(body)) return 'receipt';
  if (/<!-- dsl-patch:/.test(body)) return 'patch';
  if (/<!-- dsl-marks-returned:/.test(body)) return 'marks';
  return 'comment';
}

/** The patch notes of a thread ("pull before you continue"). */
export const patchNotes = (r: Receipts | null | undefined) => (r?.thread ?? []).filter((e) => e.kind === 'patch');

/** A comment's text as a person reads it: the engine's hidden `<!-- ... -->` marks taken out. */
export const readable = (body: string) => body.replace(/<!--[\s\S]*?-->/g, '').trim();

/** The Submission receipts issue of `repo` and its newest receipt; null when the repo has none. */
export async function readReceipts(client: GitHubClient, org: string, repo: string): Promise<Receipts | null> {
  const pick = (list: GhIssue[]) => {
    const issues = list.filter((i) => !i.pull_request);
    return issues.find((i) => i.title === RECEIPTS_TITLE) ?? issues[0];
  };
  let issue: GhIssue | undefined;
  for (const label of RECEIPTS_LABELS) {
    issue = pick(await client.listIssues(org, repo, `labels=${label}&state=all`));
    if (issue) break;
  }
  issue ??= (await client.listIssues(org, repo, 'state=all')).find((i) => !i.pull_request && i.title === RECEIPTS_TITLE);
  if (!issue) return null;
  const comments = issue.comments ? await client.listIssueComments(org, repo, issue.number) : [];
  const thread = comments.map((c) => ({ kind: threadKind(c.body), text: readable(c.body), when: c.created_at, url: c.html_url }));
  const receipts = thread.filter((c) => c.kind === 'receipt');
  const last = receipts[receipts.length - 1];
  return { url: issue.html_url, last: last ? { text: last.text, when: last.when } : null, body: readable(issue.body ?? ''), thread };
}

export const repoUrl = (org: string, repo: string) => `https://github.com/${org}/${repo}`;
export const gradebookUrl = (org: string, login: string) => `https://github.com/${org}/grades-${login}`;

/** The Submission receipts threads of the student's private repos in the semester, by repo (a thread that cannot be read is null). */
export async function readAllReceipts(client: GitHubClient, org: string, assignments: SemesterAssignment[], mine: Mine): Promise<Record<string, Receipts | null>> {
  const repos = assignments.filter((a) => a.privateRepo).map((a) => mine.units[a.slug]?.repo).filter((r): r is string => !!r);
  return Object.fromEntries(await Promise.all(repos.map(async (r) => [r, await readReceipts(client, org, r).catch(() => null)] as const)));
}

/** Every patch note across the student's threads, for This week. */
export function patchLines(assignments: SemesterAssignment[], mine: Mine | null, receipts: Record<string, Receipts | null> | null | undefined): PatchLine[] {
  if (!mine || !receipts) return [];
  return assignments.flatMap((a) => {
    const repo = mine.units[a.slug]?.repo;
    return repo ? patchNotes(receipts[repo]).map((p) => ({ slug: a.slug, when: p.when })) : [];
  });
}
