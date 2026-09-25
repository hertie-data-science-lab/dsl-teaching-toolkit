import type { ComponentChildren, VNode } from 'preact';
import { useMemo, useState } from 'preact/hooks';
import { parse } from 'yaml';
import { useEnv } from '../env';
import { cohortName } from '../model/discovery';
import { dayKey, zoned } from '../model/format';
import { CONFIG_REPO } from '../model/names';
import type { Operation, Status } from '../model/types';
import { checkNow, keepFuture, previewNext, type Scope } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { mergeOperations, type OpDef } from '../ops/session';
import { CheckLine, Crumbs, Loading, Soon } from '../ui/bits';
import { Ext } from '../ui/icons';
import type { CohortProps, ReadyProps } from './types';

export { cohortName };

export const CHECK_NOW_SOON = 'Sign in to check now.';

export const tzOf = (s: Status) => s.semester?.timezone ?? 'Europe/Berlin';
export const yearOf = (now: number, tz: string) => zoned(new Date(now).toISOString(), tz).y;
export const todayOf = (now: number, tz: string) => dayKey(new Date(now).toISOString(), tz);

const configText = (p: ReadyProps, template: string): string | null => {
  const f = p.files.file(p.course.org, template, 'grading_config.yml', 'solution');
  return f.kind === 'ready' ? f.text : null;
};

