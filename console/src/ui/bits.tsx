// The mockup's shared pieces: help, crumbs, problem cards, the stage rail, footnotes.

import type { ComponentChildren } from 'preact';
import { COHORT_STAGES, COURSE_STAGES, PROBLEM_AREA, STAGE_WORD, md, opLabel, ago } from '../model/format';
import type { Operation, Outcome, Problem, StageState } from '../model/types';
import { Alert, Check, Eye, Ext, Fail, Skip } from './icons';

export const DOCS = 'https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/blob/main/docs/';
export const SOON = 'Coming in this build';

export function ghUrl(org: string, repo?: string, path?: string, branch = 'main'): string {
  return `https://github.com/${org}${repo ? `/${repo}` : ''}${repo && path ? `/blob/${branch}/${path}` : ''}`;
}

export function Help({ title, doc, children }: { title: string; doc?: string; children: ComponentChildren }) {
  return (
    <details class="help">
      <summary>{title}</summary>
      <div class="help-body">
        {children}
        <p>
          <a href={doc ? `${DOCS}${doc}` : DOCS} target="_blank" rel="noopener">Learn more</a>
        </p>
      </div>
    </details>
  );
}

export function Crumbs({ items }: { items: { t: string; href?: string }[] }) {
  return (
    <div class="crumbs">
      {items.map((c, i) => [
        i ? <span aria-hidden="true">/</span> : null,
        c.href ? <a href={c.href}>{c.t}</a> : <span>{c.t}</span>,
      ])}
    </div>
  );
}

export function Probs({ n }: { n: number }) {
  return n ? (
    <span class="probs"><span class="count-badge">{n}</span>{n === 1 ? '1 problem' : `${n} problems`}</span>
  ) : (
    <span class="probs none"><span class="count-badge zero">0</span>No problems</span>
  );
}

/** A disabled button for an action another work package builds, with the reason on hover. */
export function Soon({ label, cls = 'btn', title = SOON }: { label: ComponentChildren; cls?: string; title?: string }) {
  return (
    <span class="soon" title={title}>
      <button class={cls} type="button" disabled aria-disabled="true">{label}</button>
    </span>
  );
}

export const Prop = () => <span class="prop" title="Not in the engine today">proposed</span>;

