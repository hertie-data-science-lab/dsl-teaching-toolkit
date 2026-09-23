// S12 Marks (the grading sheet as a grid) and S9 Teams (teams.csv for a group assignment).

import { useState } from 'preact/hooks';
import { parse } from 'yaml';
import { useEnv } from '../env';
import { readTable, writeTable } from '../edit/csv';
import { useSave } from '../edit/save';
import { YamlText, type Path } from '../edit/yamlText';
import { Invalid } from '../forms/Form';
import { assignmentIdent, assignmentTitle, fmtDay } from '../model/format';
import { cellValue, finalGrade, penaltyRate, penaltyText, readSheet, round, scoreTotal, type Unit } from '../model/marks';
import { parseRoster } from '../model/people';
import type { Assignment } from '../model/types';
import { returnMarks, type AsgRef } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { CheckLine, Crumbs, Help, Lives, Loading, Prop } from '../ui/bits';
import { SaveBar } from '../ui/edit';
import { Check, Ext, Lock } from '../ui/icons';
import { NotFound } from './Assignments';
import { todayOf, tzOf, yearOf } from './Cohort';
import { WithStatus, cohortCrumbs, cohortScope } from './common';
import type { CohortProps, ReadyProps } from './types';

function gradingConfig(p: ReadyProps, template: string): Record<string, unknown> {
  const f = p.files.file(p.course.org, template, 'grading_config.yml', 'solution');
  if (f.kind !== 'ready') return {};
  try {
    const d = parse(f.text);
    return d && typeof d === 'object' ? (d as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function courseDefault(p: ReadyProps, key: string): unknown {
  return ((p.course.meta?.assignment_defaults ?? {}) as Record<string, unknown>)[key];
}

function asgRef(a: Assignment, group: boolean): AsgRef {
  return { slug: a.slug, title: assignmentTitle(a), template: a.template, units: a.units, group, when: 'Marking' };
}

// --------------------------------------------------------------------------- marks

const key = (path: Path) => JSON.stringify(path);
const str = (v: unknown) => (v === null || v === undefined ? '' : String(v));

function Marks(p: ReadyProps & { a: Assignment }) {
  const { a } = p;
  const env = useEnv();
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [save, runSave, setSave] = useSave(env);
  const path = `grading_sheets/${a.slug}.yml`;
  const file = p.files.file(p.cohort.org, 'classroom-config', path);
  const cfg = gradingConfig(p, a.template);
  const questions = cfg.questions && typeof cfg.questions === 'object' ? (cfg.questions as Record<string, unknown>) : null;
  const qs = questions ? Object.entries(questions) : [];
  const rateText = cfg.late_penalty_per_day ?? courseDefault(p, 'late_penalty_per_day') ?? '10%';
  const rate = penaltyRate(rateText);
  const max = qs.reduce((s, [, n]) => s + (Number(n) || 0), 0);
  const crumbs = cohortCrumbs(p, 'Marks', [{ t: assignmentIdent(a.slug), href: `#assignment-${a.slug}` }]);
  const group = a.teams !== null && a.teams !== undefined;
  const scope = cohortScope(p);
  const head = (
    <div class="page-head">
      <div><h1>Marks: {assignmentTitle(a)}</h1><p class="lede">{a.marks.filled} of {a.marks.total} marked. Totals and late penalties are worked out for you.</p></div>
      <div class="actions"><OpButtons def={returnMarks(scope, asgRef(a, group), a.marks.filled)} /></div>
    </div>
  );
  if (file.kind === 'loading') return <><Crumbs items={crumbs} />{head}<Loading what="Reading the mark sheet" /></>;
  if (file.kind !== 'ready')
    return <><Crumbs items={crumbs} />{head}<section class="panel section stub"><h2>No mark sheet yet</h2><p>The mark sheet appears at hand out, with a row for every student or team.</p></section></>;
  const y = new YamlText(file.text);
  if (y.errors.length)
    return <><Crumbs items={crumbs} />{head}<CheckLine cls="bad">The mark sheet does not parse ({y.errors[0]}). Fix it with Edit the file.</CheckLine></>;
  const sheet = readSheet(file.text, y.toJS());
  const v = (pth: Path, orig: unknown) => (key(pth) in edits ? edits[key(pth)] : orig);
  const set = (pth: Path, raw: string, typed = false) => {
    setEdits({ ...edits, [key(pth)]: typed ? cellValue(raw) : raw.trim() ? raw : null });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const changed = new Set(Object.keys(edits).map((k) => (JSON.parse(k) as Path)[1] as string)).size;
  const scoreOf = (u: Unit) => {
    if (qs.length) {
      const base = u.score && typeof u.score === 'object' ? (u.score as Record<string, unknown>) : {};
      return Object.fromEntries(qs.map(([q]) => [q, v([...u.scorePath, q], base[q] ?? null)]));
    }
    return v(u.scorePath, u.score && typeof u.score === 'object' ? null : u.score);
  };
  const input = (pth: Path, orig: unknown, cls: string, label: string, typed = true, area = false) =>
    area ? (
      <textarea class={cls} rows={1} aria-label={label} onInput={(e) => set(pth, (e.target as HTMLTextAreaElement).value, false)}>{str(v(pth, orig))}</textarea>
    ) : (
      <input class={cls} type="text" inputMode={typed ? 'decimal' : undefined} value={str(v(pth, orig))} aria-label={label} onInput={(e) => set(pth, (e.target as HTMLInputElement).value, typed)} />
    );
  const info = (u: Unit, k: string) => str(u.info?.[k]);
  const rows = sheet.units.flatMap((u) => {
    const score = scoreOf(u);
    const total = scoreTotal(score, questions);
    const days = u.info?.days_late;
    const nosub = u.info !== null && !u.info?.submitted;
    const scoreCells = qs.length
      ? qs.map(([q, m]) => <td>{input([...u.scorePath, q], (u.score as Record<string, unknown> | null)?.[q] ?? null, 'q', `${q} (out of ${m}) for ${u.key}`)}</td>)
      : [<td>{input(u.scorePath, typeof u.score === 'object' ? null : u.score, 'q', `Score for ${u.key}`)}</td>];
    const sys = [
      <td class="sys">{info(u, 'submitted').replace('T', ' ') || (u.info ? 'Nothing' : '—')}</td>,
      <td class="sys num">{info(u, 'days_late')}</td>,
      <td class="sys">{info(u, 'autograde')}</td>,
      <td class="sys">{info(u, 'completion')}</td>,
    ];
    if (!sheet.group) {
      const pr = u.people[0];
      return [
        <tr class={nosub ? 'nosub' : ''}>
          <td><b>{u.key}</b></td>{sys}{scoreCells}
          <td>{input([...pr.base, 'feedback_individual'], pr.feedback, 'fb', `Feedback for ${u.key}`, false, true)}</td>
          <td>{input([...pr.base, 'adjustment_individual'], pr.adjustment, 'adj', `Adjustment for ${u.key}`)}</td>
          <td>{input([...pr.base, 'notes_not_shared_with_students'], pr.notes, 'pn', `Private notes for ${u.key}`, false)}</td>
          <td class="pen">{penaltyText(rate, days)}</td>
          <td class="calc">{round(finalGrade(total, rate, days, v([...pr.base, 'adjustment_individual'], pr.adjustment)))}</td>
        </tr>,
      ];
    }
    return [
      <tr class={`team-row${nosub ? ' nosub' : ''}`}>
        <td><b>{u.key}</b><br /><span class="footnote">{u.people.length} member{u.people.length === 1 ? '' : 's'}</span></td>{sys}{scoreCells}
        <td>{u.feedbackPath ? input(u.feedbackPath, u.feedback, 'fb', `Team feedback for ${u.key}`, false, true) : null}</td>
        <td /><td /><td class="pen">{penaltyText(rate, days)}</td><td class="calc">{round(finalGrade(total, rate, days, 0))}</td>
      </tr>,
      ...u.people.map((pr) => (
        <tr class="member-row">
          <td style="padding-left:22px"><span class="slug">{pr.handle}</span></td>
          <td class="sys" colSpan={4} /><td colSpan={Math.max(1, qs.length)} />
          <td>{input([...pr.base, 'feedback_individual'], pr.feedback, 'fb', `Feedback for ${pr.handle}`, false, true)}</td>
          <td>{input([...pr.base, 'adjustment_individual'], pr.adjustment, 'adj', `Adjustment for ${pr.handle}`)}</td>
          <td>{input([...pr.base, 'notes_not_shared_with_students'], pr.notes, 'pn', `Private notes for ${pr.handle}`, false)}</td>
          <td class="pen" /><td class="calc">{round(finalGrade(total, rate, days, v([...pr.base, 'adjustment_individual'], pr.adjustment)))}</td>
        </tr>
      )),
    ];
  });
  const doSave = async () => {
    const out = new YamlText(file.text);
    for (const [k, val] of Object.entries(edits)) out.assign(JSON.parse(k) as Path, val);
    const ok = await runSave({ owner: p.cohort.org, repo: 'classroom-config', path }, out.text, file.sha, { message: `marks: ${a.slug}, ${changed} ${sheet.group ? 'team' : 'student'}${changed === 1 ? '' : 's'}, from the Instructor Console`, statusRepo: [p.cohort.org, 'classroom-config'] });
    if (ok) setEdits({});
  };
  return (
    <>
      <Crumbs items={crumbs} />
      {head}
      <Help title="How marks work" doc="10-grade-and-return-assignments.md">
        <p>Submission details come from the repos and cannot be edited. You enter {qs.length ? 'points per question' : 'one score'}, feedback that students see, an adjustment, and private notes that are never shared. {rate !== null ? `The penalty is ${round(rate * 100)}% of the total per late day.` : 'No late penalty applies.'} Nothing reaches a student until you return marks.</p>
      </Help>
      {sheet.frozen ? <p class="note" style="margin-bottom:12px"><b>Frozen at the cutoff.</b> The submission details no longer change; your marks and feedback are still yours to edit.</p> : null}
      <div class="table-wrap" style="max-height:620px;overflow:auto">
        <table class="grid marks">
          <thead>
            <tr>
              <th rowSpan={2}>{sheet.group ? 'Team' : 'Student'}</th>
              <th class="sys grp-h" colSpan={4}>Submission<span class="grp">system <Lock /></span></th>
              <th class="grp-h" colSpan={Math.max(1, qs.length)}>{qs.length ? 'Points per question' : 'Score'}</th>
              <th rowSpan={2}>Feedback<span class="grp">students see</span></th>
              <th rowSpan={2}>Adjust<span class="grp">±</span></th>
              <th rowSpan={2}>Private notes<span class="grp">never shared</span></th>
              <th rowSpan={2} class="sys">Penalty</th>
              <th rowSpan={2} class="sys">Total{max ? ` / ${max}` : ''}</th>
            </tr>
            <tr>
              <th class="sys">Submitted</th><th class="sys">Days late</th><th class="sys">Tests</th><th class="sys">Completion</th>
              {qs.length ? qs.map(([q, m]) => <th>{q}<span class="grp">/ {String(m)}</span></th>) : <th>{max ? `/ ${max}` : 'score'}</th>}
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
      <div class="panel" style="display:grid;gap:10px;margin-top:14px">
        <SaveBar state={save} onSave={() => void doSave()} label="Save marks" disabled={!changed} file={{ org: p.cohort.org, repo: 'classroom-config', path }} note={changed ? `${changed} ${sheet.group ? 'team' : 'student'}${changed > 1 ? 's' : ''} changed` : 'No unsaved changes'} />
        <Lives org={p.cohort.org} repo="classroom-config" path={path} />
      </div>
    </>
  );
}

export function MarksScreen(p: CohortProps) {
  const title = p.entry ? `Marks: ${assignmentIdent(p.entry)}` : 'Marks';
  return (
    <WithStatus props={p} title={title} crumbs={cohortCrumbs(p, 'Marks', [{ t: 'Assignments', href: '#assignments' }])}>
      {(r) => {
        const a = (r.status.assignments ?? []).find((x) => x.slug === p.entry);
        return a ? <Marks {...r} a={a} /> : <NotFound what={`No assignment called ${p.entry ?? '(none)'} to mark.`} back="#assignments" />;
      }}
    </WithStatus>
  );
}

// --------------------------------------------------------------------------- teams

const TEAM_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TEAMS_HEADER = ['assignment', 'team', 'github_handle'];

interface TeamsState {
  teams: { name: string; members: string[] }[];
}

function Teams(p: ReadyProps & { a: Assignment; groups: Assignment[] }) {
  const { a, status, now } = p;
  const env = useEnv();
  const tz = tzOf(status), year = yearOf(now, tz), today = todayOf(now, tz);
  const [draft, setDraft] = useState<TeamsState | null>(null);
  const [newTeam, setNewTeam] = useState('');
  const [teamError, setTeamError] = useState('');
  const [save, runSave, setSave] = useSave(env);
  const file = p.files.file(p.cohort.org, 'classroom-config', 'teams.csv');
  const roster = p.files.file(p.cohort.org, 'classroom-config', 'students.csv');
  const cfg = gradingConfig(p, a.template);
  const maxSize = Number(cfg.max_team_size ?? courseDefault(p, 'max_team_size') ?? 5) || 5;
  const formation = cfg.team_formation === 'assigned' ? 'You assign them' : 'Students form their own';
  const table = file.kind === 'ready' ? readTable(file.text) : { header: TEAMS_HEADER, rows: [] };
  const base: TeamsState = { teams: [] };
  for (const r of table.rows) {
    if ((r.assignment ?? '').trim() !== a.slug) continue;
    const name = (r.team ?? '').trim();
    let t = base.teams.find((x) => x.name === name);
    if (!t) base.teams.push((t = { name, members: [] }));
    if (r.github_handle?.trim()) t.members.push(r.github_handle.trim());
  }
  const cur = draft ?? base;
  const students = roster.kind === 'ready' ? parseRoster(roster.text).rows : [];
  const joined = students.filter((s) => s.handle);
  const nameOf = (h: string) => students.find((s) => s.handle.toLowerCase() === h.toLowerCase())?.name || h;
  const inTeam = new Set(cur.teams.flatMap((t) => t.members.map((m) => m.toLowerCase())));
  const free = joined.filter((s) => !inTeam.has(s.handle.toLowerCase()));
  const notJoined = students.filter((s) => !s.handle).length;
  const change = (next: TeamsState) => {
    setDraft(next);
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const move = (h: string, to: string | null) =>
    change({ teams: cur.teams.map((t) => ({ ...t, members: t.name === to ? [...t.members.filter((m) => m !== h), h] : t.members.filter((m) => m !== h) })) });
  const create = () => {
    const nm = newTeam.trim();
    if (!TEAM_RE.test(nm)) return setTeamError('Use lowercase letters, numbers and dashes, like team-alpha.');
    if (cur.teams.some((t) => t.name === nm)) return setTeamError(`There is already a team called “${nm}”.`);
    setTeamError('');
    setNewTeam('');
    change({ teams: [...cur.teams, { name: nm, members: [] }] });
  };
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(base);
  const over = cur.teams.filter((t) => t.members.length > maxSize);
  const doSave = async () => {
    if (over.length) return setSave({ kind: 'bad', text: `${over.map((t) => t.name).join(', ')} ${over.length > 1 ? 'have' : 'has'} more than ${maxSize} members.` });
    const others = table.rows.filter((r) => (r.assignment ?? '').trim() !== a.slug);
    const mine = cur.teams.flatMap((t) => (t.members.length ? t.members : ['']).map((h) => ({ assignment: a.slug, team: t.name, github_handle: h })));
    const header = table.header.length ? table.header : TEAMS_HEADER;
    const text = writeTable({ header, rows: [...others, ...mine.filter((r) => r.github_handle)] });
    const ok = await runSave({ owner: p.cohort.org, repo: 'classroom-config', path: 'teams.csv' }, text, file.kind === 'ready' ? file.sha : null, { message: `teams: ${a.slug}, from the Instructor Console`, statusRepo: [p.cohort.org, 'classroom-config'] });
    if (ok) setDraft(null);
  };
  const opens = a.handout ? a.handout.slice(0, 10) : null;
  const closes = a.late_until ? a.late_until.slice(0, 10) : null;
  const window = !opens ? 'never' : today < opens ? 'pending' : closes && today > closes ? 'closed' : 'open';
  const empty = cur.teams.filter((t) => !t.members.length);
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Teams')} />
      <div class="page-head">
        <div>
          <h1>Teams: {assignmentTitle(a)}</h1>
          <p class="lede">{joined.length - free.length} of {joined.length} joined students in {cur.teams.length} teams; {free.length} without a team.{notJoined ? ` ${notJoined} students have not joined yet and cannot be placed.` : ''}</p>
        </div>
        <div class="actions"><a class="btn outline" href={`https://${p.cohort.org}.github.io/teams/`} target="_blank" rel="noopener">Team list on the student site <Ext /></a></div>
      </div>
      {p.groups.length > 1 ? (
        <div class="filters" role="group" aria-label="Assignment" style="margin-bottom:12px">
          <span class="lbl">Assignment</span>
          {p.groups.map((g) => <a class="toggle" aria-pressed={g.slug === a.slug} href={`#teams-${g.slug}`}>{assignmentIdent(g.slug)}</a>)}
        </div>
      ) : null}
      <Help title="How teams form" doc="09-release-assignment-to-cohort.md">
        <p>Students form their own teams on the student site until the window closes; you can assign the rest here. Teams are fixed at hand out. Team-less students get their own repo then.</p>
      </Help>
      <div class="stack">
        <div class="grid-2">
          <section class="panel section">
            <h2>Window</h2>
            <p>
              <span class={`chip ${window === 'open' ? 'ok' : ''}`}>{window === 'open' ? 'Open' : window === 'pending' ? 'Not open yet' : window === 'closed' ? 'Closed' : 'Never opens'}</span>{' '}
              {window === 'open' ? `Students can form and join teams on the student site until ${fmtDay(closes, tz, year)}.`
                : window === 'pending' ? `Opens at hand out, ${fmtDay(opens, tz, year)}.`
                : window === 'closed' ? 'Only you can change teams now.'
                : 'This assignment is handed out by hand, so students cannot form teams on the site; assign them here.'}
            </p>
            <p class="footnote">The window runs from hand out to the end of late work, both set in the schedule. <Prop /> A separate close date is proposed.</p>
            <a class="textlink" href={`#schedule-${a.slug}`}>Change the dates in the schedule</a>
          </section>
          <section class="panel section">
            <h2>Team size</h2>
            <dl class="kv"><dt>Largest team</dt><dd>{maxSize} <span class="footnote">{cfg.max_team_size ? 'from the template' : 'the course default'}</span></dd><dt>How teams form</dt><dd>{formation}</dd></dl>
            <a class="textlink" href={`?course=${p.course.org}#template-${a.slug}`}>Change on the template</a>
          </section>
        </div>
        <section class="panel section">
          <div class="section-head"><h2>Without a team</h2><span class="meta">Assign with the picker</span></div>
          <div class="free-list">
            {roster.kind === 'loading' ? <Loading what="Reading the roster" /> : free.length ? free.map((s) => (
              <div class="free-row">
                <span class="member free">{s.name || s.handle}</span>
                <select aria-label={`Team for ${s.name || s.handle}`} onChange={(e) => { const v = (e.target as HTMLSelectElement).value; if (v) move(s.handle, v); }}>
                  <option value="" selected>Add to a team…</option>
                  {cur.teams.filter((t) => t.members.length < maxSize).map((t) => <option value={t.name}>{t.name} ({t.members.length}/{maxSize})</option>)}
                </select>
              </div>
            )) : <p class="valid-msg"><Check />Every joined student is in a team.</p>}
          </div>
        </section>
        <section class="panel section">
          <div class="section-head"><h2>Teams</h2><span class="meta">{cur.teams.length} teams</span></div>
          <div class="team-grid">
            {cur.teams.map((t) => {
              const full = t.members.length >= maxSize;
              return (
                <div class="team">
                  <div class="team-h">{t.name}<span class={full ? 'full' : ''}>{t.members.length} of {maxSize}{full ? ', full' : ''}</span></div>
                  <div class="members">
                    {t.members.map((m) => <span class="member">{nameOf(m)} <button class="x" type="button" aria-label={`Take ${nameOf(m)} out of ${t.name}`} onClick={() => move(m, null)}>&times;</button></span>)}
                    {!t.members.length ? <span class="footnote">No members yet; an empty team is not saved.</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
          <div class="savebar">
            <div class="field">
              <label for="new-team">New team name</label>
              <input type="text" id="new-team" value={newTeam} placeholder="lowercase-with-dashes" onInput={(e) => setNewTeam((e.target as HTMLInputElement).value)} />
              <p class="why">Becomes the team repo’s name.</p>
            </div>
            <button class="btn small outline" type="button" style="align-self:end" onClick={create}>Create team</button>
          </div>
          {teamError ? <Invalid>{teamError}</Invalid> : null}
          {empty.length && dirty ? <p class="footnote">{empty.length} empty team{empty.length > 1 ? 's are' : ' is'} kept only until you leave this page.</p> : null}
        </section>
        <div class="panel" style="display:grid;gap:10px">
          <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty} file={{ org: p.cohort.org, repo: 'classroom-config', path: 'teams.csv' }} note={dirty ? 'Unsaved changes' : undefined} />
          <Lives org={p.cohort.org} repo="classroom-config" path="teams.csv" />
        </div>
      </div>
    </>
  );
}

export function TeamsScreen(p: CohortProps) {
  return (
    <WithStatus props={p} title="Teams" crumbs={cohortCrumbs(p, 'Teams')}>
      {(r) => {
        const groups = (r.status.assignments ?? []).filter((x) => x.teams !== null && x.teams !== undefined);
        const a = groups.find((x) => x.slug === p.entry) ?? (p.entry ? undefined : groups[0]);
        if (!a) return <NotFound what={p.entry ? `${p.entry} is not a group assignment in this cohort.` : 'No group assignment in this cohort: every assignment is done alone.'} back="#assignments" />;
        return <Teams {...r} a={a} groups={groups} />;
      }}
    </WithStatus>
  );
}
