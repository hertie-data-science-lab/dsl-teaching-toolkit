// S8 Students (the roster grid over students.csv and the engine's .system/roster.csv, Replace
// from CSV) and S7 Instructors (semester.yml `people:`).

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { EMAIL_RE, diffRoster, missingColumns, readTable, writeTable, type RosterDiff } from '../edit/csv';
import { saveSteps, useSave, type SaveState, type Step } from '../edit/save';
import { YamlText } from '../edit/yamlText';
import { Field, SchemaForm, fieldErrors } from '../forms/Form';
import { assignmentTitle, fmtShort } from '../model/format';
import { ROLE_WORD, ROSTER_HEADER, ROSTER_SAVE_NOTE, SYSTEM_HEADER, TEAM_PREFIX, parseInstructors, pendingSummary, roleSplit, sameHandle, systemByEmail, teamColumn, type Person } from '../model/people';
import { checkAccess, sendCodes, syncMembershipUrl } from '../ops/defs';
import { OpOpen } from '../ops/Panel';
import { PERSON, PHOTO_DIR, displayOnly, photoError, photoName } from '../tiers/people';
import { pick } from '../tiers/types';
import { GitHubError, authorOf } from '../github/client';
import { CheckLine, EditFile, Loading, ProblemCards, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveBar, SaveLine } from '../ui/edit';
import { StudentCounts } from './Cohort';
import { WithStatus, cohortScope } from './common';
import type { CohortProps, ReadyProps } from './types';
import { CONFIG_REPO, ROSTER_PATH, SEMESTER_FILE, STUDENTS_FILE } from '../model/names';
import { pictureOf } from '../model/student';
import { semesterFile } from '../model/settings';
import { useLoad } from '../ui/load';
import { studentData } from './Student';
import { initials } from './StudentInstructors';

type Row = Record<string, string>;
const YOURS = ['email', 'name', 'role'] as const;

/** A row's state, from the engine's `.system/roster.csv` (`sys`, its row for this email). */
function RowStatus({ r, sys }: { r: Row; sys: Row }) {
  if (!sys.code_sent_at) return <span class="notsent">Not sent{r.email && !EMAIL_RE.test(r.email) ? ': email has no domain' : ''}</span>;
  if (sys.github_handle) return <span class="joined"><span class="dot ok" aria-hidden="true" />Joined</span>;
  return <span class="joined"><span class="dot idle" aria-hidden="true" />Code sent; not joined</span>;
}

/** A team column's header: the assignment it is for, by number (`team:4`), from the status. */
export function teamHeading(column: string, assignments: { number?: number | null; slug: string; title: string }[]): string {
  const a = assignments.find((x) => teamColumn(x) === column);
  return a ? `Team, ${assignmentTitle(a as Parameters<typeof assignmentTitle>[0])}` : `Team, ${column.slice(TEAM_PREFIX.length)}`;
}

/**
 * `.system/roster.csv` with the joined students' new handles written in (`handles`: email ->
 * handle), the one edit that file takes (a switched account, `relink`); null when nothing changes.
 */
export function rosterAfter(text: string, handles: Record<string, string>): string | null {
  const t = readTable(text);
  let changed = false;
  const rows = t.rows.map((r) => {
    const h = handles[(r.email ?? '').trim().toLowerCase()];
    if (h === undefined || sameHandle(h, r.github_handle ?? '')) return r;
    changed = true;
    return { ...r, github_handle: h.trim() };
  });
  return changed ? writeTable({ header: t.header.length ? t.header : SYSTEM_HEADER, rows }) : null;
}

