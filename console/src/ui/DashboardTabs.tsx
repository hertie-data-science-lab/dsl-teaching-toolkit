// The one tabbed panel of a dashboard (decision 0034, amended): the course's and the semester's
// alike, the tab list an input. Each tab carries its count as a badge: red for problems, grey
// otherwise, a green tick in place of a count where there is nothing left (Setup complete, no
// problems). Problems is the default tab; a tab may sit at the right, after a spacer (Setup).
// What a tab is for sits in a `?` after its label while it is open, so its body starts with the rows.

import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { Hint } from './Hint';
import { Check } from './icons';

export interface DashTab {
  key: string;
  label: string;
  /** A number (red with `bad`), a short text, or `done` for the green tick. */
  count: number | string;
  bad?: boolean;
  /** After the spacer, at the right. */
  right?: boolean;
  /** What the tab is for, in the `?` after its label while it is open. */
  hint?: ComponentChildren;
  body: ComponentChildren;
}

/** The panel's id: the page head's problem count links here. */
export const DASH_PANEL = 'dash-problems';

/** `selected` / `onSelect` let the page choose the tab (the problem count opens Problems); else the panel keeps its own. */
export function DashboardTabs({ tabs, selected, onSelect }: { tabs: DashTab[]; selected?: string; onSelect?: (key: string) => void }) {
  const [own, setOwn] = useState(tabs[0]?.key ?? '');
  const cur = tabs.some((t) => t.key === (selected ?? own)) ? (selected ?? own) : tabs[0]?.key;
  const pick = (key: string) => (onSelect ? onSelect(key) : setOwn(key));
  // The row is the flex box; the tablist inside it lays out as `display: contents`, so the open
  // tab's `?` can sit right after that tab while staying outside the tablist (only tabs belong
  // in one). `order` places it: tabs, spacer and `?` in visual slots 2v, the `?` at 2v + 1.
  const left = tabs.filter((t) => !t.right);
  const right = tabs.filter((t) => t.right);
  const slot = (t: DashTab) => 2 * (t.right ? left.length + 1 + right.indexOf(t) : left.indexOf(t));
  const open = tabs.find((t) => t.key === cur);
  const tab = (t: DashTab) => (
    <button key={t.key} type="button" role="tab" id={`dash-tab-${t.key}`} data-key={t.key} aria-selected={t.key === cur} aria-controls={`dash-panel-${t.key}`} style={{ order: slot(t) }} onClick={() => pick(t.key)}>
      {t.label}
      {t.count === 'done'
        ? <span class="n ok" aria-label="done"><Check /></span>
        : <span class={`n${t.bad && t.count ? ' bad' : ''}`}>{t.count}</span>}
    </button>
  );
  return (
    <section class="panel section dash-tabs" id={DASH_PANEL} tabIndex={-1}>
      <div class="ptabs">
        <div class="ptabs-list" role="tablist">
          {left.map(tab)}
          <span class="spacer" aria-hidden="true" style={{ order: 2 * left.length }} />
          {right.map(tab)}
        </div>
        {open?.hint ? <span class="ptabs-hint" style={{ order: slot(open) + 1 }}><Hint label={`About ${open.label}`}>{open.hint}</Hint></span> : null}
      </div>
      {/* Only the current tab's body renders; the others stay as empty panels for `aria-controls`. */}
      {tabs.map((t) => (
        <div class={`ptab ptab-${t.key}`} role="tabpanel" id={`dash-panel-${t.key}`} aria-labelledby={`dash-tab-${t.key}`} hidden={t.key !== cur}>
          {t.key === cur ? t.body : null}
        </div>
      ))}
    </section>
  );
}

/** Open a dashboard's tab and move to the panel (the verdict line's link to Problems). */
export function showTab(set: (key: string) => void, key: string) {
  set(key);
  const el = document.getElementById(DASH_PANEL);
  el?.focus();
  el?.scrollIntoView?.({ block: 'start' });
}
