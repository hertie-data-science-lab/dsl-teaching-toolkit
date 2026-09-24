// The live checks each wizard step verifies before it lets you continue: does the org exist,
// can the lab's bot manage it, did the set-up run leave what it should, is the template
// there with both branches and a settings file that parses. Each check says ok, not yet
// (with what to do), or "could not tell" when GitHub would not answer.

import { useEffect, useState } from 'preact/hooks';
import { YamlText } from '../edit/yamlText';
import { GitHubError, type GitHubClient } from '../github/client';
import { COURSE_HUB_TOPIC, REGISTRY_PATH, parseRegistry } from '../model/discovery';
import { BOT } from './model';

export interface Check {
  text: string;
  ok: boolean | null; // null: could not tell
  hint?: string;
}

export const allOk = (list: Check[] | null | undefined): boolean => !!list?.length && list.every((c) => c.ok === true);

function why(e: unknown): string {
  return e instanceof GitHubError ? e.message : e instanceof Error ? e.message : String(e);
}

/** The org exists, and the bot is an active owner (the console app's stand-in until decision 0002). */
export async function checkOrg(client: GitHubClient, org: string): Promise<Check[]> {
  let exists: boolean | null;
  try {
    exists = (await client.getOrg(org)) !== null;
  } catch {
    exists = null;
  }
  const first: Check = {
    text: `The org ${org} exists`, ok: exists,
    hint: exists === false ? 'Create it on GitHub with exactly this name, then check again.' : exists === null ? 'GitHub did not answer; check again.' : undefined,
  };
  if (!exists) return [first, { text: `${BOT} can manage it`, ok: false, hint: 'Checked once the org exists.' }];
  let m: { state: string; role: string } | null | undefined;
  try {
    m = await client.getOrgMembership(org, BOT);
  } catch (e) {
    m = e instanceof GitHubError && e.status === 404 ? null : undefined;
  }
  const people = `https://github.com/orgs/${org}/people`;
  const second: Check =
    m === undefined ? { text: `${BOT} can manage it`, ok: null, hint: `Only an owner of ${org} can see its members' roles. Sign in as an owner, or check ${people}.` }
    : m === null ? { text: `${BOT} can manage it`, ok: false, hint: `Invite ${BOT} as an Owner on the org's People page.` }
    : m.state !== 'active' ? { text: `${BOT} can manage it`, ok: false, hint: `${BOT} is invited but has not accepted yet. Ask the DSL team to accept it.` }
    : m.role !== 'admin' ? { text: `${BOT} can manage it`, ok: false, hint: `${BOT} is a member, not an owner. Make it an Owner on the People page.` }
    : { text: `${BOT} can manage it`, ok: true };
  return [first, second];
}

/** Bootstrap Course Org left a course hub with its Console workflow. */
export async function checkCourseSetUp(client: GitHubClient, org: string): Promise<Check[]> {
  try {
    const repo = await client.getRepo(org, '.github');
    const topics = repo ? repo.topics ?? (await client.getRepoTopics(org, '.github')) : [];
    const hub = !!repo && topics.includes(COURSE_HUB_TOPIC);
    const [meta, consoleWf] = hub
      ? await Promise.all([client.getContents(org, '.github', 'dsl-course.yml'), client.getContents(org, '.github', '.github/workflows/console.yml')])
      : [null, null];
    return [
      { text: 'Course settings are in place (dsl-course.yml)', ok: hub && !!meta, hint: hub ? undefined : 'Set up the course; it takes a few minutes.' },
      { text: 'The Console workflow is in place', ok: !!consoleWf, hint: consoleWf ? undefined : 'Added by the set-up run.' },
    ];
  } catch (e) {
    return [{ text: 'Course settings are in place', ok: null, hint: `GitHub did not answer (${why(e)}); check again.` }];
  }
}

/** Bootstrap cohort left classroom-config and registered the cohort with its course. */
export async function checkCohortSetUp(client: GitHubClient, courseOrg: string, cohortOrg: string): Promise<Check[]> {
  try {
    const [cfg, welcome, reg] = await Promise.all([
      client.getRepo(cohortOrg, 'classroom-config'),
      client.getRepo(cohortOrg, 'welcome'),
      client.getContents(courseOrg, '.github', REGISTRY_PATH),
    ]);
    return [
      { text: 'The cohort’s settings repo is in place (classroom-config)', ok: !!cfg },
      { text: 'The join form is in place (welcome)', ok: !!welcome },
      { text: 'The course lists the cohort', ok: parseRegistry(reg?.text).includes(cohortOrg) },
    ];
  } catch (e) {
    return [{ text: 'The cohort is set up', ok: null, hint: `GitHub did not answer (${why(e)}); check again.` }];
  }
}

/** `repo` is not taken in `org`. Absence is only claimed on a 404; any other failure is "could not tell". */
export async function checkFree(client: GitHubClient, org: string, repo: string, what: string): Promise<Check> {
  try {
    const r = await client.getRepo(org, repo);
    return r ? { text: `${what} is free in the course`, ok: false, hint: `${org}/${repo} already exists. Choose another number or term.` } : { text: `${what} is free in the course`, ok: true };
  } catch (e) {
    return { text: `${what} is free in the course`, ok: null, hint: `GitHub did not answer (${why(e)}).` };
  }
}

export async function checkRepoExists(client: GitHubClient, org: string, repo: string, text: string): Promise<Check> {
  try {
    return { text, ok: (await client.getRepo(org, repo)) !== null };
  } catch (e) {
    return { text, ok: null, hint: `GitHub did not answer (${why(e)}).` };
  }
}

export interface TemplateCheck {
  checks: Check[];
  config: { text: string; sha: string } | null;
}

/** The template exists with its main and solution branches, and grading_config.yml parses. */
export async function checkTemplate(client: GitHubClient, org: string, repo: string): Promise<TemplateCheck> {
  try {
    const r = await client.getRepo(org, repo);
    if (!r) return { checks: [{ text: `Assignment template ${repo} created`, ok: false, hint: 'Not there yet; creating takes about a minute.' }], config: null };
    const [main, sol, cfg] = await Promise.all([client.getBranch(org, repo, 'main'), client.getBranch(org, repo, 'solution'), client.getContents(org, repo, 'grading_config.yml', 'solution')]);
    const parses = cfg ? !new YamlText(cfg.text).errors.length : false;
    return {
      checks: [
        { text: `Assignment template ${repo} created`, ok: true },
        { text: 'Brief and solution branches present', ok: !!main && !!sol },
        { text: 'Settings check out (grading_config.yml parses)', ok: parses, hint: cfg && !parses ? 'grading_config.yml does not parse; fix it on the template’s settings.' : undefined },
      ],
      config: cfg ? { text: cfg.text, sha: cfg.sha } : null,
    };
  } catch (e) {
    return { checks: [{ text: `Assignment template ${repo} created`, ok: null, hint: `GitHub did not answer (${why(e)}).` }], config: null };
  }
}

export interface Live<T> {
  value: T | null;
  busy: boolean;
  run: () => void;
}

/** Run `fn` now and whenever `deps` change or `run` is pressed; nothing runs without a client. */
export function useLive<T>(fn: (() => Promise<T>) | null, deps: unknown[]): Live<T> {
  const [value, setValue] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!fn) return;
    let live = true;
    setBusy(true);
    fn()
      .then((v) => live && setValue(v))
      .catch(() => {})
      .finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
  }, [...deps, tick, !!fn]);
  return { value, busy, run: () => setTick(tick + 1) };
}
