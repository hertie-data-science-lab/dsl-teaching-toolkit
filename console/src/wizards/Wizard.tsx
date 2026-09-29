// The wizard frame the mockup draws: a progress rail, one step card with its context line,
// a live check list, and the foot with Back and Continue. Steps already done are links; a
// step past the first unfinished one cannot be reached (inputs.md rule 8).

import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { Hint } from '../ui/Hint';
import { Alert, Check as CheckIcon, Ext, Fail } from '../ui/icons';
import { rememberInstallReturn } from './drafts';
import { APP_SLUG, BOT, NEW_ORG_URL, installUrl, peopleUrl } from './model';
import type { Check, OrgCheck } from './verify';

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

export function StepCard({ ctx, of, title, children, back, foot, note }: { ctx?: string; of: string; title: ComponentChildren; children: ComponentChildren; back: ComponentChildren; foot: ComponentChildren; note?: ComponentChildren }) {
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
        <span class="actions">{back}<span class="footnote">You can leave and come back.</span></span>
        <span class="actions">{foot}</span>
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

type Item = Check | { text: string; ok: undefined; hint?: string; soft?: undefined };

function state(c: Item, busy?: boolean): string {
  return c.ok === true ? 'ok' : busy ? 'busy' : c.ok === false ? 'no' : c.ok === null ? 'warn' : 'todo';
}

function Tick({ st }: { st: string }) {
  return <span class={`ck ${st}`}>{st === 'ok' ? <CheckIcon /> : st === 'no' ? <Fail /> : null}</span>;
}

/** A live check list: ok, not yet (with what to do), could not tell, or still checking. */
export function Checks({ list, busy, pending }: { list: Check[] | null; busy?: boolean; pending?: string[] }) {
  const items: Item[] = list ?? (pending ?? []).map((text) => ({ text, ok: undefined }));
  return (
    <ul class="checks" aria-live="polite">
      {items.map((c) => (
        <li>
          <Tick st={state(c, busy)} />
          <span>{c.text}{c.ok === null && !c.soft ? ' (could not tell)' : ''}{c.hint && c.ok !== true ? <span class="footnote" style="display:block">{c.hint}</span> : null}</span>
        </li>
      ))}
    </ul>
  );
}

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState<boolean | null>(null);
  const copy = () => navigator.clipboard.writeText(text).then(() => setDone(true), () => setDone(false));
  return (
    <>
      <code>{text}</code>
      <button class="textlink" type="button" aria-label={`Copy ${text}`} onClick={() => void copy()}>{done ? 'Copied' : done === false ? 'Select it to copy' : 'Copy'}</button>
    </>
  );
}

/** The `?` on the org step's title: why these three are the person's. */
export function OrgWhy({ doc }: { doc: string }) {
  return (
    <Hint label="Why these three" doc={doc}>
      GitHub lets only a person do these three things. Everything after them is automatic.
    </Hint>
  );
}

/**
 * The three things only a person can do on GitHub, each with its live tick: create the org,
 * install the console app on it (only when the build names the app), invite the bot as an
 * Owner. The install link opens in this tab, since GitHub sends the person back to the
 * console afterwards; `back` is the step it reopens.
 */
export function OrgSteps({ org, check, busy, run, back, slug = APP_SLUG }: { org: string; check: OrgCheck | null; busy: boolean; run: () => void; back: string; slug?: string }) {
  const exists = check?.checks[0]?.ok === true;
  const id = check?.id ?? null;
  const rows: { label: string; href?: string; here?: boolean; copy?: string; sub?: string }[] = [
    { label: 'Create the org', href: NEW_ORG_URL, copy: org, sub: 'Free plan; choose a business or institution and enter hertie-data-science-lab.' },
    ...(slug ? [{ label: 'Install the console app', href: exists ? installUrl(slug, id) : undefined, here: true }] : []),
    { label: `Invite ${BOT} as an Owner`, href: exists ? peopleUrl(org) : undefined, copy: BOT },
  ];
  return (
    <div class="field">
      <ol class="checks org-steps" aria-live="polite">
        {rows.map((r, i) => {
          const c: Item = check?.checks[i] ?? { text: r.label, ok: undefined };
          const st = state(c, busy);
          const note = c.ok === true ? null : c.soft ? c.text : c.hint ?? (i === 0 ? r.sub : null);
          return (
            <li>
              <Tick st={st} />
              <span>
                {r.href ? (
                  <a href={r.href} {...(r.here ? { onClick: () => rememberInstallReturn(back) } : { target: '_blank', rel: 'noopener' })}>{r.label}{r.here ? null : <> <Ext /></>}</a>
                ) : r.label}
                {r.copy ? <> <Copy text={r.copy} /></> : null}
                {note ? <span class="footnote" style="display:block">{note}</span> : null}
              </span>
            </li>
          );
        })}
      </ol>
      <button class="textlink" type="button" disabled={busy} onClick={run}>Check again</button>
    </div>
  );
}
