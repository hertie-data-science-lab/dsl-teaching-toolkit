// S15 Archive: what archiving does, when it is scheduled, Preview and Archive.

import { fmtDay } from '../model/format';
import { archive } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { Crumbs, Help } from '../ui/bits';
import { Check } from '../ui/icons';
import { WithStatus, cohortCrumbs, cohortScope, todayOf, tzOf, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';

function Archive(p: ReadyProps) {
  const { status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz), today = todayOf(now, tz);
  const date = status.semester?.archive_date ?? null;
  const when = date ? fmtDay(date, tz, year) : null;
  const passed = !!date && date.slice(0, 10) <= today;
  const archived = status.semester?.live === false;
  const t = p.cohort.termLabel;
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Archive')} />
      <div class="page-head">
        <div><h1>Archive {t}</h1><p class="lede">{archived ? 'Archived: every repo is read-only.' : `Scheduled for ${when ?? 'never'}.`}</p></div>
        <div class="actions">{archived ? null : <OpButtons def={archive(cohortScope(p), when, passed)} />}</div>
      </div>
      <Help title="What archiving does" doc="10-grade-and-return-assignments.md">
        <p>Archiving freezes every repo read-only. Students keep access. Nothing is deleted.</p>
      </Help>
      <div class="grid-2">
        <section class="panel section">
          <h2>What happens</h2>
          <ul class="checks">
            <li><span class="ck ok"><Check /></span><span>Every repo in {p.cohort.org} becomes read-only: student repos, marks repos, the student site, the join form, the settings repo.</span></li>
            <li><span class="ck ok"><Check /></span><span>Students and instructors keep read access.</span></li>
            <li><span class="ck ok"><Check /></span><span>Nothing is deleted. The student site stays online.</span></li>
          </ul>
        </section>
        <section class="panel section">
          <h2>When</h2>
          <dl class="kv"><dt>Scheduled</dt><dd>{when ?? 'Never'}, from the schedule</dd><dt>Students told</dt><dd>An Updates notice two weeks before</dd></dl>
          <a class="textlink" href="#schedule-archive">Change the date in the schedule</a>
          {!passed && !archived ? <p class="footnote">Archiving before the scheduled date asks you to confirm in the panel.</p> : null}
        </section>
      </div>
    </>
  );
}

export function ArchiveScreen(p: CohortProps) {
  return <WithStatus props={p} title={`Archive ${p.cohort.termLabel}`} crumbs={cohortCrumbs(p, 'Archive')}>{(r) => <Archive {...r} />}</WithStatus>;
}
