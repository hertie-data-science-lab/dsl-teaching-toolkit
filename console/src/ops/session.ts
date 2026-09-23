// The operation panel's state: the one operation open at a time, what this session has
// previewed (the gate), and the runs this session finished (for the Operations list).

import { signal } from '@preact/signals';
import type { ComponentChildren } from 'preact';
import { effective } from '../forms/Form';
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
  /** An information panel with no operation behind it (Export). */
  info?: ComponentChildren;
  /** Links shown once the verb has run. */
  after?: { label: string; href: string }[];
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
}

export interface SessionOptions {
  pollMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  /** Called after a run (not a preview) finishes, to refresh what the screens show. */
  onFinished?: (def: OpDef) => void;
  /** How many times to re-read a finished run whose outcome is not there yet. */
  outcomeTries?: number;
}

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

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
  readonly notice = signal<string | null>(null);
  private readonly pollMs: number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;

  constructor(
    private readonly adapter: Adapter,
    private readonly opts: SessionOptions = {},
  ) {
    this.pollMs = opts.pollMs ?? 3000;
    this.sleep = opts.sleep ?? wait;
    this.now = opts.now ?? Date.now;
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
      handle: null, progress: null, dry: null, result: null, error: null, min: false,
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

  async cancel(): Promise<void> {
    const c = this.current.value;
    if (!c?.handle || c.phase !== 'running') return;
    try {
      await this.adapter.cancel(c.handle);
    } catch (e) {
      this.patch({ error: `Could not stop it: ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  /** Press Preview (`preview`) or the verb (`run`) and follow the run to its outcome. */
  async start(kind: 'preview' | 'run'): Promise<void> {
    const c = this.current.value;
    if (!c || c.phase === 'running') return;
    if (kind === 'run' && !this.canRun(c)) return;
    const def = c.def;
    const preview = kind === 'preview';
    const args = requestArgs(def, c.values, c.checked);
    this.patch({ phase: 'running', running: kind, error: null, progress: null, handle: null, ...(preview ? { dry: null } : { result: null }) });
    let handle: Handle;
    try {
      handle = await this.adapter.submit({ op: def.op, courseOrg: def.courseOrg, cohortOrg: def.cohortOrg, args, preview });
    } catch (e) {
      this.patch({ phase: 'ready', running: null, error: e instanceof Error ? e.message : String(e) });
      return;
    }
    this.patch({ handle });
    let progress: Progress | null = null;
    for (;;) {
      try {
        progress = await this.adapter.watch(handle);
        if (this.current.value?.handle === handle) this.patch({ progress });
      } catch {
        /* a missed poll is retried */
      }
      if (progress?.state === 'completed') break;
      await this.sleep(this.pollMs);
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
    const o = result.outcome;
    const finished = o?.finished || new Date(this.now()).toISOString();
    this.runs.value = [
      {
        run_id: handle.runId, op: def.op, conclusion: o?.conclusion ?? 'failed', finished, course: def.courseOrg, cohort: def.cohortOrg,
        summary: o?.summary ?? (progress?.conclusion === 'cancelled' ? 'Stopped before it finished.' : 'The run ended without reporting what it did. Open the run on GitHub.'),
      },
      ...this.runs.value,
    ];
    const ok = !!o && o.conclusion !== 'failed';
    if (preview && ok) this.previewed.value = { ...this.previewed.value, [this.gateKey(def)]: argsKey(args) };
    if (this.current.value?.handle === handle) {
      this.patch(preview ? { phase: 'ready', running: null, dry: result } : { phase: 'done', running: null, result });
    }
    if (!preview) this.opts.onFinished?.(def);
  }
}

/** The Operations list: the status file's record, then this session's runs it does not have yet. */
export function mergeOperations(fromStatus: Operation[], session: Operation[]): Operation[] {
  const seen = new Set(fromStatus.map((o) => o.run_id));
  return [...session.filter((o) => !seen.has(o.run_id)), ...fromStatus].sort((a, b) => b.finished.localeCompare(a.finished));
}
