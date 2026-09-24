// S8 Students (the roster grid, Replace from CSV) and S7 Instructors (instructors.yml).

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { EMAIL_RE, diffRoster, missingColumns, readTable, writeTable, type RosterDiff } from '../edit/csv';
import { useSave } from '../edit/save';
import { YamlText } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { fmtShort } from '../model/format';
import { ROLE_WORD, ROSTER_HEADER, parseInstructors, type Person } from '../model/people';
import { checkAccess, sendCodes } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import { PERSON, displayOnly } from '../tiers/people';
import { CheckLine, Crumbs, EditFile, Help, Lives, Loading, ProblemCards } from '../ui/bits';
import { SaveBar, SaveLine } from '../ui/edit';
import { Lock } from '../ui/icons';
import { StudentCounts } from './Cohort';
import { WithStatus, cohortCrumbs, cohortScope } from './common';
import type { CohortProps, ReadyProps } from './types';
import { CONFIG_REPO, INSTRUCTORS_FILE } from '../model/names';

type Row = Record<string, string>;
const YOURS = ['hertie_email', 'name', 'role'] as const;

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
  const current: Row[] = replacement ? replacement.rows : [...base.map((r, i) => ({ ...r, ...(edits[i + 2] ?? {}) })), ...added];
  const nEdits = replacement ? 1 : Object.keys(edits).filter((l) => YOURS.some((k) => (edits[+l][k] ?? '') !== (base[+l - 2]?.[k] ?? ''))).length + added.filter((r) => r.hertie_email || r.name).length;
  const badEmail = current.filter((r) => (r.hertie_email || r.name) && !EMAIL_RE.test(r.hertie_email ?? ''));
  const toSend = current.filter((r) => !r.code_sent_at && EMAIL_RE.test(r.hertie_email ?? '')).length - base.filter((r) => !r.code_sent_at && EMAIL_RE.test(r.hertie_email ?? '')).length;
  const saveLabel = !nEdits ? 'Save' : toSend > 0 ? `Save and send ${toSend} code${toSend > 1 ? 's' : ''}` : `Save ${nEdits} change${nEdits > 1 ? 's' : ''}`;

  const setCell = (line: number, k: string, v: string) => {
    setEdits({ ...edits, [line]: { ...(edits[line] ?? {}), [k]: v } });
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
    const rows = current.filter((r) => r.hertie_email || r.name);
    const text = writeTable({ header: table.header, rows });
    const ok = await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: 'students.csv' }, text, file.sha, { message: `roster: ${replacement ? `replace from ${replacement.name}` : `${nEdits} change${nEdits > 1 ? 's' : ''}`}, from the Instructor Console`, statusRepo: [p.cohort.org, CONFIG_REPO] });
    if (ok) {
      setEdits({});
      setAdded([]);
      setReplacement(null);
    }
  };
  const input = (line: number, r: Row, k: string, label: string, type = 'text') => (
    <input type={type} value={r[k] ?? ''} aria-label={`${label}, line ${line}`} aria-invalid={k === 'hertie_email' && (r.hertie_email || r.name) && !EMAIL_RE.test(r.hertie_email ?? '') ? 'true' : undefined} disabled={!!replacement}
      onInput={(e) => (line > base.length + 1 ? setAdded(added.map((x, i) => (i === line - base.length - 2 ? { ...x, [k]: (e.target as HTMLInputElement).value } : x))) : setCell(line, k, (e.target as HTMLInputElement).value))} />
  );
  const role = (line: number, r: Row) => (
    <select aria-label={`Role, line ${line}`} disabled={!!replacement} onChange={(e) => (line > base.length + 1 ? setAdded(added.map((x, i) => (i === line - base.length - 2 ? { ...x, role: (e.target as HTMLSelectElement).value } : x))) : setCell(line, 'role', (e.target as HTMLSelectElement).value))}>
      <option value="enrolled" selected={(r.role || 'enrolled') === 'enrolled'}>enrolled</option>
      <option value="auditor" selected={r.role === 'auditor'}>auditor</option>
    </select>
  );
  const d = upload.pending?.diff;
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Students')} />
      <div class="page-head">
        <div><h1>Students</h1><p class="lede">{s.rows} on the roster. {s.codes_sent} codes sent; {s.joined} joined.</p></div>
        <div class="actions">
          <button class="btn quiet" type="button" aria-expanded={upload.open} onClick={() => setUpload({ ...upload, open: !upload.open })}>Replace from CSV</button>
          <OpButtons def={sendCodes(scope, waiting)} label="Send new codes to students who have not joined" />
        </div>
      </div>
      <Help title="How joining works" doc="06-enrol-students-to-cohort.md">
        <p>Adding a row emails that student a code. They redeem it on the semester’s join form; you see them turn green here.</p>
        <p>You edit email, name and role. The system columns are written when a student joins; the code itself is never shown.</p>
      </Help>
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
                    <th class="sys">GitHub handle<span class="grp">system <Lock /></span></th><th class="sys">GitHub id<span class="grp">system</span></th>
                    <th class="sys">Code sent<span class="grp">system</span></th><th class="sys">Status<span class="grp">system</span></th>
                  </tr>
                </thead>
                <tbody>
                  {current.map((r, i) => {
                    const line = i + 2;
                    const changed = !replacement && (edits[line] || i >= base.length);
                    return (
                      <tr class={`${faultLines.has(line) ? 'fault' : ''}${changed ? ' changed' : ''}`} id={`line-${line}`}>
                        <td class="line">{line}</td>
                        <td>{input(line, r, 'hertie_email', 'Email', 'email')}</td>
                        <td>{input(line, r, 'name', 'Name')}</td>
                        <td>{role(line, r)}</td>
                        <td class="sys mono">{r.github_handle || <span style="color:var(--muted)">not yet</span>}</td>
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
                <li class={`rcard${faultLines.has(i + 2) ? ' fault' : ''}`}>
                  <div class="rc-top">{r.name}<span>line {i + 2}</span></div>
                  <div class="field"><span class="label">Email</span>{input(i + 2, r, 'hertie_email', 'Email', 'email')}</div>
                  <div class="field"><span class="label">Role</span>{role(i + 2, r)}</div>
                  <div class="rc-status"><RowStatus r={r} />{r.github_handle ? ` as ${r.github_handle}` : ''}</div>
                </li>
              ))}
            </ul>
            <div style="margin-top:10px"><button class="btn small quiet" type="button" disabled={!!replacement} onClick={() => setAdded([...added, { hertie_email: '', name: '', role: 'enrolled' }])}>Add a student</button></div>
          </div>
        ) : null}
        <div class="panel" style="display:grid;gap:10px">
          <SaveBar state={save} onSave={() => void doSave()} label={saveLabel} disabled={!nEdits} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: 'students.csv' }} note={nEdits ? `${nEdits} unsaved change${nEdits > 1 ? 's' : ''}` : 'No unsaved changes'} />
          <Lives org={p.cohort.org} repo={CONFIG_REPO} path="students.csv" />
        </div>
      </div>
    </>
  );
}

