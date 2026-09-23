// S6 Schedule (read) and S11 Release detail.

import { useState } from 'preact/hooks';
import { RELEASE_WORD, TYPE_CLASS, TYPE_LABEL, fmtDay, fmtTime, fmtWhen, releaseIdent, sortKey } from '../model/format';
import { scheduleRows, type Block, type Row } from '../model/schedule';
import type { Release } from '../model/types';
import { Crumbs, EditFile, Help, Lives, Md, ProblemCards, Prop, Soon, ghUrl } from '../ui/bits';
import { readSchedule, tzOf, yearOf } from './Cohort';
import { NotFound } from './Assignments';
import { CheckNow, WithStatus, cohortCrumbs } from './common';
import type { CohortProps, ReadyProps } from './types';

const LABELS: Record<Block, string> = { releases: 'Releases', assignments: 'Assignments', events: 'Events' };

function stateCell(r: Row) {
  if (r.fault && r.block === 'releases') return <><span class="st-chip skip">will be skipped</span><span class="st-note">Fix the folder first</span></>;
  if (r.block === 'releases' && r.state === 'planned') return <><span class="st-chip">planned</span><Soon label="Release early" cls="btn small" /></>;
  return <span class="st-chip">{r.state}</span>;
}

/** What the student site shows in the Details cell. */
function Details({ r }: { r: Row }) {
  if (r.entry === 'term') return <span class="det" />;
  if (!r.show) return <span class="det"><span class="gen">Hidden from the student site; still {r.block === 'releases' ? 'released' : 'runs'}.</span></span>;
  return (
    <span class="det">
      <Md src={r.details} />
      {r.block === 'releases' && r.state !== 'released' ? <p class="gen">Materials for {r.ident} are not yet released.</p> : null}
      {r.block === 'assignments' && r.type === 'due' && !r.details ? <span>Submit via <code>{r.entry}-&lt;your-handle&gt;</code></span> : null}
    </span>
  );
}

function EntryPanel(p: ReadyProps & { row: Row; rows: Row[] }) {
  const { row, status } = p;
  const tz = tzOf(status), year = yearOf(p.now, tz);
  const rel = row.block === 'releases' ? (status.releases ?? []).find((r) => r.id === row.entry) : undefined;
  const problems = (status.problems ?? []).filter((x) => x.fix?.entry === row.entry);
  return (
    <div class="entry" role="dialog" aria-labelledby="entry-title">
      <div class="entry-head">
        <div>
          <div class="eyebrow">{row.block === 'assignments' ? 'Assignment entry' : `${TYPE_LABEL[row.type] ?? row.type}${row.when ? `, ${fmtWhen(row.when, tz, year)}` : ''}`}</div>
          <h2 id="entry-title"><b>{row.ident}</b>: {row.name}</h2>
          {rel ? <div style="margin-top:6px"><span class={`chip ${rel.state === 'will_be_skipped' ? 'bad' : rel.state === 'released' ? 'ok' : ''}`}>{RELEASE_WORD[rel.state]}</span></div> : null}
        </div>
        <a class="x" href="#schedule" aria-label="Close entry" style="text-decoration:none;display:grid;place-items:center">&times;</a>
      </div>
      <div class="entry-body">
        {row.block !== 'events' ? (
          <div class="site-note">On the student site this row shows <b>{row.ident}</b> on one line and “{row.name}” below it, without the colon.</div>
        ) : (
          <div class="site-note">On the student site this row shows only the bold title, <b>{row.name}</b>.</div>
        )}
        {problems.length ? <ProblemCards list={problems} /> : null}
        <div class="form">
          <div class="field"><span class="label">Identifier</span><div class="ident">{row.ident}<span>derived, as the student site does</span></div></div>
          <div class="field"><span class="label">Title</span><div class="readonly">{row.name || 'Untitled'}</div></div>
          <div class="field"><span class="label">When</span><div class="readonly">{row.when ? fmtWhen(row.when, tz, year) : 'TBC'}{row.tbc && row.when ? ' (TBC)' : ''}</div></div>
          <div class="field md-field"><span class="label">Details <span class="default">markdown</span></span><div class="md-prev"><span class="lbl">What students see</span>{row.details ? <Md src={row.details} /> : <span class="empty">Nothing</span>}</div></div>
          {rel ? (
            <div class="field">
              <span class="label">What to release</span>
              <div class="deploy">
                <dl class="kv">
                  <dt>From</dt><dd>{rel.source.repo}/{rel.source.path}</dd>
                  <dt>To</dt><dd>{rel.dest.repo || 'materials'}/{rel.dest.path || rel.source.path}</dd>
                </dl>
              </div>
            </div>
          ) : null}
          {rel ? <a class="textlink" href={`#release-${rel.id}`}>Open release details</a> : null}
          {row.block === 'assignments' ? <a class="textlink" href={`#assignment-${row.entry}`}>Open the assignment</a> : null}
        </div>
      </div>
      <div class="entry-foot">
        <div class="savebar">
          <Soon label="Save" title="Coming in this build: the schedule editor." />
          <EditFile org={p.cohort.org} repo="classroom-config" path="schedule.yml" />
        </div>
      </div>
    </div>
  );
}

