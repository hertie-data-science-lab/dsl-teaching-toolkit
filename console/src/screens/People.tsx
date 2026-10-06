// S8 Students (the roster grid, Replace from CSV) and S7 Instructors (instructors.yml).

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { EMAIL_RE, diffRoster, missingColumns, readTable, writeTable, type RosterDiff } from '../edit/csv';
import { useSave } from '../edit/save';
import { YamlText } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { fmtShort } from '../model/format';
import { ROLE_WORD, ROSTER_HEADER, parseInstructors, sameHandle, type Person } from '../model/people';
import { checkAccess, sendCodes } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import { PERSON, displayOnly } from '../tiers/people';
import { CheckLine, EditFile, Lives, Loading, ProblemCards } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveBar, SaveLine } from '../ui/edit';
import { StudentCounts } from './Cohort';
import { WithStatus, cohortScope } from './common';
import type { CohortProps, ReadyProps } from './types';
import { CONFIG_REPO, INSTRUCTORS_FILE } from '../model/names';

type Row = Record<string, string>;
const YOURS = ['hertie_email', 'name', 'role'] as const;
// A joined row's handle is yours too: a student who switched GitHub account is re-pointed
// here, and the next membership sync moves their repos, marks and team to it (`relink`).
const EDITED = [...YOURS, 'github_handle'] as const;

function RowStatus({ r }: { r: Row }) {
  if (!r.code_sent_at) return <span class="notsent">Not sent{r.hertie_email && !EMAIL_RE.test(r.hertie_email) ? ': email has no domain' : ''}</span>;
  if (r.github_handle) return <span class="joined"><span class="dot ok" aria-hidden="true" />Joined</span>;
  return <span class="joined"><span class="dot idle" aria-hidden="true" />Code sent; not joined</span>;
}