export function StudentsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Students" crumbs={cohortCrumbs(p, 'Students')}>{(r) => <Students {...r} />}</WithStatus>;
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
    return runSave(target, t.text, file.sha, { message: `instructors: ${what}, from the Instructor Console`, statusRepo: [p.cohort.org, CONFIG_REPO] });
  };
  const saveForm = async () => {
    if (!editing || !env) return;
    const v = editing.values;
    const errs = fieldErrors(null, PERSON, v);
    if (Object.keys(errs).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const handle = String(v.github_handle ?? '').trim();
    const before = editing.idx === 'new' ? null : people[editing.idx];
    if (handle && (!before || before.handle.toLowerCase() !== handle.toLowerCase())) {
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
      <Crumbs items={cohortCrumbs(p, 'Instructors')} />
      <div class="page-head">
        <div>
          <h1>Instructors</h1>
          <p class="lede">{ins} instructor{ins === 1 ? '' : 's'} and {tas} teaching assistant{tas === 1 ? '' : 's'}.{st && !st.synced ? ' GitHub access does not match this list yet.' : ''}</p>
        </div>
        <div class="actions">
          <OpOpen def={checkAccess(scope)} cls="btn outline" label="Check instructor access" />
          <button class="btn" type="button" disabled={!y} onClick={() => { setEditing({ idx: 'new', values: { role: 'instructor' } }); setSave({ kind: 'idle' }); }}>Add a person</button>
        </div>
      </div>
      <Help title="Who is an instructor" doc="05-manage-teaching-team.md">
        <p>Handles here get the instructor buttons for this semester; emails here get the problem emails. Check instructor access makes GitHub match this list; it never removes access.</p>
        {admins.length ? <p>Course admins ({admins.join(', ')}) have access to every semester of the course; they are set on Course details.</p> : null}
      </Help>
      <div class="stack">
        {file.kind === 'loading' ? <Loading what={`Reading ${INSTRUCTORS_FILE}`} /> : null}
        {file.kind === 'absent' ? <p class="footnote">There is no {INSTRUCTORS_FILE} yet.</p> : null}
        {y && y.errors.length ? <CheckLine cls="bad">{INSTRUCTORS_FILE} does not parse ({y.errors[0]}); fix it with Edit the file.</CheckLine> : null}
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
          <div class="panel"><SaveBar state={save} onSave={() => void saveRemovals()} label={`Save ${removed.length} removal${removed.length > 1 ? 's' : ''}`} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: INSTRUCTORS_FILE }} /></div>
        ) : null}
        {editing ? (
          <div class="person-form">
            <h3>{editing.idx === 'new' ? 'Add a person' : `Edit ${people[editing.idx]?.name || people[editing.idx]?.handle}`}</h3>
            <SchemaForm id="p" schema={null} tiers={PERSON} values={editing.values} onChange={(v) => setEditing({ ...editing, values: v })} />
            {displayOnly(editing.values) ? <CheckLine cls="warn">Display only: this person gets a card on the student site, no GitHub access and no problem emails.</CheckLine> : null}
            <SaveBar state={save} onSave={() => void saveForm()} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: INSTRUCTORS_FILE }}>
              <button class="btn quiet" type="button" onClick={() => setEditing(null)}>Cancel</button>
            </SaveBar>
          </div>
        ) : !removed.length ? (
          <>
            <SaveLine state={save} />
            <div class="savebar"><EditFile org={p.cohort.org} repo={CONFIG_REPO} path={INSTRUCTORS_FILE} /></div>
          </>
        ) : null}
        <Lives org={p.cohort.org} repo={CONFIG_REPO} path={INSTRUCTORS_FILE} />
      </div>
    </>
  );
}

export function InstructorsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Instructors" crumbs={cohortCrumbs(p, 'Instructors')}>{(r) => <Instructors {...r} />}</WithStatus>;
}
