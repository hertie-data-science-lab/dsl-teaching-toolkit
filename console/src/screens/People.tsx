// S8 Students (roster) and S7 Staff, read-only.

import { fmtShort } from '../model/format';
import { parsePeople, parseRoster, type Person, type RosterRow } from '../model/people';
import { CheckLine, Crumbs, EditFile, Help, Lives, Loading, ProblemCards, Prop, Soon } from '../ui/bits';
import { Lock } from '../ui/icons';
import { StudentCounts } from './Cohort';
import { WithStatus, cohortCrumbs } from './common';
import type { CohortProps, ReadyProps } from './types';

function RowStatus({ r }: { r: RosterRow }) {
  if (!r.sent) return <span class="notsent">Not sent</span>;
  if (r.handle) return <span class="joined"><span class="dot ok" aria-hidden="true" />Joined</span>;
  return <span class="joined"><span class="dot idle" aria-hidden="true" />Code sent; not joined</span>;
}

function Students(p: ReadyProps) {
  const { status } = p;
  const s = status.students ?? { rows: 0, codes_sent: 0, joined: 0 };
  const file = p.files.file(p.cohort.org, 'classroom-config', 'students.csv');
  const parsed = file.kind === 'ready' ? parseRoster(file.text) : null;
  const faultLines = new Set((status.problems ?? []).filter((x) => x.stage === 'K5' && x.fix?.line).map((x) => x.fix!.line!));
  const problems = (status.problems ?? []).filter((x) => x.stage === 'K5');
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Students')} />
      <div class="page-head">
        <div><h1>Students</h1><p class="lede">{s.rows} on the roster. {s.codes_sent} codes sent; {s.joined} joined.</p></div>
        <div class="actions">
          <Soon label="Replace from CSV" cls="btn quiet" />
          <Soon label="Send new codes to students who have not joined" cls="btn outline" />
          <Prop />
        </div>
      </div>
      <Help title="How joining works" doc="06-enrol-students-to-cohort.md">
        <p>Adding a row emails that student a code. They redeem it on the cohort’s join form; you see them turn green here.</p>
        <p>You edit email, name and role. The system columns are written when a student joins; the code itself is never shown.</p>
      </Help>
      <div class="stack">
        <div class="panel"><StudentCounts status={status} /></div>
        {problems.length ? <ProblemCards list={problems} /> : null}
        {file.kind === 'loading' ? <Loading what="Reading the roster" /> : null}
        {file.kind === 'absent' ? <p class="footnote">There is no students.csv yet.</p> : null}
        {file.kind === 'error' ? <CheckLine cls="bad">Could not read students.csv: {file.message}</CheckLine> : null}
        {parsed?.error ? <CheckLine cls="bad">{parsed.error}</CheckLine> : null}
        {parsed && !parsed.error ? (
          <div>
            <div class="table-wrap roster-table" style="max-height:600px;overflow:auto">
              <table class="grid" style="min-width:980px">
                <thead>
                  <tr>
                    <th>Line</th><th>Email<span class="grp">yours: hertie_email</span></th><th>Name<span class="grp">yours</span></th><th>Role<span class="grp">yours</span></th>
                    <th class="sys">GitHub handle<span class="grp">system <Lock /></span></th><th class="sys">GitHub id<span class="grp">system</span></th>
                    <th class="sys">Code sent<span class="grp">system</span></th><th class="sys">Status<span class="grp">system</span></th>
                  </tr>
                </thead>
                <tbody>
                  {parsed.rows.map((r) => (
                    <tr class={faultLines.has(r.line) ? 'fault' : ''} id={`line-${r.line}`}>
                      <td class="line">{r.line}</td><td>{r.email}</td><td>{r.name}</td><td>{r.role}</td>
                      <td class="sys mono">{r.handle || <span style="color:var(--muted)">not yet</span>}</td>
                      <td class="sys mono">{r.id}</td><td class="sys">{r.sent.replace('T', ', ').replace(/:\d\d(\.\d+)?Z?$/, '')}</td>
                      <td class="sys"><RowStatus r={r} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul class="roster-cards">
              {parsed.rows.map((r) => (
                <li class={`rcard${faultLines.has(r.line) ? ' fault' : ''}`}>
                  <div class="rc-top">{r.name}<span>line {r.line}</span></div>
                  <div class="field"><span class="label">Email</span><div class="readonly">{r.email}</div></div>
                  <div class="field"><span class="label">Role</span><div class="readonly">{r.role}</div></div>
                  <div class="rc-status"><RowStatus r={r} />{r.handle ? ` as ${r.handle}` : ''}</div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div class="panel" style="display:grid;gap:10px">
          <div class="savebar">
            <span class="footnote">Editing the roster here comes later in this build.</span>
            <Soon label="Save" />
            <EditFile org={p.cohort.org} repo="classroom-config" path="students.csv" />
          </div>
          <Lives org={p.cohort.org} repo="classroom-config" path="students.csv" />
        </div>
      </div>
    </>
  );
}

export function StudentsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Students" crumbs={cohortCrumbs(p, 'Students')}>{(r) => <Students {...r} />}</WithStatus>;
}

// --------------------------------------------------------------------------- staff

function Access({ v }: { v: boolean | null | undefined }) {
  if (v === undefined) return <span class="access"><span class="dot idle" aria-hidden="true" />Checking</span>;
  if (v === null) return <span class="access"><span class="dot idle" aria-hidden="true" />Unknown</span>;
  return v ? <span class="access"><span class="dot ok" aria-hidden="true" />Has access</span> : <span class="access"><span class="dot bad" aria-hidden="true" />Not a member</span>;
}

function dates(x: Person) {
  return `${x.start ? fmtShort(x.start) : 'term'} to ${x.end ? fmtShort(x.end) : 'end'}`;
}

function Staff(p: ReadyProps) {
  const { status } = p;
  const file = p.files.file(p.cohort.org, 'classroom-config', 'people.yml');
  const people = file.kind === 'ready' ? parsePeople(file.text) : [];
  const st = status.staff;
  const ins = people.filter((x) => x.role === 'instructor').length || st?.instructors || 0;
  const tas = people.filter((x) => x.role === 'ta').length || st?.tas || 0;
  const admins = p.course.admins;
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Staff')} />
      <div class="page-head">
        <div>
          <h1>Staff</h1>
          <p class="lede">{ins} instructor{ins === 1 ? '' : 's'} and {tas} teaching assistant{tas === 1 ? '' : 's'}.{st && !st.synced ? ' GitHub access does not match this list yet.' : ''}</p>
        </div>
        <div class="actions"><Soon label="Check staff access" cls="btn outline" /><Soon label="Add a person" /></div>
      </div>
      <Help title="Who is staff" doc="05-manage-teaching-team.md">
        <p>Handles here get the instructor buttons for this cohort; emails here get the problem emails. Check staff access makes GitHub match this list; it never removes access.</p>
        {admins.length ? <p>Course admins ({admins.join(', ')}) have access to every cohort of the course; they are set on Course details.</p> : null}
      </Help>
      <div class="stack">
        {file.kind === 'loading' ? <Loading what="Reading people.yml" /> : null}
        {file.kind === 'absent' ? <p class="footnote">There is no people.yml yet.</p> : null}
        {people.length ? (
          <div class="table-wrap">
            <table class="grid" style="min-width:880px">
              <thead><tr><th>Name</th><th>Role</th><th>Email</th><th class="sys">Access<span class="grp">from GitHub</span></th><th>Dates</th><th>Photo</th><th /></tr></thead>
              <tbody>
                {people.map((x) => (
                  <tr>
                    <td><b>{x.name || x.handle}</b><br /><span class="slug">{x.handle}</span></td>
                    <td>{x.role === 'instructor' ? 'Instructor' : 'Teaching assistant'}</td>
                    <td class="mono" style="font-size:13px">{x.email}</td>
                    <td><Access v={x.handle ? p.files.member(p.cohort.org, x.handle) : null} /></td>
                    <td class="num">{dates(x)}</td>
                    <td>{x.photo ? <span class="chip ok">Photo</span> : <span class="chip">No photo</span>}</td>
                    <td><Soon label="Edit" cls="btn small quiet" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <p class="footnote">Access states: <b>Has access</b>, <b>Not a member</b> (Check staff access invites them). An invitation that is pending shows as not a member until it is accepted.</p>
        <Lives org={p.cohort.org} repo="classroom-config" path="people.yml" />
      </div>
    </>
  );
}

export function StaffScreen(p: CohortProps) {
  return <WithStatus props={p} title="Staff" crumbs={cohortCrumbs(p, 'Staff')}>{(r) => <Staff {...r} />}</WithStatus>;
}
