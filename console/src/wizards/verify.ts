// The live checks each wizard step verifies before it lets you continue: does the org exist,
// is the console app installed on it, can the lab's bot manage it, did the set-up run leave
// what it should, is the template there with both branches and a settings file that parses.
// Each check says ok, not yet
// (with what to do), or "could not tell" when GitHub would not answer.

import { useEffect, useRef, useState } from 'preact/hooks';
import { YamlText } from '../edit/yamlText';
import { GitHubError, type GitHubClient } from '../github/client';
import { COURSE_HUB_TOPIC, REGISTRY_PATH, parseRegistry, type TokenKind } from '../model/discovery';
import { APP_SLUG, BOT } from './model';
import { CONFIG_REPO, COURSE_REPO, JOIN_REPO } from '../model/names';

export interface Check {
  text: string;
  ok: boolean | null; // null: could not tell
  hint?: string;
  /** "Could not tell" here does not hold the step back. */
  soft?: boolean;
}

export const allOk = (list: Check[] | null | undefined): boolean => !!list?.length && list.every((c) => c.ok === true || (c.ok === null && !!c.soft));

function why(e: unknown): string {
  return e instanceof GitHubError ? e.message : e instanceof Error ? e.message : String(e);
}

export interface OrgCheck {
  /** The org's id, for the app's install link; null until the org is read. */
  id: number | null;
  checks: Check[];
}

/**
 * The three things only a person can do on GitHub: the org exists, the console app is
 * installed on it (left out when the build names no app), and the bot is an active owner.
 * Whether the app is installed can only be told from an App token; any other token gets a
 * line that says so and does not block.
 */
export async function checkOrg(client: GitHubClient, org: string, opts: { kind?: TokenKind; slug?: string } = {}): Promise<OrgCheck> {
  const slug = opts.slug ?? APP_SLUG;
  const appText = `The console app is installed on ${org}`;
  const botText = `${BOT} can manage it`;
  let id: number | null = null;
  let exists: boolean | null;
  try {
    const o = await client.getOrg(org);
    exists = o !== null;
    id = o?.id ?? null;
  } catch {
    exists = null;
  }
  const first: Check = { text: `The org ${org} exists`, ok: exists, hint: exists === null ? 'GitHub did not answer; checking again.' : undefined };
  const later = 'Checked once the org exists.';
  if (!exists) return { id, checks: [first, ...(slug ? [{ text: appText, ok: false, hint: later }] : []), { text: botText, ok: false, hint: later }] };
  const [app, bot] = await Promise.all([slug ? checkApp(client, org, appText, opts.kind) : null, checkBot(client, org, botText)]);
  return { id, checks: [first, ...(app ? [app] : []), bot] };
}

async function checkApp(client: GitHubClient, org: string, text: string, kind: TokenKind | undefined): Promise<Check> {
  if (kind !== 'app') return { text: `Cannot tell from this token; the console app must be installed on ${org}.`, ok: null, soft: true };
  try {
    const on = (await client.listInstallationAccounts()).some((a) => a.login.toLowerCase() === org.toLowerCase());
    return on ? { text, ok: true } : { text, ok: false, hint: 'Install it; GitHub brings you back here.' };
  } catch {
    return { text, ok: null, hint: 'GitHub did not answer; checking again.' };
  }
}

async function checkBot(client: GitHubClient, org: string, text: string): Promise<Check> {
  let m: { state: string; role: string } | null | undefined;
  try {
    m = await client.getOrgMembership(org, BOT);
  } catch (e) {
    m = e instanceof GitHubError && e.status === 404 ? null : undefined;
  }
  return m === undefined ? { text, ok: null, hint: `Only an owner of ${org} can see its members’ roles. Sign in as an owner.` }
    : m === null ? { text, ok: false }
    : m.state !== 'active' ? { text, ok: false, hint: 'Invited. The DSL team registers new courses; the bot then joins by itself.' }
    : m.role !== 'admin' ? { text, ok: false, hint: `${BOT} is a member, not an Owner. Make it an Owner on the People page.` }
    : { text, ok: true };
}

