// Setting up a course: the one operation outside the instructor's own orgs. The course org
// has no Console workflow yet, so the wizard dispatches the central toolkit's Bootstrap
// Course Org workflow (`.github/workflows/bootstrap-org.yml`) with the user's token, follows
// its run, and then re-reads the org to verify. That workflow reports no dsl-outcome, so
// the run's conclusion is the verdict and the org's live state is the proof.

import { wait, type GitHubClient } from '../github/client';
import { HANDLE_RE, ORG_NAME_RE } from '../model/policy';

export const CENTRAL = { owner: 'hertie-data-science-lab', repo: 'dsl-teaching-toolkit', workflow: 'bootstrap-org.yml', ref: 'main' } as const;
export const CENTRAL_ACTIONS = `https://github.com/${CENTRAL.owner}/${CENTRAL.repo}/actions/workflows/${CENTRAL.workflow}`;

export interface BootstrapCourse {
  org: string;
  courseName: string;
  code: string;
  admins: string[];
}

/**
 * The workflow's inputs. `set_secret` and `central_ref` are Hidden (inputs.md): the bot token
 * is always propagated, and `release` is the workflow's only tier on purpose.
 */
export function bootstrapInputs(b: BootstrapCourse): Record<string, string> {
  const errors: string[] = [];
  if (!ORG_NAME_RE.test(b.org)) errors.push('the org name');
  if (!b.courseName.trim() || /[\x00-\x1f]/.test(b.courseName)) errors.push('the course name');
  if (!b.code.trim() || /[\x00-\x1f]/.test(b.code)) errors.push('the course code');
  const admins = b.admins.map((a) => a.trim()).filter(Boolean);
  if (admins.some((a) => !HANDLE_RE.test(a))) errors.push('a course admin handle');
  if (errors.length) throw new Error(`Check ${errors.join(', ')}.`);
  return { org: b.org, course_name: b.courseName.trim(), course_code: b.code.trim(), set_secret: 'true', admin: admins.join(','), central_ref: 'release' };
}

export interface CentralRun {
  runId: number;
  htmlUrl: string;
  state: 'queued' | 'running' | 'completed';
  conclusion: string | null;
}

/** Dispatch Bootstrap Course Org and follow it to the end, reporting each poll. */
export async function runBootstrap(
  client: GitHubClient,
  b: BootstrapCourse,
  report: (r: CentralRun) => void,
  { pollMs = 5000, sleep = wait }: { pollMs?: number; sleep?: (ms: number) => Promise<void> } = {},
): Promise<CentralRun> {
  const d = await client.dispatchWorkflow({ owner: CENTRAL.owner, repo: CENTRAL.repo, workflow: CENTRAL.workflow, ref: CENTRAL.ref, inputs: bootstrapInputs(b) });
  if (!d?.workflow_run_id) throw new Error('GitHub started the set-up but did not say which run it is.');
  let r: CentralRun = { runId: d.workflow_run_id, htmlUrl: d.html_url, state: 'queued', conclusion: null };
  report(r);
  for (;;) {
    try {
      const run = await client.getRun(CENTRAL.owner, CENTRAL.repo, r.runId);
      r = { ...r, htmlUrl: run.html_url || r.htmlUrl, state: run.status === 'completed' ? 'completed' : run.status === 'in_progress' ? 'running' : 'queued', conclusion: run.conclusion };
      report(r);
    } catch {
      /* a missed poll is retried */
    }
    if (r.state === 'completed') return r;
    await sleep(pollMs);
  }
}
