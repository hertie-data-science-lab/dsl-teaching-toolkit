// An org that still carries a name decision 0012 or 0010 retired has not been migrated. The
// console reads the new names only (no dual reading): it looks for the old ones solely to
// say so, on one screen, with the engine's NOT_MIGRATED sentence. This module is the only
// place in src/ that spells a retired name.

import type { GitHubClient } from '../github/client';
import { CONFIG_REPO, COURSE_REPO, INSTRUCTORS_FILE, JOIN_REPO, NAMES, REGISTRY_FILE } from './names';

export const NOT_MIGRATED = 'NOT_MIGRATED';

/** A retired spelling found in an org, and the name that replaced it. */
export interface Leftover {
  old: string;
  new: string;
}

/** The retired spellings the console can see from outside. */
export const RETIRED = {
  config_repo: 'classroom-config',
  join_repo: 'welcome',
  system_dir: '.dsl',
  people_file: 'people.yml',
  registry_file: 'cohort-courses-pages.yml',
  semester_topic: 'dsl-cohort',
} as const;

export const SEMESTER_TOPIC = 'dsl-semester';

/** The engine's sentence (`faults.not_migrated_text`), word for word. */
export const notMigratedText = (l: Leftover) => `${NOT_MIGRATED}: \`${l.old}\` is the old name of \`${l.new}\` - run the migration`;

const has = (paths: string[], p: string) => paths.some((x) => x === p || x.startsWith(`${p}/`));

/**
 * What a semester org still carries under a retired name: the topic on its `.github`, its
 * config and join repos, and in the config repo, people.yml and `.dsl/`. Empty when migrated.
 */
export async function semesterLeftovers(client: GitHubClient, org: string, topics?: string[]): Promise<Leftover[]> {
  const [cfg, oldCfg, join, oldJoin, tagged] = await Promise.all([
    client.getRepo(org, CONFIG_REPO),
    client.getRepo(org, RETIRED.config_repo),
    client.getRepo(org, JOIN_REPO),
    client.getRepo(org, RETIRED.join_repo),
    topics ?? client.getRepo(org, COURSE_REPO).then(async (r) => (r ? r.topics ?? (await client.getRepoTopics(org, COURSE_REPO)) : [])),
  ]);
  const out: Leftover[] = [];
  if (tagged.includes(RETIRED.semester_topic) && !tagged.includes(SEMESTER_TOPIC)) out.push({ old: RETIRED.semester_topic, new: SEMESTER_TOPIC });
  if (oldCfg && !cfg) out.push({ old: RETIRED.config_repo, new: CONFIG_REPO });
  if (oldJoin && !join) out.push({ old: RETIRED.join_repo, new: JOIN_REPO });
  const repo = cfg ? CONFIG_REPO : oldCfg ? RETIRED.config_repo : null;
  const tree = repo ? await client.listTree(org, repo, 'HEAD') : null;
  const paths = tree?.tree.map((e) => e.path) ?? [];
  if (has(paths, RETIRED.people_file)) out.push({ old: RETIRED.people_file, new: INSTRUCTORS_FILE });
  if (has(paths, RETIRED.system_dir)) out.push({ old: `${RETIRED.system_dir}/`, new: `${NAMES.system_dir}/` });
  return out;
}

/** What a course org's `.github` still carries under a retired name: the registry and `.dsl/`. */
export async function courseLeftovers(client: GitHubClient, org: string): Promise<Leftover[]> {
  const tree = await client.listTree(org, COURSE_REPO, 'HEAD');
  const paths = tree?.tree.map((e) => e.path) ?? [];
  const out: Leftover[] = [];
  if (has(paths, RETIRED.registry_file) && !has(paths, REGISTRY_FILE)) out.push({ old: RETIRED.registry_file, new: REGISTRY_FILE });
  if (has(paths, RETIRED.system_dir)) out.push({ old: `${RETIRED.system_dir}/`, new: `${NAMES.system_dir}/` });
  return out;
}