/** Bootstrap Course Org left a course hub with its Console workflow. */
export async function checkCourseSetUp(client: GitHubClient, org: string): Promise<Check[]> {
  try {
    const repo = await client.getRepo(org, COURSE_REPO);
    const topics = repo ? repo.topics ?? (await client.getRepoTopics(org, COURSE_REPO)) : [];
    const hub = !!repo && topics.includes(COURSE_HUB_TOPIC);
    const [meta, consoleWf] = hub
      ? await Promise.all([client.getContents(org, COURSE_REPO, 'dsl-course.yml'), client.getContents(org, COURSE_REPO, '.github/workflows/console.yml')])
      : [null, null];
    return [
      { text: 'Course settings are in place (dsl-course.yml)', ok: hub && !!meta, hint: hub ? undefined : 'Set up the course; it takes a few minutes.' },
      { text: 'The Console workflow is in place', ok: !!consoleWf, hint: consoleWf ? undefined : 'Added by the set-up run.' },
    ];
  } catch (e) {
    return [{ text: 'Course settings are in place', ok: null, hint: `GitHub did not answer (${why(e)}); check again.` }];
  }
}

/** Bootstrap semester left the config repo and registered the semester with its course. */
export async function checkCohortSetUp(client: GitHubClient, courseOrg: string, cohortOrg: string): Promise<Check[]> {
  try {
    const [cfg, join, reg] = await Promise.all([
      client.getRepo(cohortOrg, CONFIG_REPO),
      client.getRepo(cohortOrg, JOIN_REPO),
      client.getContents(courseOrg, COURSE_REPO, REGISTRY_PATH),
    ]);
    return [
      { text: `The semester’s settings repo is in place (${CONFIG_REPO})`, ok: !!cfg },
      { text: `The join form is in place (${JOIN_REPO})`, ok: !!join },
      { text: 'The course lists the semester', ok: parseRegistry(reg?.text).includes(cohortOrg) },
    ];
  } catch (e) {
    return [{ text: 'The semester is set up', ok: null, hint: `GitHub did not answer (${why(e)}); check again.` }];
  }
}

/** `repo` is not taken in `org`. Absence is only claimed on a 404; any other failure is "could not tell". */
export async function checkFree(client: GitHubClient, org: string, repo: string, what: string): Promise<Check> {
  try {
    const r = await client.getRepo(org, repo);
    return r ? { text: `${what} is free in the course`, ok: false, hint: `${org}/${repo} already exists. Choose another number or semester.` } : { text: `${what} is free in the course`, ok: true };
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
        { text: 'Settings check out (grading_config.yml parses)', ok: parses, hint: cfg && !parses ? 'grading_config.yml does not parse; fix it on the assignment template’s settings.' : undefined },
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
    // The answer and the end of busy in one render, so a poll never sees one without the other.
    const done = (v?: T) => {
      if (!live) return;
      if (v !== undefined) setValue(v);
      setBusy(false);
    };
    fn().then(done, () => done());
    return () => {
      live = false;
    };
  }, [...deps, tick, !!fn]);
  return { value, busy, run: () => setTick((t) => t + 1) };
}

/** How often an open step re-checks by itself. */
export const POLL_MS = 10_000;

/**
 * While `on`, run `live` again every `ms` and when the window regains focus or becomes
 * visible, skipping a tick while a check is still in flight or the page is hidden. Stops
 * when `on` turns false (all checks passed, or the step was left) or the component goes.
 */
export function usePoll(live: Live<unknown>, on: boolean, ms = POLL_MS): void {
  const cur = useRef(live);
  cur.current = live;
  useEffect(() => {
    if (!on) return;
    const again = () => {
      if (!cur.current.busy && !document.hidden) cur.current.run();
    };
    const id = setInterval(again, ms);
    window.addEventListener('focus', again);
    document.addEventListener('visibilitychange', again);
    return () => {
      clearInterval(id);
      window.removeEventListener('focus', again);
      document.removeEventListener('visibilitychange', again);
    };
  }, [on, ms]);
}
