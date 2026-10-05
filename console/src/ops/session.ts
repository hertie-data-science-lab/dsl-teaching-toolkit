// The operation panel's state: the one operation open at a time, what this session has
// previewed (the gate), and the runs this session finished (for the Operations list). A
// run that cannot be read `maxMisses` times in a row is given up on; sign-out (`reset`) ends
// every watch and forgets the runs and the gate, so the next person starts clean.

import { signal } from '@preact/signals';
import type { ComponentChildren } from 'preact';
import { effective } from '../forms/Form';
import { wait } from '../github/client';
import type { Operation } from '../model/types';
import type { Tiers } from '../tiers/types';
import type { Adapter, Handle, Progress, Result } from './adapter';
import { modeOf, type OpMode } from './registry';

/** One operation as the panel offers it: the op, its derived args, and its copy. */
export interface OpDef {
  /** The gate's subject: a preview of `key` unlocks the verb for `key` only. */
  key: string;
  op: string;
  courseOrg: string;
  cohortOrg?: string;
  name: string; // eyebrow: "Release early"
  where: string; // eyebrow, after the comma: "Fall 2026"
  title: string;
  intro: string;
  confirm?: string; // a warning line under the intro ("Their old codes stop working.")
  verb: string;
  running: string;
  cancel: string;
  args: Record<string, unknown>;
  /** Fields of the op's args schema the Options fold offers, and how. */
  options?: Tiers;
  /** What the Options fold shows above the fields and cannot change ("always" lines). */
  fixed?: { label: string; note: string }[];
  /** A box that must be ticked before the verb runs; `arg` is set true in the request when it is. */
  needsCheck?: { label: string; sub: string; arg?: string };
  proposed?: boolean;
  previewProposed?: boolean;
  /** The preview's button in the panel and on the screen, where it is not a preview to the user ("Copy"). */
  previewLabel?: string;
  /** An information panel with no operation behind it (Export). */
  info?: ComponentChildren;
  /** Where on GitHub the verb's change shows (a repo, branch or file): "See on GitHub" once it has run. A function reads the run's args. */
  target?: string | ((args: Record<string, unknown>) => string);
}

export type Phase = 'ready' | 'running' | 'done';

export interface Current {
  def: OpDef;
  mode: OpMode;
  values: Record<string, unknown>;
  checked: boolean;
  phase: Phase;
  running: 'preview' | 'run' | null;
  handle: Handle | null;
  progress: Progress | null;
  dry: Result | null; // the last preview's result
  result: Result | null; // the verb's result
  error: string | null;
  min: boolean;
  /** Stop was pressed and GitHub has not ended the run yet. */
  stopping: boolean;
  /** Stop was pressed for this run and GitHub took the cancel (a 409 included: it had already ended). */
  stopRequested: boolean;
  /** The last run was stopped: GitHub says `cancelled`, or Stop was pressed and the run ended anyway. */
  stopped: boolean;
}

export interface SessionOptions {
  pollMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  /** Called after a run (not a preview) finishes, to refresh what the screens show. */
  onFinished?: (def: OpDef) => void;
  /** How many times to re-read a finished run whose outcome is not there yet. */
  outcomeTries?: number;
  /** How many polls in a row may fail before the panel stops following the run (default 20, a minute at 3 s). */
  maxMisses?: number;
}

export const LOST_RUN = 'Could not read the run on GitHub, so the console stopped following it. Open it on GitHub to see how it ended.';
export const MISSED_POLL = 'Could not read the run just now; trying again.';

/** The args a request for `def` carries: the options as the form resolves them, and the tick. */
function requestArgs(def: OpDef, values: Record<string, unknown>, checked: boolean): Record<string, unknown> {
  const args = def.options ? effective(def.options, values) : { ...values };
  if (def.needsCheck?.arg && checked) args[def.needsCheck.arg] = true;
  return args;
}

/** The args as one comparable string: sorted, blanks dropped as the request drops them. */
function argsKey(args: Record<string, unknown>): string {
  const kept = Object.entries(args).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return JSON.stringify(kept.sort(([a], [b]) => a.localeCompare(b)));
}

