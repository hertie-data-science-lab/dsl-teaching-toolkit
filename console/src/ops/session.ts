// The operation panel's state: the one operation open at a time, the text this session's
// previews generated, and the runs this session finished (for the Operations list). A run
// is followed with `poll`: every 3 s, every 10 s once it has run for 30 s, nothing while the
// tab is hidden, and given up on after `maxMisses` unreadable polls in a row; sign-out
// (`reset`) ends every watch and forgets the runs and the previews, so the next person starts clean.

import { signal } from '@preact/signals';
import type { ComponentChildren } from 'preact';
import { effective } from '../forms/Form';
import { wait } from '../github/client';
import { poll } from '../github/poll';
import type { Operation } from '../model/types';
import type { Tiers } from '../tiers/types';
import type { Adapter, Handle, Progress, Result } from './adapter';
import { modeOf, type OpMode } from './registry';

/** One operation as the panel offers it: the op, its derived args, and its copy. */
export interface OpDef {
  /** What a preview is of: a preview of `key` is recorded for `key` only. */
  key: string;
  op: string;
  courseOrg: string;
  cohortOrg?: string;
  name: string; // eyebrow: "Release early"
  where: string; // eyebrow, after the comma: "Fall 2026"
  title: string;
  intro: string;
  confirm?: string; // a warning line under the intro ("Their old codes stop working.")
  /** Lines of figures under the intro ("Final: median 38, range 22–47, 2 held"). */
  facts?: string[];
  /** Shown under a preview's outcome, read afresh after each preview (who would be emailed). */
  dryExtra?: ComponentChildren;
  verb: string;
  /** A `?` beside the verb, wherever it shows: its name, and one paragraph per string. */
  verbHint?: { label: string; text: string[] };
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
  /**
   * The page's verb only opens the panel, where the verb runs (13 Oct 2026 gate inspection):
   * hand out, return marks, archive, update every copy, send new codes, publish website. A
   * preview there is offered, never required.
   */
  confirmInPanel?: boolean;
  /**
   * Run before the verb's request is sent (never before a preview), with the request's args:
   * a setting the run's options change is saved first. A throw stops the run and shows its message.
   */
  beforeRun?: (args: Record<string, unknown>) => Promise<void>;
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
  /** Tests: the pause between polls (default: poll's own, cut short when the tab comes back). */
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

export class OpsSession {
  readonly current = signal<Current | null>(null);
  readonly runs = signal<(Operation & { cohort?: string; course: string })[]>([]);
  /** Per op and scope, the generated text its last good preview carried (`outcome.block`). */
  readonly blocks = signal<Record<string, string>>({});
  readonly notice = signal<string | null>(null);
  private readonly pollMs: number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;
  /** Aborted by `reset`: a watch started before it stops without recording anything. */
  private watches = new AbortController();

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
    this.watches.abort();
    this.watches = new AbortController();
    this.current.value = null;
    this.runs.value = [];
    this.blocks.value = {};
    this.notice.value = null;
  }

  /** One op in one scope: what a preview's text, and the panel kept open, belong to. */
  private opKey(def: OpDef): string {
    return `${def.op}|${def.cohortOrg ?? def.courseOrg}|${def.key}`;
  }

  /** The text the last good preview of `def` in this session generated, or null. */
  lastBlock(def: OpDef): string | null {
    return this.blocks.value[this.opKey(def)] ?? null;
  }

  /** Whether the verb may run now: not while running, and a confirmation box needs its tick. A preview is never required. */
  canRun(c: Current): boolean {
    if (c.phase === 'running') return false;
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
    if (c && c.phase === 'ready' && this.opKey(c.def) === this.opKey(def) && !def.info) {
      // The same operation again (the page's verb after the panel's preview): keep its
      // options and its preview rather than starting over. A confirm-in-panel verb still only
      // focuses the panel: the panel's own verb is the one that runs it. The def is the new
      // one: its figures (facts, the verb's count) may have moved since (a save).
      this.patch({ def, min: false });
      if (start === 'run' && !def.confirmInPanel && this.canRun(this.current.value!)) void this.start(c.mode === 'previewOnly' ? 'preview' : 'run');
      else if (start === 'preview' && c.mode === 'preview') void this.start('preview');
      return;
    }
    this.current.value = {
      def, mode: modeOf(def.op), values: { ...def.args }, checked: false, phase: 'ready', running: null,
      handle: null, progress: null, dry: null, result: null, error: null, min: false, stopping: false, stopRequested: false, stopped: false,
    };
    if (def.info) return;
    const cur = this.current.value;
    if (start === 'preview' && cur.mode === 'preview') void this.start('preview');
    else if (start === 'run' && !def.confirmInPanel && this.canRun(cur)) void this.start(cur.mode === 'previewOnly' ? 'preview' : 'run');
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
    const signal = this.watches.signal;
    const gone = () => signal.aborted;
    const args = requestArgs(def, c.values, c.checked);
    this.patch({ phase: 'running', running: kind, error: null, progress: null, handle: null, stopping: false, stopRequested: false, stopped: false, ...(preview ? { dry: null } : { result: null }) });
    let handle: Handle;
    try {
      if (!preview && def.beforeRun) await def.beforeRun(args);
      if (gone()) return;
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
    let progress = null as Progress | null;
    const end = await poll(
      async () => {
        const p = await this.adapter.watch(handle);
        progress = p;
        if (!gone() && mine()) this.patch({ progress: p, ...(this.current.value!.error === MISSED_POLL ? { error: null } : {}) });
        return p.state === 'completed';
      },
      {
        every: this.pollMs,
        backoff: { after: 30_000, every: Math.max(this.pollMs, 10_000) },
        maxMisses: this.opts.maxMisses ?? 20,
        signal,
        sleep: this.opts.sleep,
        onMiss: () => mine() && this.patch({ error: MISSED_POLL }),
      },
    );
    if (end === 'stopped') return;
    if (end === 'lost') {
      if (mine()) this.patch({ phase: 'ready', running: null, stopping: false, error: LOST_RUN });
      return;
    }
    let result: Result = { outcome: null };
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
    if (preview && ok && o.block) this.blocks.value = { ...this.blocks.value, [this.opKey(def)]: o.block };
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