function Students(p: ReadyProps) {
  const { status } = p;
  const env = useEnv();
  const s = status.students ?? { rows: 0, codes_sent: 0, joined: 0 };
  const file = p.files.file(p.cohort.org, CONFIG_REPO, 'students.csv');
  const [edits, setEdits] = useState<Record<number, Row>>({});
  const [added, setAdded] = useState<Row[]>([]);
  const [replacement, setReplacement] = useState<{ rows: Row[]; diff: RosterDiff; name: string } | null>(null);
  const [upload, setUpload] = useState<{ open: boolean; error: string; pending: { rows: Row[]; diff: RosterDiff; name: string } | null }>({ open: false, error: '', pending: null });
  const [save, runSave, setSave] = useSave(env);
  const table = file.kind === 'ready' ? readTable(file.text) : null;
  const missing = table ? missingColumns(table.header, [...ROSTER_HEADER]) : [];
  const faultLines = new Set((status.problems ?? []).filter((x) => x.stage === 'K5' && x.fix?.line).map((x) => x.fix!.line!));
  const problems = (status.problems ?? []).filter((x) => x.stage === 'K5');
  const scope = cohortScope(p);
  const waiting = Math.max(0, s.codes_sent - s.joined);

  const base = table?.rows ?? [];
  // Edits are keyed by row index. `lines` is each row's line in the file (blank lines and quoted
  // newlines move it), shown and matched with the engine's faults; a row added here, or a
  // replacement's, is numbered on from the file's last row.
  const current: Row[] = replacement ? replacement.rows : [...base.map((r, i) => ({ ...r, ...(edits[i] ?? {}) })), ...added];
  const fileLines = replacement ? [] : (table?.lines ?? []);
  const lastLine = fileLines[fileLines.length - 1] ?? 1;
  const lines = current.map((_, i) => fileLines[i] ?? lastLine + i - fileLines.length + 1);
  const nEdits = replacement ? 1 : Object.keys(edits).filter((i) => EDITED.some((k) => (edits[+i][k] ?? '') !== (base[+i]?.[k] ?? ''))).length + added.filter((r) => r.hertie_email || r.name).length;
  const badEmail = current.filter((r) => (r.hertie_email || r.name) && !EMAIL_RE.test(r.hertie_email ?? ''));
  const toSend = current.filter((r) => !r.code_sent_at && EMAIL_RE.test(r.hertie_email ?? '')).length - base.filter((r) => !r.code_sent_at && EMAIL_RE.test(r.hertie_email ?? '')).length;
  const saveLabel = !nEdits ? 'Save' : toSend > 0 ? `Save and send ${toSend} code${toSend > 1 ? 's' : ''}` : `Save ${nEdits} change${nEdits > 1 ? 's' : ''}`;

  /** Row `i`'s `k`: an edit of a file row, or the text of a row added here. */
  const setCell = (i: number, k: string, v: string) => {
    if (i >= base.length) return setAdded(added.map((x, j) => (j === i - base.length ? { ...x, [k]: v } : x)));
    setEdits({ ...edits, [i]: { ...(edits[i] ?? {}), [k]: v } });
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
    if (badEmail.length) return setSave({ kind: 'bad', text: `${badEmail.length} row${badEmail.length > 1 ? 's have' : ' has'} an email that is not an address; no code can be sent to it.` });
    const switched = replacement ? [] : base.map((r, i) => (edits[i]?.github_handle ?? r.github_handle ?? '').trim()).filter((h, i) => !sameHandle(h, base[i].github_handle ?? ''));
    if (switched.includes('')) return setSave({ kind: 'bad', text: 'A joined student’s GitHub handle cannot be blank. Put back the old one, or type their new account.' });
    for (const handle of switched) {
      setSave({ kind: 'busy', text: `Checking that ${handle} exists on GitHub…` });
      let ok = false;
      try {
        ok = env ? await env.client.userExists(handle) : true;
      } catch {
        ok = true; // cannot tell: the sync checks the account itself
      }
      if (!ok) return setSave({ kind: 'bad', text: `There is no GitHub account called ${handle}.` });
    }
    const rows = current.filter((r) => r.hertie_email || r.name);
    const text = writeTable({ header: table.header, rows });
    const ok = await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: 'students.csv' }, text, file.sha, { message: `roster: ${replacement ? `replace from ${replacement.name}` : `${nEdits} change${nEdits > 1 ? 's' : ''}`}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] });
    if (ok) {
      setEdits({});
      setAdded([]);
      setReplacement(null);
    }
  };
  const input = (i: number, r: Row, k: string, label: string, type = 'text') => (
    <input type={type} value={r[k] ?? ''} aria-label={`${label}, line ${lines[i]}`} aria-invalid={k === 'hertie_email' && (r.hertie_email || r.name) && !EMAIL_RE.test(r.hertie_email ?? '') ? 'true' : undefined} disabled={!!replacement}
      onInput={(e) => setCell(i, k, (e.target as HTMLInputElement).value)} />
  );
  const role = (i: number, r: Row) => (
    <select aria-label={`Role, line ${lines[i]}`} disabled={!!replacement} onChange={(e) => setCell(i, 'role', (e.target as HTMLSelectElement).value)}>
      <option value="enrolled" selected={(r.role || 'enrolled') === 'enrolled'}>enrolled</option>
      <option value="auditor" selected={r.role === 'auditor'}>auditor</option>
    </select>
  );
  const d = upload.pending?.diff;
  return (
    <>
      <div class="page-head">
        <div><h2 class="h1">Students <Hint doc="06-enrol-students-to-cohort.md">Adding a row emails that student a code, which they redeem on the semester’s join form. You edit email, name and role; the code itself is never shown. A joined student who switched GitHub account: type their new login in GitHub handle, and the next sync moves their repos, marks and team to it.</Hint></h2><p class="lede">{s.rows} on the roster. {s.codes_sent} codes sent; {s.joined} joined.</p></div>
        <div class="actions">
          <button class="btn quiet" type="button" aria-expanded={upload.open} onClick={() => setUpload({ ...upload, open: !upload.open })}>Replace from CSV</button>
          <OpButtons def={sendCodes(scope, waiting)} label="Send new codes to students who have not joined" />
        </div>
      </div>
      <div class="stack">
        <div class="panel"><StudentCounts status={status} /></div>
        {problems.length ? <ProblemCards list={problems} /> : null}
        {upload.open ? (
          <div class="upload">
            <div class="field">
              <label for="csv-file">Replace the roster from a CSV</label>
              <input type="file" id="csv-file" accept=".csv,text/csv" onChange={(e) => void onFile((e.target as HTMLInputElement).files?.[0])} />
            </div>
            <p class="hint footnote">Needs the header <code>{ROSTER_HEADER.join(',')}</code>. You see the change before anything is saved; students already on the roster keep their codes.</p>
            {upload.error ? <CheckLine cls="bad">{upload.error}</CheckLine> : null}
            {d && upload.pending ? (
              <>
                <div class="diff">
                  <b>{upload.pending.name} against the current roster</b>
                  <span class="plus">+ {d.added.length} row{d.added.length === 1 ? '' : 's'} added{d.added.length ? ': each gets a code on save' : ''}</span>
                  <span class="chg">~ {d.changed.length} row{d.changed.length === 1 ? '' : 's'} changed (name or role)</span>
                  <span class={d.removed.length ? 'chg' : ''}>− {d.removed.length} row{d.removed.length === 1 ? '' : 's'} removed{d.removed.length ? `, ${d.removed.filter((r) => r.github_handle).length} of them joined` : ''}</span>
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
        {file.kind === 'absent' ? <p class="footnote">There is no students.csv yet.</p> : null}
        {file.kind === 'error' ? <CheckLine cls="bad">Could not read students.csv: {file.message}</CheckLine> : null}
        {missing.length ? <CheckLine cls="bad">students.csv is missing the {missing.join(', ')} column{missing.length > 1 ? 's' : ''}.</CheckLine> : null}
        {table && !missing.length ? (
          <div>
            <div class="table-wrap roster-table" style="max-height:600px;overflow:auto">
              <table class="grid" style="min-width:980px">
                <thead>
                  <tr>
                    <th>Line</th><th>Email<span class="grp">yours: hertie_email</span></th><th>Name<span class="grp">yours</span></th><th>Role<span class="grp">yours</span></th>
                    <th class="sys">GitHub handle<span class="grp">yours once joined</span></th><th class="sys">GitHub id<span class="grp">system</span></th>
                    <th class="sys">Code sent<span class="grp">system</span></th><th class="sys">Status<span class="grp">system</span></th>
                  </tr>
                </thead>
                <tbody>
                  {current.map((r, i) => {
                    const at = lines[i];
                    const changed = !replacement && (edits[i] || i >= base.length);
                    return (
                      <tr class={`${faultLines.has(at) ? 'fault' : ''}${changed ? ' changed' : ''}`} id={`line-${at}`}>
                        <td class="line">{at}</td>
                        <td>{input(i, r, 'hertie_email', 'Email', 'email')}</td>
                        <td>{input(i, r, 'name', 'Name')}</td>
                        <td>{role(i, r)}</td>
                        {r.github_id && i < base.length ? <td class="mono">{input(i, r, 'github_handle', 'GitHub handle')}</td> : <td class="sys mono">{r.github_handle || <span style="color:var(--muted)">not yet</span>}</td>}
                        <td class="sys mono">{r.github_id}</td>
                        <td class="sys">{(r.code_sent_at ?? '').replace('T', ', ').replace(/:\d\d(\.\d+)?Z?$/, '')}</td>
                        <td class="sys"><RowStatus r={r} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <ul class="roster-cards">
              {current.map((r, i) => (
                <li class={`rcard${faultLines.has(lines[i]) ? ' fault' : ''}`}>
                  <div class="rc-top">{r.name}<span>line {lines[i]}</span></div>
                  <div class="field"><span class="label">Email</span>{input(i, r, 'hertie_email', 'Email', 'email')}</div>
                  <div class="field"><span class="label">Role</span>{role(i, r)}</div>
                  <div class="rc-status"><RowStatus r={r} />{r.github_handle ? ` as ${r.github_handle}` : ''}</div>
                </li>
              ))}
            </ul>
            <div style="margin-top:10px"><button class="btn small quiet" type="button" disabled={!!replacement} onClick={() => setAdded([...added, { hertie_email: '', name: '', role: 'enrolled' }])}>Add a student</button></div>
          </div>
        ) : null}
        <div class="panel" style="display:grid;gap:10px">
          <SaveBar state={save} onSave={() => void doSave()} label={saveLabel} disabled={!nEdits} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: 'students.csv', exists: file.kind !== 'absent' }} note={nEdits ? `${nEdits} unsaved change${nEdits > 1 ? 's' : ''}` : 'No unsaved changes'} />
          <Lives org={p.cohort.org} repo={CONFIG_REPO} path="students.csv" exists={file.kind !== 'absent'} />
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

/** A person as the form edits them, from instructors.yml. */
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
  const file = p.files.file(p.cohort.org, CONFIG_REPO, INSTRUCTORS_FILE);
  const people = file.kind === 'ready' ? parseInstructors(file.text) : [];
  const y = file.kind === 'ready' ? new YamlText(file.text) : null;
  const doc = (y && !y.errors.length ? y.toJS() : null) as { instructors?: Record<string, unknown>[] } | null;
  // By position, not handle: display-only entries have none.
  const rawOf = (i: number) => ((doc?.instructors ?? [])[i] ?? {}) as Record<string, unknown>;
  const st = status.staff;
  const ins = people.filter((x) => x.role === 'instructor').length || st?.instructors || 0;
  const tas = people.filter((x) => x.role === 'teaching_assistant').length || st?.tas || 0;
  const admins = p.course.admins;
  const scope = cohortScope(p);
  const target = { owner: p.cohort.org, repo: CONFIG_REPO, path: INSTRUCTORS_FILE };

  const write = async (mutate: (t: YamlText) => void, what: string) => {
    if (!y || file.kind !== 'ready') return false;
    const t = new YamlText(file.text);
    mutate(t);
    return runSave(target, t.text, file.sha, { message: `instructors: ${what}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] });
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
      if (idx === 'new') t.set(['instructors', people.length], entryOf(v, {}));
      else t.assign(['instructors', idx], entryOf(v, rawOf(idx)));
    }, `${before ? 'edit' : 'add'} ${handle || String(v.name).trim()}`);
    if (done) setEditing(null);
  };
  const saveRemovals = async () => {
    const gone = removed.map((i) => people[i]).filter(Boolean);
    const done = await write((t) => {
      [...removed].sort((a, b) => b - a).forEach((i) => t.delete(['instructors', i]));
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
        {file.kind === 'loading' ? <Loading what={`Reading ${INSTRUCTORS_FILE}`} /> : null}
        {file.kind === 'absent' ? <p class="footnote">There is no {INSTRUCTORS_FILE} yet.</p> : null}
        {y && y.errors.length ? <CheckLine cls="bad">{INSTRUCTORS_FILE} does not parse ({y.errors[0]}); fix it with Edit the file directly.</CheckLine> : null}
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
                      <td>{x.photo ? <span class="chip ok">Photo</span> : <span class="chip">No photo</span>}</td>
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
          <div class="panel"><SaveBar state={save} onSave={() => void saveRemovals()} label={`Save ${removed.length} removal${removed.length > 1 ? 's' : ''}`} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: INSTRUCTORS_FILE, exists: file.kind !== 'absent' }} /></div>
        ) : null}
        {editing ? (
          <div class="person-form">
            <h3>{editing.idx === 'new' ? 'Add a person' : `Edit ${people[editing.idx]?.name || people[editing.idx]?.handle}`}</h3>
            <SchemaForm id="p" schema={null} tiers={PERSON} values={editing.values} onChange={(v) => setEditing({ ...editing, values: v })} />
            {displayOnly(editing.values) ? <CheckLine cls="warn">Display only: this person gets a card on the student site, no GitHub access and no problem emails.</CheckLine> : null}
            <SaveBar state={save} onSave={() => void saveForm()} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: INSTRUCTORS_FILE, exists: file.kind !== 'absent' }}>
              <button class="btn quiet" type="button" onClick={() => setEditing(null)}>Cancel</button>
            </SaveBar>
          </div>
        ) : !removed.length ? (
          <>
            <SaveLine state={save} />
            <div class="savebar"><EditFile org={p.cohort.org} repo={CONFIG_REPO} path={INSTRUCTORS_FILE} exists={file.kind !== 'absent'} /></div>
          </>
        ) : null}
        <Lives org={p.cohort.org} repo={CONFIG_REPO} path={INSTRUCTORS_FILE} exists={file.kind !== 'absent'} />
      </div>
    </>
  );
}

export function InstructorsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Instructors">{(r) => <Instructors {...r} />}</WithStatus>;
}