export class OpsSession {
  readonly current = signal<Current | null>(null);
  readonly runs = signal<(Operation & { cohort?: string; course: string })[]>([]);
  /** The gate: per op and scope, the args its last good preview in this session ran with. */
  readonly previewed = signal<Record<string, string>>({});
  /** Per op and scope, the generated text its last good preview carried (`outcome.block`). */
  readonly blocks = signal<Record<string, string>>({});
  readonly notice = signal<string | null>(null);
  private readonly pollMs: number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;
  /** Bumped by `reset`: a watch started under an older one stops without recording anything. */
  private generation = 0;

  constructor(
    private readonly adapter: Adapter,
    private readonly opts: SessionOptions = {},
  ) {
    this.pollMs = opts.pollMs ?? 3000;
    this.sleep = opts.sleep ?? wait;
    this.now = opts.now ?? Date.now;
  }

  /** Sign-out: stop following every run and forget this session's runs, previews and panel. */
  reset(): void {
    this.generation++;
    this.current.value = null;
    this.runs.value = [];
    this.previewed.value = {};
    this.blocks.value = {};
    this.notice.value = null;
  }

  private gateKey(def: OpDef): string {
    return `${def.op}|${def.cohortOrg ?? def.courseOrg}|${def.key}`;
  }

  /**
   * Whether this session previewed `def` with exactly these options: a preview unlocks the
   * run it showed, not one whose options were changed after it.
   */
  isPreviewed(def: OpDef, values: Record<string, unknown> = def.args, checked = false): boolean {
    return this.previewed.value[this.gateKey(def)] === argsKey(requestArgs(def, values, checked));
  }

  /** The text the last good preview of `def` in this session generated, or null. */
  lastBlock(def: OpDef): string | null {
    return this.blocks.value[this.gateKey(def)] ?? null;
  }

  /** Whether the verb may run now: gated ops need this session's preview, a check box its tick. */
  canRun(c: Current): boolean {
    if (c.phase === 'running') return false;
    if (c.mode === 'gated' && !this.isPreviewed(c.def, c.values, c.checked)) return false;
    if (c.def.needsCheck && !c.checked) return false;
    return true;
  }

  private patch(p: Partial<Current>): void {
    const c = this.current.value;
    if (c) this.current.value = { ...c, ...p };
  }

  /** Open the panel for `def`; `start` also presses Preview or the verb, when allowed. */
  open(def: OpDef, start?: 'preview' | 'run'): void {
    const c = this.current.value;
    if (c && c.phase === 'running') {
      this.patch({ min: false });
      this.notice.value = `One operation at a time: ${c.def.running} is still running.`;
      return;
    }
    this.notice.value = null;
    if (c && c.phase === 'ready' && this.gateKey(c.def) === this.gateKey(def) && !def.info) {
      // The same operation again (the page's verb after the panel's preview): keep its
      // options and its preview rather than starting over.
      this.patch({ min: false });
      if (start === 'run' && this.canRun(this.current.value!)) void this.start(c.mode === 'previewOnly' ? 'preview' : 'run');
      else if (start === 'preview' && (c.mode === 'gated' || c.mode === 'preview')) void this.start('preview');
      return;
    }
    this.current.value = {
      def, mode: modeOf(def.op), values: { ...def.args }, checked: false, phase: 'ready', running: null,
      handle: null, progress: null, dry: null, result: null, error: null, min: false, stopping: false, stopRequested: false, stopped: false,
    };
    if (def.info) return;
    const cur = this.current.value;
    if (start === 'preview' && (cur.mode === 'gated' || cur.mode === 'preview')) void this.start('preview');
    else if (start === 'run' && this.canRun(cur)) void this.start(cur.mode === 'previewOnly' ? 'preview' : 'run');
  }

  setValues(values: Record<string, unknown>): void {
    this.patch({ values });
  }

  setChecked(checked: boolean): void {
    this.patch({ checked });
  }

  minimise(): void {
    this.patch({ min: true });
  }

  show(): void {
    this.patch({ min: false });
  }

  /** Close the panel; a running operation carries on in the bar. */
  close(): void {
    const c = this.current.value;
    if (c?.phase === 'running') this.patch({ min: true });
    else this.current.value = null;
  }

  /**
   * Press Stop: cancel the workflow run. Pressed before GitHub has named the run, the cancel
   * goes as soon as it does. The panel says "Stopping" until the run ends, then "Stopped".
   */
  async cancel(): Promise<void> {
    const c = this.current.value;
    if (!c || c.phase !== 'running' || c.stopping) return;
    this.patch({ stopping: true, stopRequested: true, error: null });
    if (c.handle) await this.sendCancel(c.handle);
  }

