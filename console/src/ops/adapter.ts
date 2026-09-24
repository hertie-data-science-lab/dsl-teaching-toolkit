// The backend adapter: how the console asks the engine to do something and learns what
// happened. No screen imports dispatch code; they go through `Adapter`.
//
// DispatchAdapter (contracts sections 1, 2 and 5): builds a `dsl.request/1`, validates it
// against request.schema.json and the op's args schema, dispatches the course org's
// Console workflow with `return_run_details`, polls the run and its job (the client
// revalidates with ETags, so an unchanged poll is a 304), then reads the public
// `dsl-outcome` annotation off the job's check run and the private outcome file.

import type { ValidateFunction } from 'ajv/dist/2020';
import outcomeSchema from '../../schemas/outcome.schema.json';
import requestSchema from '../../schemas/request.schema.json';
import type { GitHubClient, Job } from '../github/client';
import type { Outcome } from '../model/types';
import { validator } from '../model/validate';
import { opSpec } from './registry';
import { CONFIG_REPO, COURSE_REPO, OUTCOMES_DIR } from '../model/names';

export const CONSOLE_WORKFLOW = 'console.yml';
export const CONSOLE_REF = 'main';
export const CONSOLE_REPO = COURSE_REPO;
export const CLIENT = 'console/0.1';
export const OUTCOME_TITLE = 'dsl-outcome';
export { OUTCOMES_DIR };
/** A private outcome file's path inside the semester's config repo. */
export const outcomePath = (op: string): string => `${OUTCOMES_DIR}/${op}.json`;

export interface RequestInput {
  op: string;
  courseOrg: string;
  cohortOrg?: string;
  args: Record<string, unknown>;
  preview: boolean;
}

export interface Request {
  schema: 'dsl.request/1';
  op: string;
  actor: string;
  course_org: string;
  semester_org?: string;
  args: Record<string, unknown>;
  preview: boolean;
  client: string;
}

export interface Handle {
  op: string;
  runId: number;
  htmlUrl: string;
  preview: boolean;
  courseOrg: string;
  cohortOrg?: string;
}

export type StepState = 'waiting' | 'running' | 'done' | 'failed' | 'skipped';

export interface Step {
  name: string;
  state: StepState;
}

export interface Progress {
  state: 'queued' | 'running' | 'completed';
  conclusion: string | null;
  steps: Step[];
  htmlUrl: string;
}

export interface Result {
  /** The public outcome, or the private record when the annotation could not be read. */
  outcome: Outcome | null;
  /** Per-person lines, from the private file only. */
  people: { handle: string; text: string }[];
  /** Handles from the private file that the PUBLIC outcome names: an engine redaction bug. */
  leaked: string[];
}

export interface Adapter {
  submit(input: RequestInput): Promise<Handle>;
  watch(h: Handle): Promise<Progress>;
  outcome(h: Handle): Promise<Result>;
  cancel(h: Handle): Promise<void>;
}

export class RequestInvalid extends Error {
  constructor(readonly errors: string[]) {
    super(`The request is not valid: ${errors.join('; ')}`);
    this.name = 'RequestInvalid';
  }
}

const requestValidator = validator(requestSchema);
const outcomeValidator = validator(outcomeSchema);

function messages(v: ValidateFunction): string[] {
  return (v.errors ?? []).map((e) => `${e.instancePath || '/'} ${e.message ?? 'is invalid'}`);
}

/** The args an op accepts, validated against its registry schema; errors as sentences. */
export function validateArgs(op: string, args: Record<string, unknown>): string[] {
  const v = validator(opSpec(op).args_schema);
  return v(args) ? [] : messages(v);
}

/** Drop the blanks a form leaves (an empty optional field means "the default"). */
function clean(args: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(args).filter(([, v]) => v !== undefined && v !== null && v !== ''));
}

export function buildRequest(actor: string, input: RequestInput): Request {
  const spec = opSpec(input.op);
  const args = clean(input.args);
  const req: Request = {
    schema: 'dsl.request/1',
    op: input.op,
    actor,
    course_org: input.courseOrg,
    ...(spec.scope === 'cohort' && input.cohortOrg ? { semester_org: input.cohortOrg } : {}),
    args,
    preview: input.preview,
    client: CLIENT,
  };
  const errors = [...(requestValidator(req) ? [] : messages(requestValidator)), ...validateArgs(input.op, args)];
  if (spec.scope === 'cohort' && !input.cohortOrg) errors.push('a semester operation needs a semester');
  if (input.preview && !spec.preview) errors.push(`${input.op} has no preview`);
  if (errors.length) throw new RequestInvalid(errors);
  return req;
}

