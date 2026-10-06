// Saving a file as the signed-in instructor: a sha-conditional write, then the verdict of
// the checks that commit sets off ("Checking... Valid. Problem cleared." or what failed).
// The engine's own checks (Validate schedule, the digests) stay the judge; the console only
// reports them.

import type { ValidateFunction } from 'ajv/dist/2020';
import { useState } from 'preact/hooks';
import type { Env } from '../env';
import { ConflictError, authorOf, wait, type GitHubClient } from '../github/client';
import type { Problem } from '../model/types';

export interface Target {
  owner: string;
  repo: string;
  path: string;
  branch?: string;
}

export type SaveState =
  | { kind: 'idle' }
  | { kind: 'busy'; text: string }
  | { kind: 'ok'; text: string }
  | { kind: 'bad'; text: string; conflict?: boolean };

export interface Verdict {
  ok: boolean | null; // null: no check ran, or none finished in time
  text: string;
}

/** Why a save was refused: the first two things `validate` found wrong with `what`. */
export function invalidText(what: string, validate: Pick<ValidateFunction, 'errors'>): string {
  return `Not saved: ${what} would not be valid (${(validate.errors ?? []).map((e) => `${e.instancePath} ${e.message}`).slice(0, 2).join('; ')}).`;
}

const FAILED = new Set(['failure', 'timed_out', 'action_required', 'startup_failure']);
/** Follow the check runs on `commit` until they finish, and say what they found. */
export async function verdict(
  client: GitHubClient,
  owner: string,
  repo: string,
  commit: string,
  { pollMs = 4000, tries = 45, quietTries = 5, sleep = wait }: { pollMs?: number; tries?: number; quietTries?: number; sleep?: (ms: number) => Promise<void> } = {},
): Promise<Verdict> {
  for (let i = 0; i < tries; i++) {
    let runs;
    try {
      runs = await client.listCheckRunsForRef(owner, repo, commit);
    } catch {
      runs = null;
    }
    if (runs && runs.length && runs.every((r) => r.status === 'completed')) {
      const bad = runs.find((r) => FAILED.has(r.conclusion ?? ''));
      if (!bad) return { ok: true, text: 'Valid.' };
      let why = bad.output?.title || '';
      try {
        const notes = await client.listCheckRunAnnotations(owner, repo, bad.id);
        const n = notes.find((a) => a.annotation_level === 'failure') ?? notes[0];
        if (n) why = n.message;
      } catch {
        /* the check's title is enough */
      }
      return { ok: false, text: `${bad.name} failed${why ? `: ${why}` : '.'}` };
    }
    if (runs && !runs.length && i + 1 >= quietTries) return { ok: null, text: '' };
    await sleep(pollMs);
  }
  return { ok: null, text: 'Still checking; the result will show on the Problems list.' };
}

/** Problems whose fix is in this file. */
export function problemsIn(problems: Problem[] | undefined, t: Target): Problem[] {
  return (problems ?? []).filter((p) => p.fix && p.fix.repo === `${t.owner}/${t.repo}` && p.fix.path === t.path);
}

export const CONFLICT =
  'This file changed on GitHub since you opened it, so nothing was saved. Reload it to see the change, then make yours again; or use Edit the file.';

export interface SaveOptions {
  message: string;
  /** Where the status that lists this file's problems lives: the semester's or the course's. */
  statusRepo?: [string, string];
}

