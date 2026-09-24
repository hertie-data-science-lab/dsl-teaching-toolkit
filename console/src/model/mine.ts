// A student's own facts in one semester, read from GitHub with their own token: which
// assignment repos they hold (their own `<slug>-<handle>`, their team's `<slug>-<team>`, the
// shared drop box), their team's members, the Submission receipts thread in each repo, and
// their gradebook `grades-<handle>/grades.yml`. Nothing here reads another student's data:
// GitHub shows a student only the repos they were granted, and a team repo counts as theirs
// only where they can push to it (a demo org's public repos are readable by anyone).

import { parse } from 'yaml';
import type { GhRepo, GitHubClient } from '../github/client';
import type { SemesterAssignment } from './student';

/** The receipts issue's label: the engine matches it (course.RECEIPTS_ISSUE_LABEL); the word is frozen. */
export const RECEIPTS_LABEL = 'dsl-feedback';
export const RECEIPTS_TITLE = 'Submission receipts';

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

/** The repo among `repos` that is the student's for assignment `a`, and its team. */
export function unitOf(a: SemesterAssignment, repos: GhRepo[], login: string): Omit<MyUnit, 'members'> {
  const lc = (s: string) => s.toLowerCase();
  const none = { slug: a.slug, repo: null, team: null, shared: false };
  if (a.submitVia === 'external') return none;
  if (a.submitVia === 'shared_dropbox_repo') {
    const box = repos.find((r) => lc(r.name) === lc(`${a.slug}-submissions`));
    return { ...none, repo: box?.name ?? null, shared: true };
  }
  const own = repos.find((r) => lc(r.name) === lc(`${a.slug}-${login}`));
  if (own && !a.group) return { ...none, repo: own.name };
  if (!a.group) return none;
  // A team's repo: `<slug>-<team>`, one the student can push to (their team was granted push).
  const team = repos.find((r) => lc(r.name).startsWith(lc(`${a.slug}-`)) && !lc(r.name).endsWith('-submissions') && r.permissions?.push === true);
  return team ? { ...none, repo: team.name, team: team.name.slice(a.slug.length + 1) } : none;
}

/** Everything the student's own screens need for one semester (a few calls: the repo list, the gradebook, each team). */
export async function readMine(client: GitHubClient, org: string, login: string, assignments: SemesterAssignment[]): Promise<Mine> {
  const book = `grades-${login}`;
  const [repos, grades, updated] = await Promise.all([
    client.listOrgRepos(org),
    client.getContents(org, book, 'grades.yml').catch(() => null),
    client.lastCommitDate(org, book, 'grades.yml').catch(() => null),
  ]);
  const units: Record<string, MyUnit> = {};
  await Promise.all(assignments.map(async (a) => {
    const u = unitOf(a, repos, login);
    const members = u.team ? await client.listTeamMembers(org, `${a.slug}-${u.team}`.toLowerCase()) : null;
    units[a.slug] = { ...u, members };
  }));
  return { units, gradebook: grades ? parseGradebook(grades.text, updated) : null };
}

export interface Receipts {
  url: string;
  /** The newest receipt (the engine's comment), its hidden marks removed; null before the deadline. */
  last: { text: string; when: string } | null;
}

/** A comment's text as a person reads it: the engine's hidden `<!-- ... -->` marks taken out. */
export const readable = (body: string) => body.replace(/<!--[\s\S]*?-->/g, '').trim();

/** The Submission receipts issue of `repo` and its newest receipt; null when the repo has none. */
export async function readReceipts(client: GitHubClient, org: string, repo: string): Promise<Receipts | null> {
  const issues = await client.listIssues(org, repo, `labels=${RECEIPTS_LABEL}&state=all`);
  const issue = issues[0] ?? (await client.listIssues(org, repo, 'state=all')).find((i) => i.title === RECEIPTS_TITLE);
  if (!issue) return null;
  const comments = issue.comments ? await client.listIssueComments(org, repo, issue.number) : [];
  const receipts = comments.filter((c) => /<!-- dsl-receipt:/.test(c.body));
  const last = receipts[receipts.length - 1];
  return { url: issue.html_url, last: last ? { text: readable(last.body), when: last.created_at } : null };
}

export const repoUrl = (org: string, repo: string) => `https://github.com/${org}/${repo}`;
export const gradebookUrl = (org: string, login: string) => `https://github.com/${org}/grades-${login}`;
