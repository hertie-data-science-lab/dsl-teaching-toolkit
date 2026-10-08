// Readiness rows and the dashboards' readiness tabs (decision 0034, amended): the same rows on
// the course and the semester Dashboard, and the verdict line above them. The screens build the
// item list; this file only draws it. Row marks carry the state: a tick (done), a red ! with Fix
// (a problem now or soon), a hollow circle (needed, not done yet), a dotted circle and
// "optional" (a suggestion), a dash and "Waiting for <step>". No colour bar, no legend.

import type { DashTab } from './DashboardTabs';
import { stateWord, verdictMain, verdictTags } from '../model/format';
import { isSuggestion, setupComplete, type ItemState, type RepoReadiness, type Verdict as VerdictT } from '../model/readiness';
import type { Need } from '../model/types';
import { Hint } from './Hint';
import { Check, Ext } from './icons';

export interface Link {
  href: string;
  label: string;
  ext?: boolean;
  /** The link's accessible name when its text alone is ambiguous ("Open settings"). */
  aria?: string;
}

/** One row of the panel or of a repo's checklist. */
export interface SetupItem {
  id: string;
  label: string;
  /** A setup step heads the Setup group; a to-do, To do. A suggestion of either goes to Suggestions. */
  kind: 'step' | 'todo';
  need: Need;
  state: ItemState;
  /** What is missing, one sentence; for a problem, what is wrong. */
  why?: string;
  /** A repo the item belongs to, beside its name. */
  repo?: string;
  hint?: string;
  /** The `?`'s accessible name: "About this step", "About this check", "About the syllabus". */
  hintLabel?: string;
  /** Where an open item is done. */
  link?: Link;
  /** A problem's Fix. */
  fix?: string;
  /** The step a waiting one waits for, by name. */
  waitsFor?: string;
  /** A sentence after the why: when it matters ("Hands out Wed 4 Nov."). */
  when?: string;
  aside?: boolean;
  /** The ids a set-aside writes: more than one for a merged row (the syllabus and its weekly plan). */
  ids?: string[];
}

/** The circle before a suggestion: a button that asks whether to set it aside. */
export function Circle({ label, onClick }: { label: string; onClick: () => void }) {
  return <button class="s-mark s-circle" type="button" title="Set aside…" aria-label={`Set aside ${label}…`} aria-haspopup="dialog" onClick={onClick} />;
}

function Mark({ it, onCircle }: { it: SetupItem; onCircle?: (it: SetupItem) => void }) {
  if (isSuggestion(it.need, it.state) && onCircle) return <Circle label={it.label} onClick={() => onCircle(it)} />;
  if (it.state === 'problem') return <span class="s-mark problem" aria-hidden="true">!</span>;
  return <span class="s-mark" aria-hidden="true">{it.state === 'done' ? <Check /> : it.state === 'waiting' ? '–' : null}</span>;
}

/** One row: its mark, name (repo, "optional", `?`, Fix) and, unless done, why and where. */
export function ItemRow({ it, onCircle }: { it: SetupItem; onCircle?: (it: SetupItem) => void }) {
  const suggested = isSuggestion(it.need, it.state);
  const cls = `${it.state}${suggested ? ' suggested' : ''}`;
  const link = it.link;
  const why = it.state === 'waiting' ? (it.why ?? `Waiting for ${it.waitsFor ?? 'another step'}.`) : it.why;
  return (
    <li class={cls} key={it.id}>
      <Mark it={it} onCircle={onCircle} />
      <span class="s-name">
        {it.label}
        {it.repo ? <span class="s-need slug">{it.repo}</span> : null}
        {suggested ? <span class="s-opt">optional</span> : null}
        {suggested ? null : <span class="sr">: {stateWord(it.state, it.need, it.waitsFor)}</span>}
        {it.hint ? <> <Hint label={it.hintLabel ?? 'About this item'}>{it.hint}</Hint></> : null}
        {it.state === 'problem' && it.fix ? <span class="s-fix"><a class="btn small" href={it.fix}>Fix</a></span> : null}
      </span>
      {it.state !== 'done' && (why || it.when || (link && it.state === 'open')) ? (
        <span class="s-why">
          {why}
          {it.when ? <>{why ? ' ' : null}{it.when}</> : null}
          {link && it.state === 'open' ? (
            <>
              {why || it.when ? ' ' : null}
              <a class="textlink" href={link.href} aria-label={link.aria} {...(link.ext ? { target: '_blank', rel: 'noopener' } : {})}>{link.label}{link.ext ? <Ext /> : null}</a>
            </>
          ) : null}
        </span>
      ) : null}
    </li>
  );
}