function View(p: ReadyProps) {
  const { status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const [filters, setFilters] = useState<Record<Block, boolean>>({ releases: true, assignments: true, events: true });
  const sched = readSchedule(p);
  const rows = scheduleRows(status, sched, now, tz);
  const current = p.entry ? rows.find((r) => r.entry === p.entry) : undefined;
  const counts: Record<Block, number> = { releases: 0, assignments: 0, events: 0 };
  const seen = new Set<string>();
  for (const r of rows) if (r.entry !== 'term' && r.entry !== 'archive' && !seen.has(`${r.block}:${r.entry}`)) { seen.add(`${r.block}:${r.entry}`); counts[r.block]++; }
  const skipped = (status.releases ?? []).filter((r) => r.state === 'will_be_skipped').length;
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  let todayDone = false;
  const items = [];
  for (const r of rows) {
    if (!todayDone && (!r.when || sortKey(r.when, tz) >= nowKey)) {
      todayDone = true;
      items.push(<li class="today-line">Today, {fmtDay(new Date(now).toISOString(), tz, year)}</li>);
    }
    if (!filters[r.block]) continue;
    items.push(
      <li class={`trow ${TYPE_CLASS[r.type] ?? 'evt'}${r.fault && r.block === 'releases' ? ' fault' : ''}${current && current.entry === r.entry ? ' current' : ''}`} data-entry={r.entry}>
        <span class="k">{TYPE_LABEL[r.type] ?? r.type}</span>
        <span class="d">{r.when ? fmtDay(r.when, tz, year) : 'TBC'}{r.when && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}{r.tbc ? ' (TBC)' : ''}</span> : r.tbc && r.when ? <span>(TBC)</span> : null}</span>
        <span class="ttl"><a href={`#schedule-${r.entry}`}><b>{r.ident}</b>: {r.name}</a></span>
        <Details r={r} />
        <span class="st">{stateCell(r)}</span>
      </li>,
    );
  }
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Schedule')} />
      <div class="page-head">
        <div>
          <h1>Schedule</h1>
          <p class="lede">{counts.releases + counts.assignments + counts.events} entries. {skipped ? `${skipped === 1 ? 'One release' : `${skipped} releases`} will be skipped as it stands.` : 'Every release has its folder.'}</p>
        </div>
        <div class="actions"><CheckNow /><Soon label="Add entry" cls="btn outline" /></div>
      </div>
      <Help title="How the schedule works" doc="07-schedule-releases.md">
        <p>The schedule drives everything automatic: releases, hand outs, collection, the student site’s calendar. Dates are in the cohort’s timezone. The Details column shows exactly what students see.</p>
      </Help>
      <p class="site-note" style="margin-bottom:12px">
        Every row here reads <b>Identifier</b>: Name. The student site shows the same two parts on two lines, the bold identifier then the name, with no colon; exams and events show only the bold title. Whether the site should switch to one line is a theme decision.
      </p>
      <div class="term-meta">
        {sched?.start && sched?.end ? <span>Term <b>{fmtDay(sched.start, tz, year)} to {fmtDay(sched.end, tz)}</b></span> : null}
        <span>Timezone <b>{sched?.timezone ?? tz}</b></span>
        <span>Archive <b>{status.cohort?.archive_date ? fmtDay(status.cohort.archive_date, tz, year) : 'never'}</b></span>
      </div>
      {sched === null ? <p class="footnote" style="margin-bottom:12px">Details, events and term dates appear once schedule.yml is read.</p> : null}
      <div class={`sched-layout${current ? '' : ' no-entry'}`}>
        <div>
          <div class="filters" role="group" aria-label="Show">
            <span class="lbl">Show</span>
            {(['releases', 'assignments', 'events'] as Block[]).map((b) => (
              <button type="button" class="toggle" aria-pressed={filters[b]} onClick={() => setFilters({ ...filters, [b]: !filters[b] })}>
                {LABELS[b]} <span class="n">{counts[b]}</span>
              </button>
            ))}
          </div>
          <div style="overflow-x:auto">
            <ul class="timeline wide">
              <li class="th" aria-hidden="true"><span>Type</span><span>Date</span><span>Title</span><span>Details</span><span style="justify-self:end">State</span></li>
              {items}
            </ul>
          </div>
          <details class="fold adv-bottom">
            <summary>Advanced</summary>
            <div class="fold-body">
              <div class="savebar"><span class="footnote">Release a folder that is not in the schedule, for a one-off or a correction.</span><Soon label="Release something unscheduled…" cls="btn small outline" /></div>
              <div class="savebar"><span class="footnote">See what automation’s next scheduled release run would do.</span><Soon label="Preview scheduled releases" cls="btn small outline" /></div>
            </div>
          </details>
          <div style="margin-top:14px"><Lives org={p.cohort.org} repo="classroom-config" path="schedule.yml" /></div>
        </div>
        {current ? <EntryPanel {...p} row={current} rows={rows} /> : null}
      </div>
    </>
  );
}