  private async sendCancel(handle: Handle): Promise<void> {
    try {
      await this.adapter.cancel(handle);
    } catch (e) {
      if (this.current.value?.handle === handle) this.patch({ stopping: false, stopRequested: false, error: `Could not stop it: ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  /** Press Preview (`preview`) or the verb (`run`) and follow the run to its outcome. */
  async start(kind: 'preview' | 'run'): Promise<void> {
    const c = this.current.value;
    if (!c || c.phase === 'running') return;
    if (kind === 'run' && !this.canRun(c)) return;
    const def = c.def;
    const preview = kind === 'preview';
    const generation = this.generation;
    const gone = () => this.generation !== generation;
    const args = requestArgs(def, c.values, c.checked);
    this.patch({ phase: 'running', running: kind, error: null, progress: null, handle: null, stopping: false, stopRequested: false, stopped: false, ...(preview ? { dry: null } : { result: null }) });
    let handle: Handle;
    try {
      handle = await this.adapter.submit({ op: def.op, courseOrg: def.courseOrg, cohortOrg: def.cohortOrg, args, preview });
    } catch (e) {
      if (gone()) return;
      this.patch({ phase: 'ready', running: null, stopping: false, error: e instanceof Error ? e.message : String(e) });
      return;
    }
    if (gone()) return;
    this.patch({ handle });
    if (this.current.value?.stopping) void this.sendCancel(handle);
    const mine = () => this.current.value?.handle === handle;
    const maxMisses = this.opts.maxMisses ?? 20;
    let progress: Progress | null = null;
    for (let misses = 0; ; ) {
      try {
        progress = await this.adapter.watch(handle);
        misses = 0;
        if (gone()) return;
        if (mine()) this.patch({ progress, ...(this.current.value!.error === MISSED_POLL ? { error: null } : {}) });
      } catch {
        if (gone()) return;
        if (++misses >= maxMisses) {
          if (mine()) this.patch({ phase: 'ready', running: null, stopping: false, error: LOST_RUN });
          return;
        }
        if (mine()) this.patch({ error: MISSED_POLL });
      }
      if (progress?.state === 'completed') break;
      await this.sleep(this.pollMs);
      if (gone()) return;
    }
    let result: Result = { outcome: null, people: [], leaked: [] };
    const tries = this.opts.outcomeTries ?? 3;
    for (let i = 0; i < tries; i++) {
      try {
        result = await this.adapter.outcome(handle);
      } catch {
        /* retried below */
      }
      if (result.outcome) break;
      if (i + 1 < tries) await this.sleep(this.pollMs);
    }
    if (gone()) return;
    const o = result.outcome;
    const stopped = progress?.conclusion === 'cancelled' || !!this.current.value?.stopRequested;
    const finished = o?.finished || new Date(this.now()).toISOString();
    this.runs.value = [
      {
        run_id: handle.runId, op: def.op, conclusion: o?.conclusion ?? (stopped ? 'skipped' : 'failed'), finished, course: def.courseOrg, cohort: def.cohortOrg,
        summary: o?.summary ?? (stopped ? 'Stopped before it finished.' : 'The run ended without reporting what it did. Open the run on GitHub.'),
      },
      ...this.runs.value,
    ];
    const ok = !!o && o.conclusion !== 'failed';
    if (preview && ok) this.previewed.value = { ...this.previewed.value, [this.gateKey(def)]: argsKey(args) };
    if (preview && ok && o.block) this.blocks.value = { ...this.blocks.value, [this.gateKey(def)]: o.block };
    if (this.current.value?.handle === handle) {
      // A refused cancel's error is about a run that has now ended either way.
      const ended = { running: null, stopping: false, stopped, error: null };
      this.patch(preview ? { ...ended, phase: 'ready', dry: result } : { ...ended, phase: 'done', result });
    }
    if (!preview) this.opts.onFinished?.(def);
  }
}

/** The Operations list: the status file's record, then this session's runs it does not have yet. */
export function mergeOperations(fromStatus: Operation[], session: Operation[]): Operation[] {
  const seen = new Set(fromStatus.map((o) => o.run_id));
  return [...session.filter((o) => !seen.has(o.run_id)), ...fromStatus].sort((a, b) => b.finished.localeCompare(a.finished));
}
