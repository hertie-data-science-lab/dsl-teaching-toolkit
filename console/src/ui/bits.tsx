// The mockup's shared pieces: crumbs, problem cards, footnotes.

import type { ComponentChildren } from 'preact';
import { encPath } from '../github/client';
import { PROBLEM_AREA, md, opLabel, ago } from '../model/format';
import type { ReleaseMark } from '../model/readiness';
import type { Operation, Outcome, Problem } from '../model/types';
import { Alert, Check, Eye, Ext, Fail, Skip } from './icons';

export const DOCS = 'https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/blob/main/docs/';
export const SOON = 'Coming in this build';

export { encPath };

/**
 * The GitHub page of an org, a repo, or a file (`blob`) or folder (`tree`) in it. The one rule
 * for the branch: `main` for the repos the toolkit creates; `HEAD` (the default branch, whatever
 * it is called) for a repo it did not.
 */
export function ghUrl(org: string, repo?: string, path?: string, branch = 'main', view: 'blob' | 'tree' = 'blob'): string {
  return `https://github.com/${org}${repo ? `/${repo}` : ''}${repo && path ? `/${view}/${branch}/${encPath(path)}` : ''}`;
}

/** GitHub's in-browser editor for one file, optionally at a line. */
export function editUrl(org: string, repo: string, path: string, branch = 'main', line?: number): string {
  return `https://github.com/${org}/${repo}/edit/${branch}/${encPath(path)}${line ? `#L${line}` : ''}`;
}

/** GitHub's new-file page with the name filled in: where an edit link goes while the file does not exist yet. */
export function newFileUrl(org: string, repo: string, path: string, branch = 'main'): string {
  return `https://github.com/${org}/${repo}/new/${branch}?filename=${encodeURIComponent(path)}`;
}

/** GitHub's upload page for a repo's branch. */
export function uploadUrl(org: string, repo: string, branch = 'main'): string {
  return `https://github.com/${org}/${repo}/upload/${branch}`;
}

/** One workflow run of `repo` (`owner/name`). */
export function runUrl(repo: string, runId: number): string {
  return `https://github.com/${repo}/actions/runs/${runId}`;
}

/** The breadcrumbs: each a link to its home, plain for the open page; "›" between. */
export function Crumbs({ items }: { items: { t: string; href?: string }[] }) {
  return (
    <div class="crumbs">
      {items.map((c, i) => [
        i ? <span aria-hidden="true">›</span> : null,
        c.href ? <a href={c.href}>{c.t}</a> : <span>{c.t}</span>,
      ])}
    </div>
  );
}

/** A problem count (now or soon, `problemCount`): Home's cards and the course's Semesters rows. */
export function Probs({ n }: { n: number }) {
  if (!n) return <span class="probs none">No problems</span>;
  return <span class="probs"><span class="count-badge">{n}</span>{n === 1 ? '1 problem' : `${n} problems`}</span>;
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

/** "Edit the file directly"; while the file does not exist (`exists` false), GitHub's new-file page instead. */
export function EditFile({ org, repo, path, branch = 'main', line, exists = true }: { org: string; repo: string; path: string; branch?: string; line?: number; exists?: boolean }) {
  return (
    <a class="edit-file" href={exists ? editUrl(org, repo, path, branch, line) : newFileUrl(org, repo, path, branch)} target="_blank" rel="noopener">
      {exists ? 'Edit the file directly' : 'Create the file on GitHub'} <Ext />
    </a>
  );
}

export function Lives({ org, repo, path, branch = 'main', exists = true }: { org: string; repo: string; path?: string; branch?: string; exists?: boolean }) {
  const where = `${org}/${repo}${path ? `/${path}` : ''}`;
  return (
    <p class="lives">
      {path && !exists ? (
        <a href={newFileUrl(org, repo, path, branch)} target="_blank" rel="noopener">Create {where} on GitHub</a>
      ) : (
        <a href={ghUrl(org, repo, path, branch)} target="_blank" rel="noopener">Lives in {where}</a>
      )}
    </p>
  );
}

export function Md({ src, class: cls }: { src: string | null | undefined; class?: string }) {
  return <div class={cls} dangerouslySetInnerHTML={{ __html: md(src) }} />;
}

export function CheckLine({ cls, children }: { cls: 'ok' | 'bad' | 'busy' | 'warn'; children: ComponentChildren }) {
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

/**
 * A release row's mark (decision 0034 §6): now or soon, a red `!`, the red time word and Fix;
 * later, a dotted "not ready yet" and the same Fix; late, a red `!` and "late" (the caller links
 * its Details). Fix goes where the problem card's does.
 */
export function ReleaseMarks({ m, entry }: { m: ReleaseMark; entry: string }) {
  const red = m.bites !== 'later';
  const href = (m.problem && fixHref(m.problem)) || `#schedule-${entry}`;
  return (
    <>
      {red ? <span class="ex" aria-hidden="true">!</span> : null}
      <span class={`st-chip ${red ? 'skip' : 'later'}`}>{m.word}</span>
      {m.late ? null : <a class={red ? 'btn small' : 'textlink'} href={href}>Fix</a>}
    </>
  );
}

/** The problem cards. `cohort` marks the course's faults "(course)" on a semester's page. */
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
              {p.fix && org && repo ? <EditFile org={org} repo={repo} path={p.fix.path} branch={p.fix.ref ?? (p.fix.screen === 'template' ? 'solution' : 'main')} line={p.fix.line ?? undefined} /> : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

const TONE: Record<string, string> = { done: 'ok', failed: 'fail', previewed: 'dry', skipped: 'skip', nothing_to_do: 'skip' };

/** An operation's mark: done, failed, previewed, or skipped (nothing to do). */
export function OpMark({ conclusion }: { conclusion: string }) {
  const tone = TONE[conclusion] ?? 'skip';
  return <span class={`mark ${tone}`}>{tone === 'ok' ? <Check /> : tone === 'fail' ? <Fail /> : tone === 'dry' ? <Eye /> : <Skip />}</span>;
}

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
        const oc = outcomes[o.op];
        const detail = oc && oc.run_id === o.run_id ? oc : undefined;
        return (
          <li key={o.run_id}>
            <OpMark conclusion={o.conclusion} />
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
                    <a href={runUrl(runRepo, o.run_id)} target="_blank" rel="noopener">Open run</a>
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
