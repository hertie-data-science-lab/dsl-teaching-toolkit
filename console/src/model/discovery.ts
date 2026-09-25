// Which course and semester orgs the signed-in person can see, and their role in each
// (decision 0011 rule 2). The orgs come from whatever listings the token kind answers,
// merged; each is classified by the topics on its `.github` repo: `dsl-course-hub` is a
// course (its semesters come from its registry, `.github/semesters.yml`), `dsl-semester` is
// a semester; an org still on the retired topic is not listed (model/migration). Push on an
// org's `.github` makes the person an instructor there; membership without it makes them a
// student of a semester org.

import { parse } from 'yaml';
import type { GhRepo, GitHubClient } from '../github/client';
import { str } from './format';
import { SEMESTER_TOPIC } from './migration';
import { CONFIG_REPO, COURSE_REPO, POINTER_PATH, REGISTRY_FILE } from './names';

export const COURSE_HUB_TOPIC = 'dsl-course-hub';
export const REGISTRY_PATH = REGISTRY_FILE;
export const COURSE_META_PATH = 'dsl-course.yml';

export interface CohortRef {
  org: string;
  term: string; // the semester key, e.g. f2026
  termLabel: string; // "Fall 2026"
}

export interface Course {
  org: string;
  name: string;
  code: string;
  description: string;
  write: boolean;
  admins: string[];
  cohorts: CohortRef[];
  meta: Record<string, unknown> | null; // dsl-course.yml as parsed, for Course details
}

/** "Course name, Fall 2026". */
export const cohortName = (p: { course: Pick<Course, 'name'>; cohort: Pick<CohortRef, 'termLabel'> }) => `${p.course.name}, ${p.cohort.termLabel}`;

const SEASON: Record<string, string> = { f: 'Fall', s: 'Spring', w: 'Winter', u: 'Summer' };

/** "hertie-dsl-demo-f2026" -> { term: "f2026", label: "Fall 2026" }. */
export function termOf(org: string): { term: string; label: string } {
  const m = /-([fswu])(\d{4})$/.exec(org);
  if (!m) return { term: org.split('-').pop() ?? org, label: org };
  return { term: `${m[1]}${m[2]}`, label: `${SEASON[m[1]]} ${m[2]}` };
}

/** The registry's semester list: `{semesters: [...]}` or a bare list; anything else is empty. */
export function parseRegistry(text: string | null | undefined): string[] {
  if (!text) return [];
  let data: unknown;
  try {
    data = parse(text);
  } catch {
    return [];
  }
  const list = data && typeof data === 'object' && !Array.isArray(data) ? (data as { semesters?: unknown }).semesters : data;
  return Array.isArray(list) ? list.filter((c): c is string => typeof c === 'string' && c.length > 0) : [];
}

/** The course `org` is, reading its `.github` repo unless the caller already has it; null when it is not a course. */
export async function discoverCourse(client: GitHubClient, org: string, known?: GhRepo): Promise<Course | null> {
  const repo = known ?? (await client.getRepo(org, COURSE_REPO));
  if (!repo) return null;
  const topics = await topicsOf(client, org, repo);
  if (!topics.includes(COURSE_HUB_TOPIC)) return null;
  const [registry, meta] = await Promise.all([client.getContents(org, COURSE_REPO, REGISTRY_PATH), readMeta(client, org)]);
  const people = (meta?.people ?? {}) as { course_admins?: { github_handle?: string }[] };
  const cohorts = parseRegistry(registry?.text).map((c) => {
    const t = termOf(c);
    return { org: c, term: t.term, termLabel: t.label };
  });
  cohorts.sort((a, b) => b.term.slice(1).localeCompare(a.term.slice(1)) || a.term.localeCompare(b.term));
  return {
    org,
    name: str(meta?.course_name) || org,
    code: str(meta?.course_code),
    description: str(meta?.course_description),
    write: repo.permissions?.push === true,
    admins: (people.course_admins ?? []).map((a) => str(a.github_handle)).filter(Boolean),
    cohorts,
    meta,
  };
}

const topicsOf = async (client: GitHubClient, org: string, repo: GhRepo) => repo.topics ?? (await client.getRepoTopics(org, COURSE_REPO));

/** A course org's `.github/dsl-course.yml` as parsed; null when absent or unreadable. */
const readMeta = (client: GitHubClient, org: string) => readYamlMap(client, org, COURSE_REPO, COURSE_META_PATH);

/**
 * A semester's pointer to its course (`.system/dsl-course.yml` in its config repo); null when
 * absent or unreadable. The config repo is private, so a student reads null here.
 */
