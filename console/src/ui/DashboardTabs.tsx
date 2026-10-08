// The one tabbed panel of a dashboard (decision 0034, amended): the course's and the semester's
// alike, the tab list an input. Each tab carries its count as a badge: red for problems, grey
// otherwise, a green tick in place of a count where there is nothing left (Setup complete, no
// problems). Problems is the default tab; a tab may sit at the right, after a spacer (Setup).

import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { Check } from './icons';

export interface DashTab {
  key: string;
  label: string;
  /** A number (red with `bad`), a short text, or `done` for the green tick. */
  count: number | string;
  bad?: boolean;
  /** After the spacer, at the right. */
  right?: boolean;
  body: ComponentChildren;
}

/** The panel's id: the page head's problem count links here. */
export const DASH_PANEL = 'dash-problems';

/** `selected` / `onSelect` let the page choose the tab (the problem count opens Problems); else the panel keeps its own. */
export function DashboardTabs({ tabs, selected, onSelect }: { tabs: DashTab[]; selected?: string; onSelect?: (key: string) => void }) {
  const [own, setOwn] = useState(tabs[0]?.key ?? '');
  const cur = tabs.some((t) => t.key === (selected ?? own)) ? (selected ?? own) : tabs[0]?.key;
  const pick = (key: string) => (onSelect ? onSelect(key) : setOwn(key));
  const tab = (t: DashTab) => (
    <button type="button" role="tab" id={`dash-tab-${t.key}`} data-key={t.key} aria-selected={t.key === cur} aria-controls={`dash-panel-${t.key}`} onClick={() => pick(t.key)}>
      {t.label}
      {t.count === 'done'
        ? <span class="n ok" aria-label="done"><Check /></span>
        : <span class={`n${t.bad && t.count ? ' bad' : ''}`}>{t.count}</span>}
    </button>
  );
  return (
    <section class="panel section dash-tabs" id={DASH_PANEL} tabIndex={-1}>
      <div class="ptabs" role="tablist">
        {tabs.filter((t) => !t.right).map(tab)}
        <span class="spacer" aria-hidden="true" />
        {tabs.filter((t) => t.right).map(tab)}
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
