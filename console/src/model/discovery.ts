// Which courses the signed-in instructor can see: orgs from GET /user/orgs whose `.github`
// repo carries the `dsl-course-hub` topic. Each course's cohorts come from its registry,
// `.github/cohort-courses-pages.yml`. Write access = push on that `.github` repo.

import { parse } from 'yaml';
import type { GitHubClient } from '../github/client';
import { str } from './format';

export const COURSE_HUB_TOPIC = 'dsl-course-hub';
export const REGISTRY_PATH = 'cohort-courses-pages.yml';
export const COURSE_META_PATH = 'dsl-course.yml';

export interface CohortRef {
  org: string;
  term: string; // the tag, e.g. f2026
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

const SEASON: Record<string, string> = { f: 'Fall', s: 'Spring', w: 'Winter', u: 'Summer' };

/** "hertie-dsl-demo-f2026" -> { term: "f2026", label: "Fall 2026" }. */
export function termOf(org: string): { term: string; label: string } {
  const m = /-([fswu])(\d{4})$/.exec(org);
  if (!m) return { term: org.split('-').pop() ?? org, label: org };
  return { term: `${m[1]}${m[2]}`, label: `${SEASON[m[1]]} ${m[2]}` };
}

/** The registry's cohort list: `{cohorts: [...]}` or a bare list; anything else is empty. */
export function parseRegistry(text: string | null | undefined): string[] {
  if (!text) return [];
  let data: unknown;
  try {
    data = parse(text);
  } catch {
    return [];
  }
  const list = data && typeof data === 'object' && !Array.isArray(data) ? (data as { cohorts?: unknown }).cohorts : data;
  return Array.isArray(list) ? list.filter((c): c is string => typeof c === 'string' && c.length > 0) : [];
}

export async function discoverCourse(client: GitHubClient, org: string): Promise<Course | null> {
  const repo = await client.getRepo(org, '.github');
  if (!repo) return null;
  const topics = repo.topics ?? (await client.getRepoTopics(org, '.github'));
  if (!topics.includes(COURSE_HUB_TOPIC)) return null;
  const [registry, metaFile] = await Promise.all([
    client.getContents(org, '.github', REGISTRY_PATH),
    client.getContents(org, '.github', COURSE_META_PATH),
  ]);
  let meta: Record<string, unknown> | null = null;
  try {
    const m = metaFile ? parse(metaFile.text) : null;
    meta = m && typeof m === 'object' ? (m as Record<string, unknown>) : null;
  } catch {
    meta = null;
  }
  const people = (meta?.people ?? {}) as { course_admins?: { github_handle?: string }[] };
  const cohorts = parseRegistry(registry?.text).map((c) => {
    const t = termOf(c);
    return { org: c, term: t.term, termLabel: t.label };
  });
  cohorts.sort((a, b) => b.term.slice(1).localeCompare(a.term.slice(1)) || a.term.localeCompare(b.term));
  return {
    org,
    name: str(meta?.course_name) || str(meta?.org_name) || org,
    code: str(meta?.course_code),
    description: str(meta?.course_description),
    write: repo.permissions?.push === true,
    admins: (people.course_admins ?? []).map((a) => str(a.github_handle)).filter(Boolean),
    cohorts,
    meta,
  };
}

/** Every course the user can see, writable ones first, then by name. */
export async function discoverCourses(client: GitHubClient): Promise<Course[]> {
  const orgs = await client.listUserOrgs();
  const found = await Promise.all(orgs.map((o) => discoverCourse(client, o.login).catch(() => null)));
  return found
    .filter((c): c is Course => c !== null)
    .sort((a, b) => Number(b.write) - Number(a.write) || a.name.localeCompare(b.name));
}