const readPointer = (client: GitHubClient, org: string) => readYamlMap(client, org, CONFIG_REPO, POINTER_PATH);

async function readYamlMap(client: GitHubClient, org: string, repo: string, path: string): Promise<Record<string, unknown> | null> {
  const f = await client.getContents(org, repo, path);
  try {
    const m = f ? parse(f.text) : null;
    return m && typeof m === 'object' ? (m as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

// --------------------------------------------------------------------------- roles

/** How the person signed in: the listings GitHub answers differ by kind. */
export type TokenKind = 'classic' | 'fine-grained' | 'app';
export type Role = 'instructor' | 'student';
/** Which shell renders: the instructor screens, or the student screens of a semester. */
export type Mode = 'instructor' | 'student';

/** A semester org the person is a member of. */
export interface Semester extends CohortRef {
  /** The course org its pointer (`.system/dsl-course.yml` in the config repo) names; '' when it names none or cannot be read. */
  courseOrg: string;
  courseName: string;
  /** Archiving a semester archives its `.github` last but one, so an archived `.github` means an archived semester. */
  archived: boolean;
  role: Role;
}

export interface Estate {
  /** Course orgs, writable ones first (today's instructor list, read-only ones included). */
  courses: Course[];
  /** Semester orgs the person is a member of, newest first. */
  semesters: Semester[];
  /**
   * The person's role per org, keyed by the lower-cased login (read it with roleOf). Instructor
   * wins over student. An org absent here carries no role: a course the person can read but not
   * change is still in `courses`, shown read only.
   */
  roles: Map<string, Role>;
  kind: TokenKind;
  /** Semester orgs that invited the person, who has not accepted yet (classic and App sign-in only: a fine-grained token cannot list memberships). */
  invited?: Semester[];
}

/** Where a person accepts an org's invitation on GitHub. */
export const invitationUrl = (org: string) => `https://github.com/orgs/${org}/invitation`;

/** The orgs whose invitation the person has not accepted yet; [] when the token cannot list memberships. */
export async function pendingOrgs(client: GitHubClient, kind: TokenKind): Promise<string[]> {
  if (kind === 'fine-grained') return [];
  const list = await client.listPendingMemberships().catch(() => []);
  return list.filter((m) => m.state === 'pending').map((m) => m.organization.login);
}

/** "Machine Learning, Fall 2026". */
export const semesterName = (s: Pick<Semester, 'courseName' | 'termLabel' | 'org'>) => (s.courseName ? `${s.courseName}, ${s.termLabel}` : s.termLabel);

/** Most orgs whose membership is checked one by one, here and by PatAuth: a runaway guard. */
export const PROBE_LIMIT = 50;

/** The person's role in `org`, whatever its case. */
export const roleOf = (e: Pick<Estate, 'roles'>, org: string): Role | undefined => e.roles.get(org.toLowerCase());

/**
 * The orgs the person is an active member of, from every listing their token kind answers.
 * Classic: `/user/orgs` and `/user/memberships/orgs`. GitHub App: those plus the accounts of
 * `/user/installations`. Fine-grained: GitHub answers neither org listing (`/user/orgs` is an
 * empty 200, `/user/memberships/orgs` is not open to it), so the candidates are the owners of
 * `/user/repos` and the public memberships, each checked with `/user/memberships/orgs/{org}`,
 * which such a token may read for an org it reaches. A listing that fails counts as empty,
 * unless every one failed: that is GitHub not answering, not an account with no orgs.
 */
export async function memberOrgs(client: GitHubClient, kind: TokenKind, login: string): Promise<string[]> {
  let asked = 0;
  const failures: unknown[] = [];
  const soft = <T>(use: boolean, p: () => Promise<T[]>): Promise<T[]> | T[] => {
    if (!use) return [];
    asked++;
    return p().catch((e: unknown) => {
      failures.push(e);
      return [];
    });
  };
  const fine = kind === 'fine-grained';
  const [orgs, memberships, installs, owners, open] = await Promise.all([
    soft(!fine, () => client.listUserOrgs()),
    soft(!fine, () => client.listOrgMemberships()),
    soft(kind === 'app', () => client.listInstallationAccounts()),
    soft(fine, () => client.listRepoOwners()),
    soft(fine, () => client.listPublicOrgs(login)),
  ]);
  if (failures.length === asked) throw failures[0];
  const byKey = new Map<string, string>(); // lower-cased login -> as GitHub spells it
  const add = (l: string) => byKey.has(l.toLowerCase()) || byKey.set(l.toLowerCase(), l);
  const members = new Set<string>();
  for (const l of [...orgs.map((o) => o.login), ...memberships.filter((m) => m.state === 'active').map((m) => m.organization.login)]) {
    add(l);
    members.add(l.toLowerCase());
  }
  for (const a of [...installs, ...owners]) if (a.type === 'Organization') add(a.login);
  for (const o of open) add(o.login);
  const unknown = [...byKey.keys()].filter((k) => !members.has(k)).slice(0, PROBE_LIMIT);
  const checked = await Promise.all(unknown.map((k) => client.getMyMembership(byKey.get(k)!).catch(() => null)));
  unknown.forEach((k, i) => checked[i]?.state === 'active' && members.add(k));
  return [...byKey.entries()].filter(([k]) => members.has(k)).map(([, l]) => l);
}

type Found = { course: Course } | { semester: Omit<Semester, 'courseName'> } | null;

async function classify(client: GitHubClient, org: string): Promise<Found> {
  const repo = await client.getRepo(org, COURSE_REPO);
  if (!repo) return null;
  const topics = await topicsOf(client, org, repo);
  if (topics.includes(COURSE_HUB_TOPIC)) {
    const course = await discoverCourse(client, org, repo);
    return course ? { course } : null;
  }
  if (!topics.includes(SEMESTER_TOPIC)) return null;
  const meta = await readPointer(client, org);
  const t = termOf(org);
  return {
    semester: {
      org,
      term: t.term,
      termLabel: t.label,
      courseOrg: str(meta?.course),
      archived: repo.archived === true,
      role: repo.permissions?.push === true ? 'instructor' : 'student',
    },
  };
}

/** Every course and semester the person can see, and their role in each. */
export async function discoverEstate(client: GitHubClient, who: { kind: TokenKind; login: string }): Promise<Estate> {
  const [orgs, pending] = await Promise.all([memberOrgs(client, who.kind, who.login), pendingOrgs(client, who.kind)]);
  const [found, asked] = await Promise.all([
    Promise.all(orgs.map((o) => classify(client, o).catch(() => null))),
    // A semester's `.github` is public, so an invited person can already read what it is.
    Promise.all(pending.slice(0, PROBE_LIMIT).map((o) => classify(client, o).catch(() => null))),
  ]);
  const courses = found
    .flatMap((f) => (f && 'course' in f ? [f.course] : []))
    .sort((a, b) => Number(b.write) - Number(a.write) || a.name.localeCompare(b.name));
  const byOrg = new Map(courses.map((c) => [c.org.toLowerCase(), c]));
  const bare = found.flatMap((f) => (f && 'semester' in f ? [f.semester] : []));
  const invitedBare = asked.flatMap((f) => (f && 'semester' in f && !f.semester.archived ? [f.semester] : []));
  // A semester whose course is not among the person's orgs: its name is on the course's public `.github`.
  const names = new Map<string, string>();
  await Promise.all(
    [...new Set([...bare, ...invitedBare].map((s) => s.courseOrg).filter((c) => c && !byOrg.has(c.toLowerCase())))].map(async (c) => {
      const meta = await readMeta(client, c).catch(() => null);
      names.set(c, str(meta?.course_name));
    }),
  );
  const roles = new Map<string, Role>();
  // A writable course makes the person an instructor of every semester it registers.
  for (const c of courses) if (c.write) for (const o of [c.org, ...c.cohorts.map((k) => k.org)]) roles.set(o.toLowerCase(), 'instructor');
  const semesters: Semester[] = bare.map((s) => {
    const role: Role = roles.get(s.org.toLowerCase()) ?? s.role;
    roles.set(s.org.toLowerCase(), role);
    return { ...s, role, courseName: byOrg.get(s.courseOrg.toLowerCase())?.name ?? names.get(s.courseOrg) ?? '' };
  });
  semesters.sort((a, b) => Number(a.archived) - Number(b.archived) || b.term.slice(1).localeCompare(a.term.slice(1)) || a.org.localeCompare(b.org));
  const invited: Semester[] = invitedBare.map((s) => ({ ...s, role: 'student', courseName: byOrg.get(s.courseOrg.toLowerCase())?.name ?? names.get(s.courseOrg) ?? '' }));
  return { courses, semesters, roles, kind: who.kind, invited };
}

/** The semesters the person is a student of. */
export const studentSemesters = (e: Estate) => e.semesters.filter((s) => s.role === 'student');

/** Whether the person is an instructor anywhere: the console then opens in instructor mode. */
export const isInstructor = (e: Estate) => [...e.roles.values()].includes('instructor');