export function ScheduleScreen(p: CohortProps) {
  return <WithStatus props={p} title="Schedule" crumbs={cohortCrumbs(p, 'Schedule')}>{(r) => <View {...r} />}</WithStatus>;
}

// --------------------------------------------------------------------------- S11

function ReleaseDetail(p: ReadyProps & { rel: Release }) {
  const { status, now, rel } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const all = status.releases ?? [];
  const ident = releaseIdent(rel, all);
  const st = rel.state;
  const problems = (status.problems ?? []).filter((x) => x.fix?.entry === rel.id);
  const last = (status.operations ?? []).find((o) => o.op.startsWith('release.') && o.summary.includes(`${ident}:`));
  const dest = rel.dest.repo || 'materials', destPath = rel.dest.path || rel.source.path;
  return (
    <>
      <Crumbs items={cohortCrumbs(p, ident, [{ t: 'Schedule', href: '#schedule' }])} />
      <div class="page-head">
        <div>
          <h1><b>{ident}</b>: {rel.title}</h1>
          <p class="lede"><span class={`chip ${st === 'will_be_skipped' ? 'bad' : st === 'released' ? 'ok' : ''}`}>{RELEASE_WORD[st]}</span>{fmtWhen(rel.when, tz, year)}</p>
        </div>
        <div class="actions"><a class="btn quiet" href={`#schedule-${rel.id}`}>Edit entry</a></div>
      </div>
      <Help title="Releases" doc="08-release-materials-to-cohort.md">
        <p>
          {st === 'released'
            ? 'Edits students should see: push to the cohort copy, or release again after fixing the course copy. Edits future terms should keep: keep cohort edits for future terms.'
            : st === 'will_be_skipped' ? 'Automation will skip this until the folder exists.'
            : st === 'late' ? 'Reason codes tell you whether the source, the schedule or the scheduler was at fault.'
            : 'Nothing to do; it goes out at the scheduled time. You can release it early.'}
        </p>
      </Help>
      <div class="grid-2">
        <section class="panel section">
          <h2>From and to</h2>
          <dl class="kv">
            <dt>From</dt><dd><a href={ghUrl(p.course.org, rel.source.repo, rel.source.path, 'main').replace('/blob/', '/tree/')} target="_blank" rel="noopener">{p.course.org}/{rel.source.repo}/{rel.source.path}</a></dd>
            <dt>To</dt><dd><a href={ghUrl(p.cohort.org, dest, destPath, 'main').replace('/blob/', '/tree/')} target="_blank" rel="noopener">{p.cohort.org}/{dest}/{destPath}</a></dd>
            <dt>On the student site</dt><dd>{rel.show_on_site ? 'Shown' : 'Hidden'}{rel.tbc ? ', TBC' : ''}</dd>
          </dl>
        </section>
        <section class="panel section">
          <h2>Last outcome</h2>
          {problems.length ? <ProblemCards list={problems} /> : last ? <p class="o-say">{last.summary}</p> : st === 'released' ? <p>Released {fmtWhen(rel.when, tz, year)}.</p> : <p>Not released yet. It goes out {fmtDay(rel.when, tz, year)} at {fmtTime(rel.when, tz)} without you.</p>}
        </section>
      </div>
      <section class="panel section" style="margin-top:20px">
        <h2>Actions</h2>
        <div class="actions">
          {st === 'planned' ? <><Soon label="Preview" /><Prop /><Soon label="Release early" cls="btn outline" /></>
            : st === 'released' ? <><Soon label="Preview" /><Prop /><Soon label="Release again" cls="btn outline" /><Soon label="Keep cohort edits for future terms" cls="btn quiet" /></>
            : st === 'late' ? <><Soon label="Preview" /><Soon label="Release now" cls="btn outline" /></>
            : <a class="btn" href={`#schedule-${rel.id}`}>Fix the folder</a>}
        </div>
        <div class="savebar"><span class="footnote">After changing the course copy, the student site may need an update.</span><a class="btn small quiet" href="#site">Update site on the Site page</a></div>
      </section>
    </>
  );
}

export function ReleaseScreen(p: CohortProps) {
  return (
    <WithStatus props={p} title="Release" crumbs={cohortCrumbs(p, 'Release', [{ t: 'Schedule', href: '#schedule' }])}>
      {(r) => {
        const rel = (r.status.releases ?? []).find((x) => x.id === p.entry);
        return rel ? <ReleaseDetail {...r} rel={rel} /> : <NotFound what={`No release called ${p.entry} in the schedule.`} back="#schedule" />;
      }}
    </WithStatus>
  );
}