function Students(p: ReadyProps) {
  const { status } = p;
  const env = useEnv();
  const s = status.students ?? { rows: 0, codes_sent: 0, joined: 0 };
  const file = p.files.file(p.cohort.org, CONFIG_REPO, STUDENTS_FILE);
  const sysFile = p.files.file(p.cohort.org, CONFIG_REPO, ROSTER_PATH);
  const [edits, setEdits] = useState<Record<number, Row>>({});
  const [added, setAdded] = useState<Row[]>([]);
  // A joined student's new handle, by file row (decision 0017 relink): written to `.system/roster.csv`.
  const [handles, setHandles] = useState<Record<number, string>>({});
  // File rows marked for removal (by index), struck through until Save drops them.
  const [removed, setRemoved] = useState<number[]>([]);
  const [replacement, setReplacement] = useState<{ rows: Row[]; diff: RosterDiff; name: string } | null>(null);
  const [upload, setUpload] = useState<{ open: boolean; error: string; pending: { rows: Row[]; diff: RosterDiff; name: string } | null }>({ open: false, error: '', pending: null });
  // Read view until Edit roster: the grid then shows inputs for your columns (and a joined
  // row's handle), saved together by the Save bar below.
  const [editing, setEditing] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });
  const table = file.kind === 'ready' ? readTable(file.text) : null;
  const sysText = sysFile.kind === 'ready' ? sysFile.text : '';
  const sys = systemByEmail(sysText);
  const sysOf = (r: Row): Row => sys.get((r.email ?? '').trim().toLowerCase()) ?? {};
  const missing = table ? missingColumns(table.header, [...ROSTER_HEADER]) : [];
  const teamCols = (table?.header ?? []).filter((h) => h.startsWith(TEAM_PREFIX));
  const edited = [...YOURS, ...teamCols];
  const faultLines = new Set((status.problems ?? []).filter((x) => x.stage === 'K5' && x.fix?.line && (!x.fix.path || x.fix.path === STUDENTS_FILE)).map((x) => x.fix!.line!));
  const problems = (status.problems ?? []).filter((x) => x.stage === 'K5');
  const scope = cohortScope(p);
  const waiting = Math.max(0, s.codes_sent - s.joined);

  const base = table?.rows ?? [];
  // The role split is the file's: the status counts rows, not roles.
  const split = table && !missing.length ? roleSplit(base) : null;
  // Edits are keyed by row index. `lines` is each row's line in the file (blank lines and quoted
  // newlines move it), shown and matched with the engine's faults; a row added here, or a
  // replacement's, is numbered on from the file's last row.
  const current: Row[] = replacement ? replacement.rows : [...base.map((r, i) => ({ ...r, ...(edits[i] ?? {}) })), ...added];
  const fileLines = replacement ? [] : (table?.lines ?? []);
  const lastLine = fileLines[fileLines.length - 1] ?? 1;
  const lines = current.map((_, i) => fileLines[i] ?? lastLine + i - fileLines.length + 1);
  const gone = (i: number) => !replacement && removed.includes(i);
  // What Save writes: every row but the removed ones.
  const kept = current.filter((_, i) => !gone(i));
  const keptBase = base.filter((_, i) => !gone(i));
  const handleOf = (i: number) => (i < base.length && handles[i] !== undefined ? handles[i] : (sysOf(base[i] ?? {}).github_handle ?? ''));
  const switchedRows = replacement ? [] : base.map((_, i) => i).filter((i) => !gone(i) && handles[i] !== undefined && !sameHandle(handles[i], sysOf(base[i]).github_handle ?? ''));
  const d0 = replacement?.diff;
  const counts = d0
    ? { added: d0.added.length, removed: d0.removed.length, changed: d0.changed.length }
    : {
        added: added.filter((r) => r.email || r.name).length,
        removed: removed.length,
        changed: base.map((_, i) => i).filter((i) => !gone(i) && (edited.some((k) => (edits[i]?.[k] ?? base[i][k] ?? '') !== (base[i][k] ?? '')) || switchedRows.includes(i))).length,
      };
  const nEdits = replacement ? 1 : counts.added + counts.removed + counts.changed;
  const badEmail = kept.filter((r) => (r.email || r.name) && !EMAIL_RE.test(r.email ?? ''));
  const noName = replacement ? [] : added.filter((r) => r.email && !r.name?.trim());
  const unsent = (r: Row) => !sysOf(r).code_sent_at && EMAIL_RE.test(r.email ?? '');
  const toSend = kept.filter(unsent).length - keptBase.filter(unsent).length;
  const saveLabel = !nEdits ? 'Save' : toSend > 0 ? `Save and send ${toSend} code${toSend > 1 ? 's' : ''}` : `Save ${nEdits} change${nEdits > 1 ? 's' : ''}`;

  /** Row `i`'s `k`: an edit of a file row, or the text of a row added here. */
  const setCell = (i: number, k: string, v: string) => {
    if (i >= base.length) setAdded(added.map((x, j) => (j === i - base.length ? { ...x, [k]: v } : x)));
    else setEdits({ ...edits, [i]: { ...(edits[i] ?? {}), [k]: v } });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const onFile = async (f: File | undefined) => {
    if (!f || !table) return;
    const t = readTable(await f.text());
    const miss = missingColumns(t.header, [...ROSTER_HEADER]);
    if (miss.length) return setUpload({ open: true, error: `${f.name} needs the header ${ROSTER_HEADER.join(',')}; it lacks ${miss.join(', ')}.`, pending: null });
    const { diff, rows } = diffRoster(base, t.rows);
    setUpload({ open: true, error: '', pending: { rows, diff, name: f.name } });
  };
  const doSave = async () => {
    if (!table || file.kind !== 'ready') return;
    if (noName.length) return setSave({ kind: 'bad', text: `${noName.length} added row${noName.length > 1 ? 's have' : ' has'} no name.` });
    if (badEmail.length) return setSave({ kind: 'bad', text: `${badEmail.length} row${badEmail.length > 1 ? 's have' : ' has'} an email that is not an address; no code can be sent to it.` });
    if (switchedRows.some((i) => !handles[i].trim())) return setSave({ kind: 'bad', text: 'A joined student’s GitHub handle cannot be blank. Put back the old one, or type their new account.' });
    for (const i of switchedRows) {
      const handle = handles[i].trim();
      setSave({ kind: 'busy', text: `Checking that ${handle} exists on GitHub…` });
      let ok = false;
      try {
        ok = env ? await env.client.userExists(handle) : true;
      } catch {
        ok = true; // cannot tell: the sync checks the account itself
      }
      if (!ok) return setSave({ kind: 'bad', text: `There is no GitHub account called ${handle}.` });
    }
    // students.csv holds only its own columns (and any a person added): never the engine's.
    const rows = kept.filter((r) => r.email || r.name);
    const text = writeTable({ header: table.header, rows });
    const studentsChanged = replacement !== null || counts.added + counts.removed > 0 || text !== file.text;
    const first: Step | null = studentsChanged ? { target: { owner: p.cohort.org, repo: CONFIG_REPO, path: STUDENTS_FILE }, text, sha: file.sha, opts: { message: `roster: ${replacement ? `replace from ${replacement.name}` : pendingSummary(counts)}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] } } : null;
    const relinked = switchedRows.length && sysFile.kind === 'ready' ? rosterAfter(sysFile.text, Object.fromEntries(switchedRows.map((i) => [(base[i].email ?? '').trim().toLowerCase(), handles[i]]))) : null;
    const second: Step | null = relinked !== null && sysFile.kind === 'ready' ? { target: { owner: p.cohort.org, repo: CONFIG_REPO, path: ROSTER_PATH }, text: relinked, sha: sysFile.sha, opts: { message: `roster: relink ${switchedRows.length} switched account${switchedRows.length > 1 ? 's' : ''}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] } } : null;
    const reset = () => {
      setEdits({});
      setAdded([]);
      setRemoved([]);
      setReplacement(null);
    };
    if (await saveSteps(env, setSave, first, second, 'Roster saved; the new handles are not', reset)) {
      reset();
      setHandles({});
    }
  };
  const input = (i: number, r: Row, k: string, label: string, type = 'text') => (!editing || gone(i) ? (r[k] ?? '') : (
    <input type={type} value={r[k] ?? ''} aria-label={`${label}, line ${lines[i]}`} aria-invalid={k === 'email' && (r.email || r.name) && !EMAIL_RE.test(r.email ?? '') ? 'true' : undefined} disabled={!!replacement}
      onInput={(e) => setCell(i, k, (e.target as HTMLInputElement).value)} />
  ));
  const role = (i: number, r: Row) => (!editing || gone(i) ? (r.role || 'enrolled') : (
    <select aria-label={`Role, line ${lines[i]}`} disabled={!!replacement} onChange={(e) => setCell(i, 'role', (e.target as HTMLSelectElement).value)}>
      <option value="enrolled" selected={(r.role || 'enrolled') === 'enrolled'}>enrolled</option>
      <option value="auditor" selected={r.role === 'auditor'}>auditor</option>
    </select>
  ));
  const handleCell = (i: number, r: Row) => {
    const sr = sysOf(r);
    if (!(sr.github_id && i < base.length)) return <td class="sys mono">{sr.github_handle || <span style="color:var(--muted)">not yet</span>}</td>;
    return (
      <td class="mono">{!editing || gone(i) ? handleOf(i) : (
        <input type="text" value={handleOf(i)} aria-label={`GitHub handle, line ${lines[i]}`} disabled={!!replacement}
          onInput={(e) => { setHandles({ ...handles, [i]: (e.target as HTMLInputElement).value }); if (save.kind !== 'busy') setSave({ kind: 'idle' }); }} />
      )}</td>
    );
  };
  /** Edit mode's per-row control: a file row is marked (Undo takes it back), a row added here goes at once. */
  const removeControl = (i: number) => {
    if (replacement) return null;
    if (gone(i)) return <button class="btn small quiet" type="button" aria-label={`Undo removing line ${lines[i]}`} onClick={() => setRemoved(removed.filter((x) => x !== i))}>Undo</button>;
    const drop = () => (i >= base.length ? setAdded(added.filter((_, j) => j !== i - base.length)) : setRemoved([...removed, i]));
    return <button class="btn small quiet rm" type="button" aria-label={`Remove line ${lines[i]}`} title="Remove" onClick={() => { drop(); if (save.kind !== 'busy') setSave({ kind: 'idle' }); }}>&times;</button>;
  };
  const d = upload.pending?.diff;
  const asgs = status.assignments ?? [];
  return (
    <>
      <div class="page-head">
        <div><h2 class="h1">Students <Hint doc="06-enrol-students-to-cohort.md">Adding a row emails that student a code, which they redeem on the semester’s join form. You edit email, name, role and the team columns; the code itself is never shown. A joined student who switched GitHub account: type their new login in GitHub handle, and the next sync moves their repos, marks and team to it.</Hint></h2></div>
        <div class="students-actions">
          {/* Send codes is the page's main action; Preview lives in its panel, not beside the button. */}
          <div class="main-action">
            <OpOpen def={sendCodes(scope, waiting)} cls="btn big" label="Send codes" />
            <Hint label="About Send codes">{SEND_CODES_HINT}</Hint>
          </div>
          <div class="actions">
            <button class="btn quiet" type="button" aria-expanded={upload.open} onClick={() => setUpload({ ...upload, open: !upload.open })}>Replace from CSV</button>
            <Hint label="About Replace from CSV">{REPLACE_CSV_HINT}</Hint>
            <a class="btn quiet" href={syncMembershipUrl(scope)} target="_blank" rel="noopener" title="Opens Sync membership on GitHub: press Run workflow there. Saving the roster starts it anyway.">Sync now</a>
          </div>
        </div>
      </div>
      <div class="stack">
        <div class="panel"><StudentCounts status={status} split={split} /></div>
        {problems.length ? <ProblemCards list={problems} /> : null}
        {upload.open ? (
          <div class="upload">
            <div class="field">
              <label for="csv-file">Replace the roster from a CSV</label>
              <input type="file" id="csv-file" accept=".csv,text/csv" onChange={(e) => void onFile((e.target as HTMLInputElement).files?.[0])} />
            </div>
            <p class="hint footnote">Needs the header <code>{ROSTER_HEADER.join(',')}</code>. You see the change before anything is saved; students already on the roster keep their codes and teams.</p>
            {upload.error ? <CheckLine cls="bad">{upload.error}</CheckLine> : null}
            {d && upload.pending ? (
              <>
                <div class="diff">
                  <b>{upload.pending.name} against the current roster</b>
                  <span class="plus">+ {d.added.length} row{d.added.length === 1 ? '' : 's'} added{d.added.length ? ': each gets a code on save' : ''}</span>
                  <span class="chg">~ {d.changed.length} row{d.changed.length === 1 ? '' : 's'} changed (name or role)</span>
                  <span class={d.removed.length ? 'chg' : ''}>− {d.removed.length} row{d.removed.length === 1 ? '' : 's'} removed{d.removed.length ? `, ${d.removed.filter((r) => sysOf(r).github_handle).length} of them joined` : ''}</span>
                  <span>{d.unchanged} unchanged.</span>
                </div>
                <div class="actions">
                  <button class="btn" type="button" onClick={() => { setReplacement(upload.pending); setUpload({ open: false, error: '', pending: null }); setSave({ kind: 'idle' }); }}>Use this roster</button>
                  <button class="btn quiet" type="button" onClick={() => setUpload({ open: false, error: '', pending: null })}>Cancel</button>
                </div>
              </>
            ) : null}
          </div>
        ) : null}
        {replacement ? <p class="note"><b>Replacing the roster from {replacement.name}.</b> Nothing is written until you save. <button class="textlink" type="button" onClick={() => setReplacement(null)}>Undo</button></p> : null}
        {file.kind === 'loading' ? <Loading what="Reading the roster" /> : null}
        {file.kind === 'absent' ? <p class="footnote">There is no {STUDENTS_FILE} yet.</p> : null}
        {file.kind === 'error' ? <CheckLine cls="bad">Could not read {STUDENTS_FILE}: {file.message}</CheckLine> : null}
        {sysFile.kind === 'error' ? <CheckLine cls="bad">Could not read {ROSTER_PATH}: {sysFile.message}. Who has joined is not shown.</CheckLine> : null}
        {missing.length ? <CheckLine cls="bad">{STUDENTS_FILE} is missing the {missing.join(', ')} column{missing.length > 1 ? 's' : ''}.</CheckLine> : null}
        {table && !missing.length ? (
          <div class="roster-box">
            {/* Edit roster: a pencil over the table's corner, a tick while editing. */}
            <button class="icon-btn roster-edit" type="button" aria-pressed={editing} aria-label={editing ? 'Done editing' : 'Edit roster'} title={editing ? 'Done editing' : 'Edit roster'} disabled={!table || !!missing.length || !!replacement} onClick={() => setEditing(!editing)}>{editing ? '✓' : '✎'}</button>
            <div class={`table-wrap roster-table${editing ? ' editing' : ''}`} style="max-height:600px;overflow:auto">
              <table class="grid" style="min-width:980px">
                <thead>
                  <tr>
                    <th>Line</th><th>Email<span class="grp">yours</span></th><th>Name<span class="grp">yours</span></th><th>Role<span class="grp">yours</span></th>
                    {teamCols.map((c) => <th>{teamHeading(c, asgs)}<span class="grp">yours: {c}</span></th>)}
                    <th class="sys">GitHub handle<span class="grp">yours once joined</span></th><th class="sys">GitHub id<span class="grp">system</span></th>
                    <th class="sys">Code sent<span class="grp">system</span></th><th class="sys">Status<span class="grp">system</span></th>
                    {editing ? <th><span class="sr">Remove</span></th> : null}
                  </tr>
                </thead>
                <tbody>
                  {current.map((r, i) => {
                    const at = lines[i];
                    const sr = sysOf(r);
                    const changed = !replacement && (edits[i] || handles[i] !== undefined || i >= base.length);
                    return (
                      <tr class={`${faultLines.has(at) ? 'fault' : ''}${gone(i) ? ' removed' : changed ? ' changed' : ''}`} id={`line-${at}`}>
                        <td class="line">{at}</td>
                        <td>{input(i, r, 'email', 'Email', 'email')}</td>
                        <td>{input(i, r, 'name', 'Name')}</td>
                        <td>{role(i, r)}</td>
                        {teamCols.map((c) => <td class="mono">{input(i, r, c, teamHeading(c, asgs))}</td>)}
                        {handleCell(i, r)}
                        <td class="sys mono">{sr.github_id}</td>
                        <td class="sys">{(sr.code_sent_at ?? '').replace('T', ', ').replace(/:\d\d(\.\d+)?Z?$/, '')}</td>
                        <td class="sys"><RowStatus r={r} sys={sr} /></td>
                        {editing ? <td class="rm-cell">{removeControl(i)}</td> : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <ul class={`roster-cards${editing ? ' editing' : ''}`}>
              {current.map((r, i) => (
                <li class={`rcard${faultLines.has(lines[i]) ? ' fault' : ''}${gone(i) ? ' removed' : ''}`}>
                  <div class="rc-top">{r.name}<span>line {lines[i]}{editing ? <> {removeControl(i)}</> : null}</span></div>
                  <div class="field"><span class="label">Email</span>{input(i, r, 'email', 'Email', 'email')}</div>
                  <div class="field"><span class="label">Role</span>{role(i, r)}</div>
                  {teamCols.map((c) => <div class="field"><span class="label">{teamHeading(c, asgs)}</span>{input(i, r, c, teamHeading(c, asgs))}</div>)}
                  <div class="rc-status"><RowStatus r={r} sys={sysOf(r)} />{sysOf(r).github_handle ? ` as ${sysOf(r).github_handle}` : ''}</div>
                </li>
              ))}
            </ul>
            <div style="margin-top:10px"><button class="btn small quiet" type="button" disabled={!!replacement} onClick={() => { setEditing(true); setAdded([...added, { email: '', name: '', role: 'enrolled' }]); }}>Add a student</button></div>
          </div>
        ) : null}
        <div class="panel" style="display:grid;gap:10px">
          <SaveBar state={save} onSave={() => void doSave()} label={saveLabel} disabled={!nEdits} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: STUDENTS_FILE, exists: file.kind !== 'absent' }} note={nEdits ? `${pendingSummary(counts)}. ${ROSTER_SAVE_NOTE}` : 'No unsaved changes'} />
        </div>
      </div>
    </>
  );
}

export function StudentsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Students">{(r) => <Students {...r} />}</WithStatus>;
}

// --------------------------------------------------------------------------- instructors

function Access({ v }: { v: boolean | null | undefined }) {
  if (v === undefined) return <span class="access"><span class="dot idle" aria-hidden="true" />Checking</span>;
  if (v === null) return <span class="access"><span class="dot idle" aria-hidden="true" />Unknown</span>;
  return v ? <span class="access"><span class="dot ok" aria-hidden="true" />Has access</span> : <span class="access"><span class="dot bad" aria-hidden="true" />Not a member</span>;
}

function dates(x: Person) {
  return `${x.start ? fmtShort(x.start) : 'start'} to ${x.end ? fmtShort(x.end) : 'end'}`;
}

/** A person as the form edits them, from semester.yml's `people:`. */
function formOf(x: Person, raw: Record<string, unknown>): Record<string, unknown> {
  return { github_handle: x.handle, email: x.email, role: x.role, name: x.name || undefined, title: x.title || undefined, photo: x.photo || undefined, url: x.url || undefined, start: x.start || undefined, end: x.end || undefined, show_email: raw.show_email === true };
}

function entryOf(v: Record<string, unknown>, raw: Record<string, unknown>): Record<string, unknown> {
  const t = (k: string) => (typeof v[k] === 'string' && (v[k] as string).trim() ? (v[k] as string).trim() : undefined);
  return {
    ...raw, github_handle: t('github_handle'), email: t('email'), role: t('role'), name: t('name'), title: t('title'), photo: t('photo'), url: t('url'), start: t('start'), end: t('end'),
    show_email: v.show_email === true ? true : raw.show_email === false ? false : undefined,
  };
}

function Instructors(p: ReadyProps) {
  const { status } = p;
  const env = useEnv();
  const [editing, setEditing] = useState<{ idx: number | 'new'; values: Record<string, unknown> } | null>(null);
  const [removed, setRemoved] = useState<number[]>([]);
  const [save, runSave, setSave] = useSave(env);
  const file = p.files.file(p.cohort.org, CONFIG_REPO, SEMESTER_FILE);
  // A semester with no semester.yml yet edits the stub: the first person added creates it.
  const sf = semesterFile(p.files, p.cohort.org);
  const text = sf && sf !== 'loading' ? sf.text : null;
  const people = text !== null ? parseInstructors(text) : [];
  const y = text !== null ? new YamlText(text) : null;
  const doc = (y && !y.errors.length ? y.toJS() : null) as { people?: Record<string, unknown>[] } | null;
  // By position, not handle: display-only entries have none.
  const rawOf = (i: number) => ((Array.isArray(doc?.people) ? doc.people : [])[i] ?? {}) as Record<string, unknown>;
  const st = status.staff;
  const ins = people.filter((x) => x.role === 'instructor').length || st?.instructors || 0;
  const tas = people.filter((x) => x.role === 'teaching_assistant').length || st?.tas || 0;
  const admins = p.course.admins;
  const scope = cohortScope(p);
  const target = { owner: p.cohort.org, repo: CONFIG_REPO, path: SEMESTER_FILE };

  const write = async (mutate: (t: YamlText) => void, what: string) => {
    if (!y || y.errors.length || !sf || sf === 'loading') return false;
    const t = new YamlText(sf.text);
    // Not checked against the exported schema: a display-only person (a name, no handle) is one
    // the schema's required keys refuse, and the engine reads it (`displayOnly`).
    mutate(t);
    return runSave(target, t.text, sf.sha, { message: `instructors: ${what}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] });
  };
  const saveForm = async () => {
    if (!editing || !env) return;
    const v = editing.values;
    const errs = fieldErrors(null, PERSON, v);
    if (Object.keys(errs).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const handle = String(v.github_handle ?? '').trim();
    const before = editing.idx === 'new' ? null : people[editing.idx];
    if (handle && (!before || !sameHandle(before.handle, handle))) {
      setSave({ kind: 'busy', text: `Checking that ${handle} exists on GitHub…` });
      let ok = false;
      try {
        ok = await env.client.userExists(handle);
      } catch {
        ok = true; // cannot tell: let the engine's check decide
      }
      if (!ok) return setSave({ kind: 'bad', text: `There is no GitHub account called ${handle}.` });
    }
    const idx = editing.idx;
    const done = await write((t) => {
      if (idx === 'new') t.set(['people', people.length], entryOf(v, {}));
      else t.assign(['people', idx], entryOf(v, rawOf(idx)));
    }, `${before ? 'edit' : 'add'} ${handle || String(v.name).trim()}`);
    if (done) setEditing(null);
  };
  const saveRemovals = async () => {
    const gone = removed.map((i) => people[i]).filter(Boolean);
    const done = await write((t) => {
      [...removed].sort((a, b) => b - a).forEach((i) => t.delete(['people', i]));
    }, `remove ${gone.map((x) => x.handle || x.name).join(', ')}`);
    if (done) setRemoved([]);
  };
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">Instructors <Hint doc="05-manage-teaching-team.md">Handles here get the instructor buttons for this semester, and emails here get the problem emails. Check instructor access makes GitHub match this list; it never removes access.</Hint></h2>
          <p class="lede">{ins} instructor{ins === 1 ? '' : 's'} and {tas} teaching assistant{tas === 1 ? '' : 's'}.{st && !st.synced ? ' GitHub access does not match this list yet.' : ''}{admins.length ? ` Course admins (${admins.join(', ')}) also have access; they are set on Course details.` : ''}</p>
        </div>
        <div class="actions">
          <OpOpen def={checkAccess(scope)} cls="btn outline" label="Check instructor access" />
          <button class="btn" type="button" disabled={!y} onClick={() => { setEditing({ idx: 'new', values: { role: 'instructor' } }); setSave({ kind: 'idle' }); }}>Add a person</button>
        </div>
      </div>
      <div class="stack">
        {file.kind === 'loading' ? <Loading what={`Reading ${SEMESTER_FILE}`} /> : null}
        {file.kind === 'absent' ? <p class="footnote">There is no {SEMESTER_FILE} yet: adding a person creates it.</p> : null}
        {file.kind === 'error' ? <CheckLine cls="bad">Could not read {SEMESTER_FILE}: {file.message}</CheckLine> : null}
        {y && y.errors.length ? <CheckLine cls="bad">{SEMESTER_FILE} does not parse ({y.errors[0]}); fix it with Edit the file directly.</CheckLine> : null}
        {people.length ? (
          <div class="table-wrap">
            <table class="grid" style="min-width:880px">
              <thead><tr><th>Name</th><th>Role</th><th>Email</th><th class="sys">Access<span class="grp">from GitHub</span></th><th>Dates</th><th>Photo</th><th /></tr></thead>
              <tbody>
                {people.map((x, i) =>
                  removed.includes(i) ? (
                    <tr class="changed"><td colSpan={7}><span>{x.name || x.handle} removed.</span> <button class="textlink" type="button" onClick={() => setRemoved(removed.filter((r) => r !== i))}>Undo</button></td></tr>
                  ) : (
                    <tr>
                      <td><b>{x.name || x.handle}</b><br /><span class="slug">{x.handle}</span></td>
                      <td>{ROLE_WORD[x.role] ?? (x.role ? `${x.role} (not a role)` : 'No role')}</td>
                      <td class="mono" style="font-size:13px">{x.email}</td>
                      <td><Access v={x.handle ? p.files.member(p.cohort.org, x.handle) : null} /></td>
                      <td class="num">{dates(x)}</td>
                      <td><PhotoThumb photo={x.photo} name={x.name || x.handle} org={p.cohort.org} /></td>
                      <td>
                        <button class="btn small quiet" type="button" onClick={() => { setEditing({ idx: i, values: formOf(x, rawOf(i)) }); setSave({ kind: 'idle' }); }}>Edit</button>
                        <button class="btn small quiet" type="button" onClick={() => setRemoved([...removed, i])}>Remove</button>
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        ) : null}
        <p class="footnote">Access states: <b>Has access</b>, <b>Not a member</b> (Check instructor access invites them). An invitation that is pending shows as not a member until it is accepted.</p>
        {removed.length && !editing ? (
          <div class="panel"><SaveBar state={save} onSave={() => void saveRemovals()} label={`Save ${removed.length} removal${removed.length > 1 ? 's' : ''}`} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: SEMESTER_FILE, exists: file.kind !== 'absent' }} /></div>
        ) : null}
        {editing ? (
          <div class="person-form">
            <h3>{editing.idx === 'new' ? 'Add a person' : `Edit ${people[editing.idx]?.name || people[editing.idx]?.handle}`}</h3>
            <SchemaForm id="p" schema={null} tiers={pick(PERSON, Object.keys(PERSON).filter((k) => k !== 'photo'))} values={editing.values} onChange={(v) => setEditing({ ...editing, values: v })} />
            <PhotoField p={p} values={editing.values} set={(photo) => setEditing({ ...editing, values: { ...editing.values, photo } })} />
            {displayOnly(editing.values) ? <CheckLine cls="warn">Display only: this person gets a card on the student site, no GitHub access and no problem emails.</CheckLine> : null}
            <SaveBar state={save} onSave={() => void saveForm()} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: SEMESTER_FILE, exists: file.kind !== 'absent' }}>
              <button class="btn quiet" type="button" onClick={() => setEditing(null)}>Cancel</button>
            </SaveBar>
          </div>
        ) : !removed.length ? (
          <>
            <SaveLine state={save} />
            <div class="savebar"><EditFile org={p.cohort.org} repo={CONFIG_REPO} path={SEMESTER_FILE} exists={file.kind !== 'absent'} /></div>
          </>
        ) : null}
      </div>
    </>
  );
}

/**
 * The Photo field: a link or a path typed in, or an image dropped on it (or chosen) that is
 * uploaded to the site repo's photo folder, the path then filled in.
 */
function PhotoField({ p, values, set }: { p: ReadyProps; values: Record<string, unknown>; set: (v: string | undefined) => void }) {
  const env = useEnv();
  const [state, setState] = useState<{ kind: 'idle' | 'busy' | 'bad'; text?: string }>({ kind: 'idle' });
  const [over, setOver] = useState(false);
  const repo = `${p.cohort.org}.github.io`;
  const dir = p.files.dir(p.cohort.org, repo, PHOTO_DIR);
  const folder = dir.kind === 'absent' ? ghUrl(p.cohort.org, repo) : ghUrl(p.cohort.org, repo, PHOTO_DIR, 'main', 'tree');
  const upload = async (f: File | undefined) => {
    if (!f || !env) return;
    const err = photoError(f);
    if (err) return setState({ kind: 'bad', text: err });
    const name = photoName(f.name);
    setState({ kind: 'busy', text: `Uploading ${name}…` });
    try {
      await env.client.putContents({ owner: p.cohort.org, repo, path: `${PHOTO_DIR}/${name}`, text: new Uint8Array(await f.arrayBuffer()), sha: null, message: `site: add the photo ${name}, from the DSL Teaching Console`, author: authorOf(env.user) });
    } catch (e) {
      const taken = e instanceof GitHubError && e.status === 422;
      return setState({ kind: 'bad', text: taken ? `${name} is already in the folder: rename the image, or type its path.` : `Not uploaded: ${e instanceof Error ? e.message : String(e)}` });
    }
    setState({ kind: 'idle' });
    set(`/${PHOTO_DIR}/${name}`);
  };
  return (
    <div
      class={`photo-drop${over ? ' over' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); void upload(e.dataTransfer?.files[0]); }}
    >
      <Field id="p-photo" k="photo" t={PERSON.photo} value={values.photo} values={values} error={fieldErrors(null, { photo: PERSON.photo }, values).photo} set={(_, v) => set(v === undefined ? undefined : String(v))} />
      <p class="why">
        Drop an image here, or <label class="textlink" for="p-photo-file">choose one</label>, to upload it to the site repo’s <a class="textlink" href={folder} target="_blank" rel="noopener">photo folder</a> (PNG, JPEG or WebP, up to 2 MB).
        <input type="file" id="p-photo-file" class="sr" accept="image/png,image/jpeg,image/webp" onChange={(e) => void upload((e.target as HTMLInputElement).files?.[0])} />
      </p>
      {state.kind === 'busy' ? <CheckLine cls="busy">{state.text}</CheckLine> : state.kind === 'bad' ? <CheckLine cls="bad">{state.text}</CheckLine> : null}
    </div>
  );
}

/**
 * A person's photo as a small round thumbnail: an absolute URL as is; a path in the site repo
 * (`/_images/pp/x.jpg`, or a bare name in the photo folder) read through the API as the site would
 * show it; the initials, muted, when there is none or it does not load.
 */
const SEND_CODES_HINT =
  'Every student on the roster gets a private enrolment code by email the moment their row is saved; they paste it on the semester’s join form to get their repos. This button sends a NEW code to everyone who has not joined yet (their old code stops working) and emails it again. Preview first to see how many that is.';
const REPLACE_CSV_HINT =
  'Upload a CSV from the registrar to replace the roster in one go: you see a diff of added, removed and changed rows before anything is saved. Columns the toolkit fills (GitHub handle, code, when it was sent) are kept for rows that stay.';

export function PhotoThumb({ photo, name, org }: { photo: string; name: string; org: string }) {
  const env = useEnv();
  const [failed, setFailed] = useState(false);
  const raw = photo.trim();
  const absolute = /^https?:\/\//.test(raw);
  const path = !raw || absolute ? '' : raw.startsWith('/') ? raw : `/${PHOTO_DIR}/${raw}`;
  const load = useLoad(env && path ? () => studentData(env.client).picture(org, pictureOf(path, org)) : null, [path, org]);
  const src = absolute ? raw : load.kind === 'ready' ? load.value : '';
  if (!src || failed) return <span class={`thumb${raw ? '' : ' none'}`} role="img" aria-label={raw ? name : `${name}: no photo`}>{initials(name)}</span>;
  return <img class="thumb" src={src} alt={name} onError={() => setFailed(true)} />;
}

export function InstructorsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Instructors">{(r) => <Instructors {...r} />}</WithStatus>;
}