/** The rows the tabs list: Setup (one-time steps), Suggestions, Set aside. A needed to-do is not listed: it shows once, at its point of need (its repo's row; on a semester, Coming up). */
export function groups(items: SetupItem[]): { setup: SetupItem[]; suggestions: SetupItem[]; aside: SetupItem[] } {
  const live = items.filter((i) => !i.aside);
  return {
    setup: live.filter((i) => i.kind === 'step' && !isSuggestion(i.need, i.state)),
    suggestions: live.filter((i) => isSuggestion(i.need, i.state)),
    aside: items.filter((i) => i.aside),
  };
}

/**
 * A dashboard's readiness tabs after its Problems (and, on a semester, Coming up): Suggestions,
 * Set aside, and Setup at the right with a tick once complete. `onCircle` (a viewer who can set
 * aside) makes a suggestion's circle a button; `onBack` gives each set-aside line Bring back.
 */
export function readinessTabs({ items, scope, onCircle, onBack, busy = false }: {
  items: SetupItem[];
  scope: 'course' | 'semester';
  onCircle?: (it: SetupItem) => void;
  onBack?: (id: string) => void;
  busy?: boolean;
}): DashTab[] {
  const g = groups(items);
  const done = g.setup.filter((i) => i.state === 'done').length;
  const course = scope === 'course';
  return [
    {
      key: 'suggestions', label: 'Suggestions', count: g.suggestions.length,
      body: (
        <>
          {g.suggestions.length ? <ul class="setup">{g.suggestions.map((it) => <ItemRow it={it} onCircle={onCircle} />)}</ul> : <p class="footnote">No suggestions open.</p>}
          <p class="footnote">Optional: the {course ? 'course works' : 'semester runs'} without these.{onCircle ? ' If you would rather skip one, click the circle before it to set it aside.' : ''}</p>
        </>
      ),
    },
    {
      key: 'aside', label: 'Set aside', count: g.aside.length,
      body: g.aside.length ? (
        <ul class="aside-list">
          {g.aside.map((r) => (
            <li key={r.id}>
              {r.label}{r.repo ? <> (<span class="slug">{r.repo}</span>)</> : null}
              {onBack ? <> · <button class="textlink" type="button" disabled={busy} aria-label={`Bring back ${r.label}`} onClick={() => onBack(r.id)}>Bring back</button></> : null}
            </li>
          ))}
        </ul>
      ) : <p class="footnote">Nothing set aside. Suggestions you choose to pass move here; bring one back at any time.</p>,
    },
    {
      key: 'setup', label: 'Setup', right: true, count: setupComplete(items) ? 'done' : `${done} of ${g.setup.length}`,
      body: (
        <>
          <ul class="setup">{g.setup.map((it) => <ItemRow it={it} />)}</ul>
          <p class="footnote">One-time setup: what the {course ? 'course needs before a semester can start' : 'semester needs before automation can run'}. Done stays done.</p>
        </>
      ),
    },
  ];
}

/**
 * The verdict line under a page's head: red ! with the problem count, a grey circle with what
 * is missing, or a green tick. With `onOpen` its words are a button to the Problems tab: the
 * dashboard's one problem count (decision 0034, amended).
 */
export function Verdict({ v, onOpen }: { v: VerdictT | null; onOpen?: () => void }) {
  if (!v) return <p class="verdict">Status not computed yet.</p>;
  const cls = v.state === 'fixing' ? 'bad' : v.state === 'ready' ? 'ok' : 'open';
  const words = verdictMain(v);
  return (
    <p class={`verdict ${cls}`}>
      {v.state === 'fixing' ? <span class="ex" aria-hidden="true">!</span> : v.state === 'ready' ? <Check /> : <span class="ex" aria-hidden="true" />}
      {onOpen ? <button type="button" class="verdict-btn" title="Show the problems" onClick={onOpen}>{words}</button> : <span>{words}</span>}
      {verdictTags(v).map((t) => <span class="vtag">{t}</span>)}
    </p>
  );
}

/** A repo's state as chips (decision 0034): Has a problem, Not ready or Ready; on its settings head also a dotted "n suggestions" (a `row` shows the state alone). */
export function RepoChip({ r, row = false }: { r: RepoReadiness; row?: boolean }) {
  return (
    <>
      {r.state === 'problem' ? <span class="chip bad">Has a problem</span> : r.state === 'ready' ? <span class="chip ok">Ready</span> : <span class="chip notready">Not ready</span>}
      {r.suggestions && !row ? <span class="chip suggest">{r.suggestions} suggestion{r.suggestions === 1 ? '' : 's'}</span> : null}
    </>
  );
}

/** The line under a repo that is not ready: its first missing needed item. */
export function RepoWhy({ r }: { r: RepoReadiness }) {
  return r.state === 'not_ready' && r.missing ? <span class="r-sub">{r.missing}</span> : null;
}

