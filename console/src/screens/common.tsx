import type { ComponentChildren, VNode } from 'preact';
import { useState } from 'preact/hooks';
import { CheckLine, Crumbs, Loading, Prop, Soon } from '../ui/bits';
import type { CohortProps, ReadyProps } from './types';

export const cohortName = (p: Pick<CohortProps, 'course' | 'cohort'>) => `${p.course.name}, ${p.cohort.termLabel}`;

export const CHECK_NOW_SOON = 'Coming in this build: Check now refreshes the status and re-runs every check.';

export function CheckNow({ small }: { small?: boolean }) {
  return <Soon label="Check now" cls={small ? 'btn small' : 'btn'} title={CHECK_NOW_SOON} />;
}

/** The cohort header's overflow menu (revision brief section 9). Its items arrive with the operation panel. */
export function MoreMenu() {
  const [open, setOpen] = useState(false);
  return (
    <div class="menu-wrap">
      <button class="btn outline" type="button" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>More</button>
      <div class="popmenu" hidden={!open} role="menu">
        <button type="button" role="menuitem" disabled title="Coming in this build">Preview the next automatic run <Prop /></button>
        <button type="button" role="menuitem" disabled title="Coming in this build">Keep cohort edits for future terms</button>
        <button type="button" role="menuitem" disabled title="Coming in this build">Export <span class="pm-sub">roster, marks, schedule</span></button>
        <a href="#archive" role="menuitem">Archive this cohort</a>
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
      <b>Not yet checked.</b> {files} changed since this cohort was last checked, so what you see may be out of date. Check now brings it up to date.
    </p>
  );
}

/** The page every cohort screen shows before an org has been refreshed on the new engine. */
export function NotComputed({ title, crumbs }: { title: string; crumbs: { t: string; href?: string }[] }) {
  return (
    <>
      <Crumbs items={crumbs} />
      <div class="page-head">
        <div>
          <h1>{title}</h1>
          <p class="lede">Status not computed yet.</p>
        </div>
        <div class="actions"><CheckNow /></div>
      </div>
      <section class="panel section stub">
        <h2>Status not computed yet</h2>
        <p>This cohort has not been checked since it moved to the console's engine, so there is no status to show: no problems list, no term strip, no counts.</p>
        <p>Check now computes it. Automation also computes it at the next nightly refresh.</p>
      </section>
    </>
  );
}

/**
 * Render a cohort screen once its status is ready, or the loading, absent, invalid and error
 * states every cohort screen shares.
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
  if (l.kind === 'loading') return <Loading what="Reading the cohort's status" />;
  if (l.kind === 'absent') return <NotComputed title={title} crumbs={crumbs} />;
  if (l.kind === 'invalid' || l.kind === 'error')
    return (
      <>
        <Crumbs items={crumbs} />
        <div class="page-head"><div><h1>{title}</h1></div><div class="actions"><CheckNow /></div></div>
        <section class="panel section">
          <CheckLine cls="bad">
            {l.kind === 'invalid' ? `The cohort's status file does not match the expected shape: ${l.errors.slice(0, 3).join('; ')}.` : `Could not read the cohort's status: ${l.message}`}
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
    { t: cohortName(p), href: page ? '#cohort' : undefined },
    ...(mid ?? []),
    ...(page ? [{ t: page }] : []),
  ];
}