function parseConfig(text: string | null): Record<string, unknown> {
  if (text === null) return {};
  try {
    const d = parse(text);
    return d && typeof d === 'object' ? (d as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** A template's grading_config.yml as parsed, or {} while it is missing or unreadable. */
export function gradingConfig(p: ReadyProps, template: string): Record<string, unknown> {
  return parseConfig(configText(p, template));
}

/** gradingConfig for a component: parsed again only when the file's text changes. */
export function useGradingConfig(p: ReadyProps, template: string): Record<string, unknown> {
  const text = configText(p, template);
  return useMemo(() => parseConfig(text), [text]);
}

/** Who an operation on this semester acts for. */
export function cohortScope(p: Pick<CohortProps, 'course' | 'cohort'>): Scope {
  return { courseOrg: p.course.org, cohortOrg: p.cohort.org, where: p.cohort.termLabel };
}

/** Check now: refresh the status and re-run every check (semester.check). */
export function CheckNow({ small, p, label }: { small?: boolean; p?: Pick<CohortProps, 'course' | 'cohort'>; label?: string }) {
  if (!p) return <Soon label="Check now" cls={small ? 'btn small' : 'btn'} title={CHECK_NOW_SOON} />;
  return <OpButtons def={checkNow(cohortScope(p))} small={small} label={label} />;
}

/** The operations of this semester: the status file's record plus this session's runs. */
export function useOperations(fromStatus: Operation[] | undefined, cohortOrg: string): Operation[] {
  const env = useEnv();
  const mine = (env?.ops.runs.value ?? []).filter((r) => r.cohort === cohortOrg);
  return mergeOperations(fromStatus ?? [], mine);
}

function ExportInfo({ org }: { org: string }) {
  const f = (path: string) => `https://github.com/${org}/${CONFIG_REPO}/${path.includes('.') ? 'blob' : 'tree'}/main/${path}`;
  const row = (t: string, sub: string, path: string) => (
    <li><span class="r-title">{t}</span><span class="r-sub">{sub}</span><span class="r-side"><a class="btn small quiet" href={f(path)} target="_blank" rel="noopener">Open <Ext /></a></span></li>
  );
  return (
    <>
      <ul class="rows">
        {row('Roster', 'students.csv', 'students.csv')}
        {row('Marks for the registrar', 'Written each time marks are returned', 'registrar')}
        {row('Schedule', 'schedule.yml', 'schedule.yml')}
      </ul>
      <p class="footnote">Each opens on GitHub, where you can download it.</p>
    </>
  );
}

/** The semester header's overflow menu (revision brief section 9). */
export function MoreMenu({ p }: { p?: Pick<CohortProps, 'course' | 'cohort'> }) {
  const [open, setOpen] = useState(false);
  const env = useEnv();
  const scope = p ? cohortScope(p) : null;
  const go = (def: OpDef | null) => {
    setOpen(false);
    if (def) env?.ops.open(def);
  };
  const exportDef = scope && p ? { ...checkNow(scope), key: 'export', name: 'Export', title: 'Download a copy', intro: '', info: <ExportInfo org={p.cohort.org} /> } : null;
  return (
    <div class="menu-wrap">
      <button class="btn outline" type="button" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>More</button>
      <div class="popmenu" hidden={!open} role="menu">
        <button type="button" role="menuitem" disabled={!scope} onClick={() => go(scope && previewNext(scope))}>Preview the next automatic run</button>
        <button type="button" role="menuitem" disabled={!scope} onClick={() => go(scope && keepFuture(scope))}>Keep for future semesters</button>
        <button type="button" role="menuitem" disabled={!scope} onClick={() => go(exportDef)}>Export <span class="pm-sub">roster, marks, schedule</span></button>
        <a href="#archive" role="menuitem" onClick={() => setOpen(false)}>Archive this semester</a>
      </div>
    </div>
  );
}

/** A status older than the files it was computed from. */
export function StaleNote({ stale }: { stale: string[] }) {
  if (!stale.length) return null;
  const files = stale.map((s) => s.replace(/^course\//, '')).join(', ');
  return (
    <p class="note" style="margin-bottom:18px">
      <b>Not yet checked.</b> {files} changed since this semester was last checked, so what you see may be out of date. Check now brings it up to date.
    </p>
  );
}

/** The page every semester screen shows before an org has been refreshed on the new engine. */
export function NotComputed({ title, crumbs, p }: { title: string; crumbs: { t: string; href?: string }[]; p?: Pick<CohortProps, 'course' | 'cohort'> }) {
  return (
    <>
      <Crumbs items={crumbs} />
      <div class="page-head">
        <div>
          <h1>{title}</h1>
          <p class="lede">Status not computed yet.</p>
        </div>
        <div class="actions"><CheckNow p={p} /></div>
      </div>
      <section class="panel section stub">
        <h2>Status not computed yet</h2>
        <p>This semester has not been checked since it moved to the console's engine, so there is no status to show: no problems list, no semester strip, no counts.</p>
        <p>Check now computes it. Automation also computes it at the next nightly refresh.</p>
      </section>
    </>
  );
}

/**
 * Render a semester screen once its status is ready, or the loading, absent, invalid and error
 * states every semester screen shares.
 */
export function WithStatus({
  props,
  title,
  crumbs,
  children,
}: {
  props: CohortProps;
  title: string;
  crumbs: { t: string; href?: string }[];
  children: (p: ReadyProps) => VNode | ComponentChildren;
}) {
  const l = props.loaded;
  if (l.kind === 'loading') return <Loading what="Reading the semester's status" />;
  if (l.kind === 'absent') return <NotComputed title={title} crumbs={crumbs} p={props} />;
  if (l.kind === 'invalid' || l.kind === 'error')
    return (
      <>
        <Crumbs items={crumbs} />
        <div class="page-head"><div><h1>{title}</h1></div><div class="actions"><CheckNow p={props} /></div></div>
        <section class="panel section">
          <CheckLine cls="bad">
            {l.kind === 'invalid' ? `The semester's status file does not match the expected shape: ${l.errors.slice(0, 3).join('; ')}.` : `Could not read the semester's status: ${l.message}`}
          </CheckLine>
        </section>
      </>
    );
  return (
    <>
      <StaleNote stale={l.stale} />
      {children({ ...props, status: l.status })}
    </>
  );
}

export function cohortCrumbs(p: Pick<CohortProps, 'course' | 'cohort'>, page?: string, mid?: { t: string; href: string }[]) {
  return [
    { t: cohortName(p), href: page ? '#semester' : undefined },
    ...(mid ?? []),
    ...(page ? [{ t: page }] : []),
  ];
}