export function EditFile({ org, repo, path, branch = 'main', line }: { org: string; repo: string; path: string; branch?: string; line?: number }) {
  return (
    <a class="edit-file" href={`https://github.com/${org}/${repo}/edit/${branch}/${path}${line ? `#L${line}` : ''}`} target="_blank" rel="noopener">
      Edit the file <Ext />
    </a>
  );
}

export function Lives({ org, repo, path, branch = 'main' }: { org: string; repo: string; path?: string; branch?: string }) {
  return (
    <p class="lives">
      <a href={ghUrl(org, repo, path, branch)} target="_blank" rel="noopener">Lives in {`${org}/${repo}${path ? `/${path}` : ''}`}</a>
    </p>
  );
}

export function Md({ src, class: cls }: { src: string | null | undefined; class?: string }) {
  return <div class={cls} dangerouslySetInnerHTML={{ __html: md(src) }} />;
}

export function CheckLine({ cls, children }: { cls: 'ok' | 'bad' | 'busy'; children: ComponentChildren }) {
  return (
    <div class={`check-line ${cls}`}>
      {cls === 'busy' ? <span class="spin" /> : cls === 'ok' ? <Check /> : <Alert />}
      <span>{children}</span>
    </div>
  );
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <div class="loading"><span class="spin" />{what}…</div>;
}

/** "schedule.yml, line 41" */
function whereOf(p: Problem): string {
  if (!p.fix) return '';
  const file = p.fix.path.split('/').pop();
  return p.fix.line ? `${file}, line ${p.fix.line}` : (file ?? '');
}

export function fixHref(p: Problem): string | null {
  const f = p.fix;
  if (!f?.screen) return null;
  return `#${f.screen}${f.entry ? `-${f.entry}` : ''}`;
}

export function ProblemCards({ list, cohort }: { list: Problem[]; cohort?: boolean }) {
  if (!list.length)
    return (
      <div class="no-problems"><Check /><span>No problems. Everything automatic will happen on time.</span></div>
    );
  return (
    <ul class="problems">
      {list.map((p) => {
        const href = fixHref(p);
        const [org, repo] = (p.fix?.repo ?? '').split('/');
        return (
          <li class="problem" key={p.id}>
            <div class="p-where">
              <b>{PROBLEM_AREA[p.stage] ?? p.stage}</b>
              <span>{whereOf(p)}</span>
              {cohort && p.scope === 'course' ? <span>(course)</span> : null}
            </div>
            <p class="p-say">{p.text}</p>
            <p class="p-effect">{p.stops}</p>
            <div class="p-fix">
              {href ? <a class="btn small" href={href}>Fix</a> : null}
              {p.fix && org && repo ? <EditFile org={org} repo={repo} path={p.fix.path} branch={p.fix.ref ?? (p.fix.screen === 'template' ? 'solution' : 'main')} line={p.fix.line} /> : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function Legend() {
  return (
    <div class="legend">
      {(['done', 'problem', 'blocked', 'todo'] as StageState[]).map((s) => (
        <span><span class={`seg ${s}`} />{STAGE_WORD[s]}</span>
      ))}
    </div>
  );
}

export function Rail({
  scope,
  stages,
  problems,
  acts = {},
}: {
  scope: 'cohort' | 'course';
  stages: Record<string, StageState>;
  problems: Problem[];
  acts?: Record<string, ComponentChildren>;
}) {
  const names = scope === 'cohort' ? COHORT_STAGES : COURSE_STAGES;
  return (
    <ol class="rail" style={`--n:${names.length}`}>
      {names.map(([id, name]) => {
        const st = stages[id] ?? 'todo';
        const prob = problems.find((p) => p.stage === id);
        return (
          <li class={st}>
            <span class={`seg ${st}`} aria-hidden="true" />
            <span class="r-name">{name}</span>
            <span class="r-state">
              <span class="sr">{STAGE_WORD[st]}: </span>
              {st === 'problem' && prob ? prob.text : STAGE_WORD[st]}
            </span>
            {acts[id] ? <span class="r-act">{acts[id]}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

const TONE: Record<string, string> = { done: 'ok', failed: 'fail', previewed: 'dry', skipped: 'skip', nothing_to_do: 'skip' };

export function OpsList({
  list,
  now,
  full,
  runRepo,
  outcomes = {},
}: {
  list: Operation[];
  now: number;
  full?: boolean;
  runRepo: string; // "<course-org>/.github", where the Console workflow runs
  outcomes?: Record<string, Outcome | undefined>;
}) {
  if (!list.length) return <p class="footnote">No operations yet.</p>;
  return (
    <ul class="ops">
      {list.map((o) => {
        const tone = TONE[o.conclusion] ?? 'skip';
        const oc = outcomes[o.op];
        const detail = oc && oc.run_id === o.run_id ? oc : undefined;
        return (
          <li key={o.run_id}>
            <span class={`mark ${tone}`}>{tone === 'ok' ? <Check /> : tone === 'fail' ? <Fail /> : tone === 'dry' ? <Eye /> : <Skip />}</span>
            <div class="o-head">
              <span><b>{opLabel(o.op)}</b>{o.conclusion === 'failed' ? <span class="failed">FAILED</span> : null}</span>
              <span>{ago(o.finished, now)}</span>
            </div>
            <p class="o-say">{o.summary}</p>
            {full ? (
              <details class="fold o-more">
                <summary>Details</summary>
                <div class="fold-body reasons">
                  {detail?.counts && Object.keys(detail.counts).length ? (
                    <div class="outcome-counts">
                      {Object.entries(detail.counts).map(([k, v]) => <div><div class="n">{v}</div><div class="l">{k}</div></div>)}
                    </div>
                  ) : null}
                  {detail?.reasons?.length ? (
                    <table><tbody>{detail.reasons.map((r) => <tr><td><code>{r.code}</code></td><td>{r.text}</td></tr>)}</tbody></table>
                  ) : null}
                  <p class="footnote">
                    Ran in {runRepo} as run #{o.run_id}.{' '}
                    <a href={`https://github.com/${runRepo}/actions/runs/${o.run_id}`} target="_blank" rel="noopener">Open run</a>
                  </p>
                </div>
              </details>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