/** The outcome in a `dsl-outcome` annotation's message, or null when it is not one. */
export function parseOutcome(message: string): Outcome | null {
  let data: unknown;
  try {
    data = JSON.parse(message);
  } catch {
    return null;
  }
  return outcomeValidator(data) ? (data as unknown as Outcome) : null;
}

/** Every handle in `people` that appears anywhere in the public outcome. */
export function leakedHandles(pub: Outcome, people: { handle: string }[]): string[] {
  const text = JSON.stringify({ ...pub, actor: '' });
  return people
    .map((p) => p.handle)
    .filter((h) => /^[A-Za-z0-9-]+$/.test(h) && new RegExp(`(?<![A-Za-z0-9-])${h}(?![A-Za-z0-9-])`, 'i').test(text));
}

const STEP_WORDS: [RegExp, string][] = [
  [/^Verify the user/, 'Checking you may do this'],
  [/checkout/i, 'Getting the engine'],
  [/setup-python/i, 'Preparing'],
  [/^Run the request$/, ''], // named by the caller: the op's own running sentence
];

/** The job's steps an instructor reads, in their words. Setup, teardown and reporting steps are left out. */
export function stepsOf(job: Job | undefined, running: string): Step[] {
  const out: Step[] = [];
  for (const s of job?.steps ?? []) {
    const m = STEP_WORDS.find(([re]) => re.test(s.name));
    if (!m) continue;
    const state: StepState =
      s.status === 'completed' ? (s.conclusion === 'success' ? 'done' : s.conclusion === 'skipped' ? 'skipped' : 'failed') : s.status === 'in_progress' ? 'running' : 'waiting';
    out.push({ name: m[1] || running, state });
  }
  return out;
}

export class DispatchAdapter implements Adapter {
  constructor(
    private readonly client: GitHubClient,
    private readonly actor: () => string,
    private readonly running: (op: string) => string = () => 'Running',
  ) {}

  async submit(input: RequestInput): Promise<Handle> {
    const req = buildRequest(this.actor(), input);
    const r = await this.client.dispatchWorkflow({
      owner: input.courseOrg,
      repo: CONSOLE_REPO,
      workflow: CONSOLE_WORKFLOW,
      ref: CONSOLE_REF,
      inputs: { request: JSON.stringify(req) },
    });
    if (!r?.workflow_run_id) throw new Error('GitHub started the Console workflow but did not say which run it is.');
    return { op: input.op, runId: r.workflow_run_id, htmlUrl: r.html_url, preview: input.preview, courseOrg: input.courseOrg, cohortOrg: req.semester_org };
  }

  async watch(h: Handle): Promise<Progress> {
    const run = await this.client.getRun(h.courseOrg, CONSOLE_REPO, h.runId);
    const jobs = run.status === 'queued' ? [] : await this.client.listJobs(h.courseOrg, CONSOLE_REPO, h.runId);
    const state = run.status === 'completed' ? 'completed' : run.status === 'in_progress' ? 'running' : 'queued';
    return { state, conclusion: run.conclusion, steps: stepsOf(jobs[0], this.running(h.op)), htmlUrl: run.html_url || h.htmlUrl };
  }

  async outcome(h: Handle): Promise<Result> {
    let pub: Outcome | null = null;
    for (const job of await this.client.listJobs(h.courseOrg, CONSOLE_REPO, h.runId)) {
      const notes = await this.client.listCheckRunAnnotations(h.courseOrg, CONSOLE_REPO, job.id);
      const note = notes.find((a) => a.title === OUTCOME_TITLE);
      if (note) {
        pub = parseOutcome(note.message);
        if (pub) break;
      }
    }
    const [owner, repo] = h.cohortOrg ? [h.cohortOrg, CONFIG_REPO] : [h.courseOrg, CONSOLE_REPO];
    let priv: Outcome | null = null;
    try {
      const f = await this.client.getContents(owner, repo, outcomePath(h.op));
      const o = f ? parseOutcome(f.text) : null;
      priv = o && o.run_id === h.runId ? o : null;
    } catch {
      priv = null;
    }
    const people = priv?.people ?? [];
    const outcome: Outcome | null = pub ?? (priv ? { ...priv, people: undefined } : null);
    return { outcome, people, leaked: pub ? leakedHandles(pub, people) : [] };
  }

  async cancel(h: Handle): Promise<void> {
    await this.client.cancelRun(h.courseOrg, CONSOLE_REPO, h.runId);
  }
}
