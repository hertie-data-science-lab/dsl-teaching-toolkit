// The wizard frame the mockup draws: a progress rail, one step card with its context line,
// a live check list, and the foot with Back and Continue. Steps already done are links; a
// step past the first unfinished one cannot be reached (inputs.md rule 8).

import type { ComponentChildren } from 'preact';
import { Prop } from '../ui/bits';
import { Alert, Check as CheckIcon, Ext } from '../ui/icons';
import { BOT } from './model';
import type { Check, Live } from './verify';

export interface StepDef {
  t: string;
  s: string;
  check?: boolean;
}

export function Rail({ steps, cur, done, heading, base, query = '' }: { steps: StepDef[]; cur: number; done: boolean[]; heading: string; base: string; query?: string }) {
  const first = done.findIndex((d) => !d);
  const reach = first < 0 ? steps.length : first + 1;
  return (
    <div>
      <p class="wrail-h">{heading}</p>
      <ol class={`wrail${steps.length === 3 ? ' n3' : ''}`}>
        {steps.map((s, i) => {
          const k = i + 1;
          const cls = `${done[i] && k !== cur ? 'done' : k === cur ? 'now' : 'todo'}${s.check ? ' check' : ''}`;
          return (
            <li class={cls} aria-current={k === cur ? 'step' : undefined}>
              <span class="wn">{done[i] && k !== cur ? <CheckIcon /> : k}</span>
              <span class="wt">{k !== cur && k <= reach ? <a href={`${query}#${base}${k}`}>{s.t}</a> : s.t}</span>
              <span class="ws">{s.s}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function StepCard({ ctx, of, title, children, back, foot, note }: { ctx?: string; of: string; title: string; children: ComponentChildren; back: ComponentChildren; foot: ComponentChildren; note?: ComponentChildren }) {
  return (
    <section class="wstep">
      <div class="wstep-head">
        {ctx ? <span class="ctx">{ctx}</span> : null}
        <span class="of">{of}</span>
        <h2>{title}</h2>
        {note}
      </div>
      <div class="wstep-body">{children}</div>
      <div class="wstep-foot">
        <span>{back}</span>
        <span class="actions"><span class="footnote">You can leave and come back.</span>{foot}</span>
      </div>
    </section>
  );
}

export function Verified({ children }: { children: ComponentChildren }) {
  return <div class="verified"><CheckIcon /><span>{children}</span></div>;
}

export function WizError({ children }: { children: ComponentChildren }) {
  return <div class="wiz-error"><Alert /><span>{children}</span></div>;
}

/** A live check list: ok, not yet (with what to do), could not tell, or still checking. */
export function Checks({ list, busy, pending }: { list: Check[] | null; busy?: boolean; pending?: string[] }) {
  const items: (Check | { text: string; ok: undefined; hint?: string })[] = list ?? (pending ?? []).map((text) => ({ text, ok: undefined }));
  return (
    <ul class="checks" aria-live="polite">
      {items.map((c) => {
        const st = c.ok === true ? 'ok' : busy ? 'busy' : 'no';
        return (
          <li>
            <span class={`ck ${st}`}>{st === 'ok' ? <CheckIcon /> : null}</span>
            <span>{c.text}{c.ok === null ? ' (could not tell)' : ''}{c.hint && c.ok !== true ? <span class="footnote" style="display:block">{c.hint}</span> : null}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Org step: "Create the org on GitHub, install the app, then I check". */
export function OrgLinks({ org }: { org: string }) {
  return (
    <div class="ext-links">
      <a href="https://github.com/account/organizations/new?plan=free" target="_blank" rel="noopener">
        <span class="n">1</span>Create the org on GitHub <Ext /><span>Use the name above; the free plan is enough</span>
      </a>
      <a href={`https://github.com/orgs/${org}/people`} target="_blank" rel="noopener">
        <span class="n">2</span>Install the console app on it <Ext /><Prop /><span>Today: invite {BOT} as an Owner</span>
      </a>
    </div>
  );
}

export function LiveChecks({ live, pending, again = 'Check again' }: { live: Live<Check[]>; pending: string[]; again?: string }) {
  return (
    <>
      <div class="field"><span class="label">Live check</span><Checks list={live.value} busy={live.busy} pending={pending} /></div>
      <div><button class="btn small outline" type="button" disabled={live.busy} onClick={() => live.run()}>{live.value ? again : 'Check'}</button></div>
    </>
  );
}