/** Write `text` over `sha` (null for a new file), then report the checks. */
export async function saveText(env: Env, t: Target, text: string | null, sha: string | null, opts: SaveOptions, report: (s: SaveState) => void): Promise<boolean> {
  report({ kind: 'busy', text: 'Saving…' });
  let commit: string;
  try {
    if (text === null) {
      commit = (await env.client.deleteContents({ owner: t.owner, repo: t.repo, path: t.path, sha: sha!, message: opts.message, author: authorOf(env.user), branch: t.branch })).commit;
      env.files.put(t.owner, t.repo, t.path, t.branch, null, '');
    } else {
      const r = await env.client.putContents({ owner: t.owner, repo: t.repo, path: t.path, text, sha, message: opts.message, author: authorOf(env.user), branch: t.branch });
      commit = r.commit;
      env.files.put(t.owner, t.repo, t.path, t.branch, text, r.sha);
    }
  } catch (e) {
    if (e instanceof ConflictError || (e instanceof Error && 'status' in e && (e as { status: number }).status === 422 && /sha/i.test(e.message)))
      report({ kind: 'bad', text: CONFLICT, conflict: true });
    else report({ kind: 'bad', text: `Not saved: ${e instanceof Error ? e.message : String(e)}` });
    return false;
  }
  report({ kind: 'busy', text: 'Checking…' });
  const sr = opts.statusRepo;
  const before = sr ? statusProblems(env, sr, t).length : 0;
  const v = await verdict(env.client, t.owner, t.repo, commit, { pollMs: env.pollMs });
  if (v.ok === false) {
    report({ kind: 'bad', text: `Saved, but ${v.text}` });
    return true;
  }
  let after = before;
  if (sr) {
    await env.statuses.reload(sr[0], sr[1]);
    after = statusProblems(env, sr, t).length;
  }
  const head = v.ok ? 'Valid.' : 'Saved.';
  const tail = before > after ? (after ? ` ${before - after} problem${before - after > 1 ? 's' : ''} cleared; ${after} remain${after > 1 ? '' : 's'}.` : ' Problem cleared.') : '';
  report({ kind: 'ok', text: `${head}${tail}${v.ok === null && v.text ? ` ${v.text}` : ''}` });
  return true;
}

function statusProblems(env: Env, sr: [string, string], t: Target): Problem[] {
  const l = env.statuses.get(sr[0], sr[1]).value;
  return l.kind === 'ready' ? problemsIn(l.status.problems, t) : [];
}

export interface Step {
  target: Target;
  text: string;
  sha: string | null;
  opts: SaveOptions;
}

/**
 * Commit one file, then a second that depends on it (the schedule, then assignments.yml).
 * `afterFirst` runs as soon as the first commit lands, whatever happens next, so a retry
 * never writes it twice. A second commit that fails is reported as `partial: <why>`: the
 * first file is saved and the message says so.
 */
export async function saveSteps(env: Env | null, report: (s: SaveState) => void, first: Step | null, second: Step | null, partial: string, afterFirst: () => void): Promise<boolean> {
  if (!env) {
    report({ kind: 'bad', text: 'Sign in to save.' });
    return false;
  }
  if (first) {
    if (!(await saveText(env, first.target, first.text, first.sha, first.opts, report))) return false;
    afterFirst();
  }
  if (!second) return true;
  const seen: SaveState[] = [];
  const ok = await saveText(env, second.target, second.text, second.sha, second.opts, (s) => {
    seen.push(s);
    if (!first || s.kind !== 'bad') report(s);
  });
  const last = seen[seen.length - 1];
  if (first && last?.kind === 'bad') report(ok ? last : { kind: 'bad', text: `${partial}: ${last.text.replace(/^Not saved: /, '')}`, conflict: last.conflict });
  return ok;
}

/** A Save button's state, and the function that saves. */
export function useSave(env: Env | null): [SaveState, (t: Target, text: string | null, sha: string | null, opts: SaveOptions) => Promise<boolean>, (s: SaveState) => void] {
  const [state, setState] = useState<SaveState>({ kind: 'idle' });
  const run = async (t: Target, text: string | null, sha: string | null, opts: SaveOptions) => {
    if (!env) {
      setState({ kind: 'bad', text: 'Sign in to save.' });
      return false;
    }
    return saveText(env, t, text, sha, opts, setState);
  };
  return [state, run, setState];
}
