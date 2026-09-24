// The assignment hub's Marks tab (S12, the grading sheet as a grid) and Teams tab (S9,
// teams.csv for a team assignment), and the cohort's read-only Marks overview.

import { useMemo, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { readTable, writeTable } from '../edit/csv';
import { useSave } from '../edit/save';
import { YamlText, type Path } from '../edit/yamlText';
import { Invalid } from '../forms/Form';
import { ASSIGNMENT_WORD, assignmentTitle, fmtDay, fmtWhen, str } from '../model/format';
import { cellValue, finalGrade, gradebookWrites, penaltyRate, penaltyText, readSheet, returnedOn, round, scoreTotal, type Unit } from '../model/marks';
import { parseRoster } from '../model/people';
import type { Assignment } from '../model/types';
import { returnMarks, teamsWindow, type AsgRef } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { tabHref } from '../router';
import { CheckLine, Crumbs, Help, Lives, Loading } from '../ui/bits';
import { SaveBar } from '../ui/edit';
import { Check, Ext, Lock } from '../ui/icons';
import type { TabProps } from './Assignments';
import { readSchedule } from './Cohort';
import { WithStatus, cohortCrumbs, cohortScope, todayOf, tzOf, useGradingConfig, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';

function courseDefault(p: ReadyProps, key: string): unknown {
  return ((p.course.meta?.assignment_defaults ?? {}) as Record<string, unknown>)[key];
}

function asgRef(a: Assignment, group: boolean): AsgRef {
  return { slug: a.slug, title: assignmentTitle(a), template: a.template, units: a.units, group, when: 'Marking' };
}

// --------------------------------------------------------------------------- marks

const key = (path: Path) => JSON.stringify(path);

export function MarksTab(p: TabProps) {
  const { a } = p;
  const env = useEnv();
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const [save, runSave, setSave] = useSave(env);
  const path = `grading_sheets/${a.slug}.yml`;
  const file = p.files.file(p.cohort.org, 'classroom-config', path);
  const text = file.kind === 'ready' ? file.text : null;
  const parsed = useMemo(() => {
    if (text === null) return null;
    const y = new YamlText(text);
    return y.errors.length ? { error: y.errors[0] } : { sheet: readSheet(text, y.toJS()) };
  }, [text]);
  const cfg = useGradingConfig(p, a.template);
  const questions = cfg.questions && typeof cfg.questions === 'object' ? (cfg.questions as Record<string, unknown>) : null;
  const qs = questions ? Object.entries(questions) : [];
  const rateText = cfg.late_penalty_per_day ?? courseDefault(p, 'late_penalty_per_day') ?? '10%';
  const rate = penaltyRate(rateText);
  const max = qs.reduce((s, [, n]) => s + (Number(n) || 0), 0);
  const group = a.teams !== null && a.teams !== undefined;
  const scope = cohortScope(p);
  const head = (
    <div class="page-head">
      <div><h1>{assignmentTitle(a)}</h1><p class="lede">{a.marks.filled} of {a.marks.total} marked. Totals and late penalties are worked out for you.</p></div>
      <div class="actions"><OpButtons def={returnMarks(scope, asgRef(a, group), a.marks.filled)} /></div>
    </div>
  );
  const top = <>{head}{p.tabs}</>;
  if (file.kind === 'loading') return <>{top}<Loading what="Reading the mark sheet" /></>;
  if (file.kind !== 'ready')
    return <>{top}<section class="panel section stub"><h2>No mark sheet yet</h2><p>The mark sheet appears at hand out, with a row for every student or team.</p></section></>;
  if (!parsed?.sheet)
    return <>{top}<CheckLine cls="bad">The mark sheet does not parse ({parsed?.error}). Fix it with Edit the file.</CheckLine></>;
  const { sheet } = parsed;
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
    const open = !folded[u.key];
    return [
      <tr class={`team-row${nosub ? ' nosub' : ''}`}>
        <td>
          <button class="fold-toggle" type="button" aria-expanded={open} aria-label={`${open ? 'Hide' : 'Show'} the members of ${u.key}`} onClick={() => setFolded({ ...folded, [u.key]: open })}>{open ? '▾' : '▸'}</button>
          <b>{u.key}</b><br /><span class="footnote">{u.people.length} member{u.people.length === 1 ? '' : 's'}</span>
        </td>{sys}{scoreCells}
        <td>{u.feedbackPath ? input(u.feedbackPath, u.feedback, 'fb', `Team feedback for ${u.key}`, false, true) : null}</td>
        <td /><td /><td class="pen">{penaltyText(rate, days)}</td><td class="calc">{round(finalGrade(total, rate, days, 0))}</td>
      </tr>,
      ...(open ? u.people : []).map((pr) => (
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
      {top}
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
              <th rowSpan={2}>Adjust ±<span class="grp">{sheet.group ? 'individual adjustment relative to team (optional)' : 'optional'}</span></th>
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

// --------------------------------------------------------------------------- teams

const TEAM_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TEAMS_HEADER = ['assignment', 'team', 'github_handle'];

interface TeamsState {
  teams: { name: string; members: string[] }[];
}

export function TeamsTab(p: TabProps) {
  const { a, status, now } = p;
  const env = useEnv();
  const tz = tzOf(status), year = yearOf(now, tz), today = todayOf(now, tz);
  const [draft, setDraft] = useState<TeamsState | null>(null);
  const [newTeam, setNewTeam] = useState('');
  const [teamError, setTeamError] = useState('');
  const [save, runSave, setSave] = useSave(env);
  const file = p.files.file(p.cohort.org, 'classroom-config', 'teams.csv');
  const roster = p.files.file(p.cohort.org, 'classroom-config', 'students.csv');
  const cfg = useGradingConfig(p, a.template);
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
  // As the engine's formation window: hand out to the grading pin (grading_datetime, else due).
  const pin = readSchedule(p)?.assignments[a.slug]?.grading ?? a.due;
  const opens = a.handout ? a.handout.slice(0, 10) : null;
  const closes = pin ? pin.slice(0, 10) : null;
  const window = !opens ? 'never' : today < opens ? 'pending' : closes && today > closes ? 'closed' : 'open';
  const empty = cur.teams.filter((t) => !t.members.length);
  return (
    <>
      <div class="page-head">
        <div>
          <h1>{assignmentTitle(a)}</h1>
          <p class="lede">{joined.length - free.length} of {joined.length} joined students in {cur.teams.length} teams; {free.length} without a team.{notJoined ? ` ${notJoined} students have not joined yet and cannot be placed.` : ''}</p>
        </div>
        <div class="actions"><a class="btn outline" href={`https://${p.cohort.org}.github.io/assignments/`} target="_blank" rel="noopener">Assignments on the student site <Ext /></a></div>
      </div>
      {p.tabs}
      <Help title="How teams form" doc="09-release-assignment-to-cohort.md">
        <p>Students form their own teams on the student site until the window closes; you can assign the rest here. Students without a team get no repo at hand out. Assign them here or they are left out.</p>
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
            <p class="footnote">The window runs from hand out until marking starts: the due date, unless the schedule sets a later grading date.</p>
            {window === 'open' && free.length ? <div class="actions"><OpButtons def={teamsWindow(cohortScope(p), asgRef(a, true), fmtDay(closes, tz, year))} small label={`Email ${free.length} without a team`} /></div> : null}
            <a class="textlink" href={`#schedule-${a.slug}`}>Change the dates in the schedule</a>
          </section>
          <section class="panel section">
            <h2>Team size</h2>
            <dl class="kv"><dt>Max team size</dt><dd>{maxSize} <span class="footnote">{cfg.max_team_size ? 'from the assignment template' : 'the course default'}</span></dd><dt>How teams form</dt><dd>{formation}</dd></dl>
            <a class="textlink" href={`?course=${p.course.org}#template-${a.slug}`}>Change on the assignment template</a>
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
                  <div class="team-h">{t.name}<span class={full ? 'full' : undefined}>{t.members.length} of {maxSize}{full ? ', full' : ''}</span></div>
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

// --------------------------------------------------------------------------- overview

const SHEETS = 'grading_sheets';
const LEDGER = 'gradebook/distributed.csv';

function OverviewRow(p: ReadyProps & { a: Assignment; hasSheet: boolean; writes: Map<string, string> | null }) {
  const { a, status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const path = `${SHEETS}/${a.slug}.yml`;
  const changed = p.hasSheet ? p.files.lastChange(p.cohort.org, 'classroom-config', path) : null;
  const sheet = a.returned && p.hasSheet ? p.files.file(p.cohort.org, 'classroom-config', path) : null;
  const text = sheet?.kind === 'ready' ? sheet.text : null;
  const doc = text !== null ? new YamlText(text) : null;
  const on = text !== null && doc && !doc.errors.length && p.writes ? returnedOn(readSheet(text, doc.toJS()), p.writes) : null;
  return (
    <tr>
      <td><a class="rowlink" href={tabHref(a.slug, 'marks')}>{assignmentTitle(a)}</a></td>
      <td><span class={`chip ${a.state === 'marking' ? 'asg' : ''}`}>{ASSIGNMENT_WORD[a.state]}</span></td>
      <td class="num">{p.hasSheet ? `${a.marks.filled} / ${a.marks.total}` : <span class="footnote">No mark sheet yet</span>}</td>
      <td>{a.returned ? <span class="chip ok">Yes</span> : 'No'}</td>
      <td class="num">{on ? fmtDay(on, tz, year) : ''}</td>
      <td class="num">{changed === undefined ? '…' : changed ? fmtWhen(changed, tz, year) : ''}</td>
    </tr>
  );
}

function MarksOverview(p: ReadyProps) {
  const list = p.status.assignments ?? [];
  const dir = p.files.dir(p.cohort.org, 'classroom-config', SHEETS);
  const sheets = new Set(dir.kind === 'ready' ? dir.entries.map((e) => e.name) : []);
  const ledger = p.files.file(p.cohort.org, 'classroom-config', LEDGER);
  const writes = ledger.kind === 'ready' ? gradebookWrites(ledger.text) : ledger.kind === 'loading' ? null : new Map<string, string>();
  const returned = list.filter((a) => a.returned).length;
  const toMark = list.reduce((n, a) => n + (sheets.has(`${a.slug}.yml`) ? a.marks.total - a.marks.filled : 0), 0);
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Marks')} />
      <div class="page-head">
        <div>
          <h1>Marks</h1>
          <p class="lede">{sheets.size ? `${returned} of ${list.length} assignment${list.length === 1 ? '' : 's'} returned; ${toMark} mark${toMark === 1 ? '' : 's'} still to enter.` : 'Where marking stands this term, one row per assignment.'}</p>
        </div>
      </div>
      <Help title="Where marks are entered" doc="10-grade-and-return-assignments.md">
        <p>This page only reads. Open an assignment to enter its marks and return them to students.</p>
      </Help>
      {dir.kind === 'loading' ? <Loading what="Reading the mark sheets" /> : dir.kind === 'error' ? (
        <CheckLine cls="bad">Could not list the mark sheets in classroom-config/{SHEETS}: {dir.message}</CheckLine>
      ) : !sheets.size ? (
        <section class="panel section stub">
          <h2>No mark sheets yet</h2>
          <p>A mark sheet appears when an assignment is handed out, with a row for every student or team.</p>
        </section>
      ) : (
        <>
          <div class="table-wrap">
            <table class="grid" style="min-width:820px">
              <thead><tr><th>Assignment</th><th>State</th><th>Marked</th><th>Returned</th><th>Gradebooks last updated</th><th>Last change</th></tr></thead>
              <tbody>{list.map((a) => <OverviewRow {...p} a={a} hasSheet={sheets.has(`${a.slug}.yml`)} writes={writes} />)}</tbody>
            </table>
          </div>
          <p class="footnote" style="margin-top:8px">Gradebooks last updated: the newest write to this assignment’s students’ gradebooks. Every return rewrites every student’s gradebook, so this date moves forward for all returned assignments whenever any assignment is returned. Last change: the newest edit to the mark sheet.</p>
        </>
      )}
    </>
  );
}

export function MarksOverviewScreen(p: CohortProps) {
  return <WithStatus props={p} title="Marks" crumbs={cohortCrumbs(p, 'Marks')}>{(r) => <MarksOverview {...r} />}</WithStatus>;
}
