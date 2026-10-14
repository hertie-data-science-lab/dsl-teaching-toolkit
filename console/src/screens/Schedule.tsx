// S6 Schedule with its editor (one form per kind, decision 0038 and the forms mock-up v3) and S11 Release detail.

import { useMemo, useState } from 'preact/hooks';
import { parse } from 'yaml';
import { useEnv } from '../env';
import { invalidText, saveSteps, type SaveState, type Step } from '../edit/save';
import { YamlText, deepEqual, obj } from '../edit/yamlText';
import { Invalid } from '../forms/Form';
import { RELEASE_WORD, TYPE_CLASS, siteRowWord, fmtDay, fmtTime, fmtWhen, heldSentence, markWord, releaseIdent, repoWords, sortKey } from '../model/format';
import { needsANumber, parseSchedule, scheduleRows, type Block, type Row } from '../model/schedule';
import {
  ASSIGNMENTS_BLOCK, READINGS_KIND, SESSION_KINDS, applyChanges, archiveFallbackWords, blockOf, changesOf, defaultReleaseAt, draftErrors, emptyTwin, hiddenKind, newDraft, nextNumber, nextSession, numberedField, readDraft, releaseAt, writeDraft,
  type AssignmentDraft, type Draft, type EntryDraft, type FileDraft, type SemesterDraft, type SemesterEntryDraft, type SemesterType, type Slot,
} from '../model/scheduleEdit';
import type { Assignment, Release } from '../model/types';
import { archive, keepFuture, releaseAdhoc, releaseAgain, releaseEarly, releaseNow, scheduledPreview } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import { TIMEZONES } from '../tiers/course';
import { RUN_REASON } from '../tiers/runSettings';
import { CheckLine, DueSubmit, EditFile, Md, ProblemCards, ReleaseMarks, ghUrl } from '../ui/bits';
import { FilePicker, pathVerdict } from '../ui/FilePicker';
import { Hint } from '../ui/Hint';
import { SaveLine, UnsavedBar, lineOf } from '../ui/edit';
import { releaseMarks, releaseProblems, templateReadinessIn, tier, type ReleaseMark, type Tiered } from '../model/readiness';
import { NOTHING_TO_RELEASE, markClass, releaseRef, rowMark } from './Cohort';
import { NotFound } from './Assignments';
import { CheckNow, WithStatus, cohortScope, gradingConfig, todayOf, tzOf, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';
import { ASSIGNMENTS_FILE, CONFIG_REPO } from '../model/names';
import { RUN_KEYS, TEAM_FORMATION, TEAM_FORMATIONS, VISIBILITIES, VISIBILITY, assignmentsFile, resolve, validAssignments, validSchedule } from '../model/cascade';
import { SolutionNote, assignmentsAfterSchedule, cutoffOf, forcedVisibility, semesterLayers } from './RunSettings';
import { DEFAULT_DEST_REPO, DEFAULT_TIMEZONE, KINDS, SUBMIT_VIA_DEFAULT, penaltyError, pluralOf } from '../model/policy';
import { otherRepos } from './CourseIndex';

const LABELS: Record<Block, string> = { releases: 'Releases', assignments: 'Assignments', events: 'Events' };

/** The address an external assignment names, once handed out: the site links it only then. */
function submitUrlOnce(a: Assignment, now: number): string | undefined {
  const url = a.settings?.submit_url?.value;
  return typeof url === 'string' && url && a.handout && Date.parse(a.handout) <= now ? url : undefined;
}

/** What the student site shows in the Details cell; `a`, the due row's assignment, says where to submit as the site does. */
function Details({ r, a, now }: { r: Row; a?: Assignment; now: number }) {
  if (r.entry === 'semester') return <span class="det" />;
  if (!r.show) {
    return (
      <span class="det">
        <span class="prop">Not shown</span> <Hint small label="About Not shown">This entry is not displayed on the student site or in the student console. The engine still acts on it (a hidden release still ships at its time). Edit the entry to show it.</Hint>
      </span>
    );
  }
  return (
    <span class="det">
      <Md src={r.details} />
      {r.block === 'releases' && r.state !== 'released' ? <p class="gen">Materials for {r.ident} are not yet released.</p> : null}
      {r.type === 'due' && a?.submit_shape ? <DueSubmit shape={a.submit_shape} slug={a.semester_name ?? a.slug} group={a.teams !== null} url={submitUrlOnce(a, now)} /> : null}
    </span>
  );
}

// ------------------------------------------------------------------ the file

interface SchedFile {
  text: string;
  sha: string;
  doc: Record<string, unknown>;
  error: string | null;
}

export function useSchedFile(p: CohortProps): SchedFile | null | 'loading' {
  const f = p.files.file(p.cohort.org, CONFIG_REPO, 'schedule.yml');
  const text = f.kind === 'ready' ? f.text : null;
  const parsed = useMemo(() => {
    if (text === null) return null;
    const y = new YamlText(text);
    const errors = y.errors;
    const doc = errors.length ? {} : ((y.toJS() ?? {}) as Record<string, unknown>);
    return { doc: doc && typeof doc === 'object' ? doc : {}, error: errors[0] ?? null };
  }, [text]);
  if (f.kind === 'loading') return 'loading';
  if (f.kind !== 'ready') return null;
  return { text: f.text, sha: f.sha, ...parsed! };
}

/** schedule.yml after the semester's dates sheet (`drafts`), or why it would not be valid. */
export function scheduleAfter(sf: SchedFile, drafts: SemesterDraft[]): { text: string } | { error: string } {
  const y = new YamlText(sf.text);
  for (const d of drafts) writeDraft(y, d, sf.doc);
  return validSchedule(y.toJS()) ? { text: y.text } : { error: invalidText('the schedule', validSchedule) };
}

/** The Schedule's Check `?`: what Check semester setup looks at. */
export const SCHEDULE_CHECK_HINT = 'Check re-reads the schedule, the course repos and the templates and reports anything that would stop a release, hand-out, collection or return from running: entries that do not parse, dates out of order, a release whose folder is missing, a template whose grading config is invalid. It changes nothing and sends nothing.';

/** Every repo a file may come from: the materials repos, then the course's Other repos; `unlisted` when the listing failed. */
export function sourceRepos(p: ReadyProps, materials: string[]): { materials: string[]; others: string[]; unlisted: boolean } {
  const listing = p.files.repos(p.course.org);
  const known = [...materials, ...(p.status.course?.templates ?? []).map((t) => t.repo)];
  return { materials, others: listing.kind === 'ready' ? otherRepos(p.course.org, listing.repos, known).map((r) => r.name) : [], unlisted: listing.kind === 'error' };
}

// ------------------------------------------------------------------ the forms' words

/** Every `?` and hint the forms show, verbatim from the forms review and mock-up v3. */
export const FORM_TEXT = {
  files: 'Copies run AT the release time, not now: whatever is in the folder then is what students get. The path does not have to exist yet: schedule a folder you are still writing, or a file you will add later.',
  filesBullets: [
    'Slides, notebooks, code. Copying runs at the release time: the folder or file need not exist or be finished yet.',
    'Put associated readings under Readings below so they land on the Readings tab and never on the public site.',
    'Nothing to ship? Leave this empty: the session still appears.',
  ],
  number: 'The row word: Lecture 3. Same as the session unless this kind counts on its own (Maths labs start a week late).',
  session: 'The teaching unit: a lecture, its lab and its readings share one session.',
  provisional: 'Date provisional keeps the date, shows it with (TBC), everything runs on it.',
  tbc: 'Date TBC clears and disables the date: the row reads TBC and nothing runs.',
  showConsole: '"Show on student site" reads "Show in student console" for a course with the console on.',
  perFile: 'Per-file release time: click a file chip to set its own time (default: an hour before the first slot).',
  readingsSub: 'Writes a second entry under Readings, attached to this session. The reading materials go in Files (private to students); the Reading guide is what every student sees (and the only readings content on the public site).',
  guide: '(markdown: any URL links, guidance as to what to read, chapters or pages, how to approach it, etc. This is publicly visible even when the hosted readings files are private)',
  readingsFilesSub: 'the reading materials; private unless a file is made public here',
  readingsFiles: 'private unless a file is made public (per-file toggle on its chip)',
  attach: 'Attached readings show on that session’s row and under its heading on the Readings tab; None gives them a dated row of their own.',
  examFiles: 'Files to release (rare for an exam: a past paper, a formula sheet):',
  assetsName: '(for you; never a row)',
  template: 'The course’s assignment template carries the expected values; a change here applies to this semester’s instance only.',
  external: '"Outside GitHub" on a template with automatic tests: nothing is collected, so the tests cannot run.',
  runHeading: '(all inherited from the semester’s default config values unless changed here)',
  run: 'Values are prewritten from the semester’s defaults; change one to override it for this assignment only.',
  submitUrl: '(only when the route is Outside GitHub)',
  repoName: 'Each student’s (or team’s) repo is this name, then -<handle or team>. The default is the entry’s key.',
  details: '(a sentence students see on the row)',
  detailsAssignment: '(a sentence on both rows)',
  breaksOnly: '(breaks only)',
};

const ROUTE_LABEL: Record<string, string> = { assignment_repo: 'Assignment repo', shared_dropbox_repo: 'Shared drop-box repo', external: 'Outside GitHub' };
const GROUP_LABEL: Record<string, string> = { group: 'Group', individual: 'Solo' };
const SEMESTER_LABEL: Record<SemesterType, string> = { start: 'Start', end: 'End', break: 'Break', archive: 'Archive' };

// ------------------------------------------------------------------ small parts

type Set_<T> = (patch: Partial<T>) => void;
const val = (e: Event) => (e.target as HTMLInputElement).value;
const intOf = (t: string): number | '' => (t.trim() === '' ? '' : Number(t));

/** A label line with its `?` on it, never under the input. */
function Lbl({ for: id, children, hint, strong = false }: { for?: string; children: preact.ComponentChildren; hint?: string; strong?: boolean }) {
  return (
    <span class="lbl-row">
      <label for={id}>{strong ? <b>{children}</b> : children}</label>
      {hint ? <Hint small label="About this field">{hint}</Hint> : null}
    </span>
  );
}

function Err({ e }: { e?: string }) {
  return e ? <Invalid>{e}</Invalid> : null;
}

function Toggle({ id, on, set, children, hint, disabled }: { id: string; on: boolean; set: (v: boolean) => void; children: preact.ComponentChildren; hint?: string; disabled?: boolean }) {
  return (
    <span class="sw-line">
      <label class="sw" for={id}><input id={id} type="checkbox" role="switch" checked={on} disabled={disabled} onChange={(e) => set((e.target as HTMLInputElement).checked)} /> {children}</label>
      {hint ? <Hint small label="About this switch">{hint}</Hint> : null}
    </span>
  );
}

/** "Show on student site", "Date provisional", "Date TBC" (round 3: the same three everywhere). */
function Toggles({ id, d, set, consoleOn, showHint, noTbc, plural = false }: { id: string; d: { show: boolean; provisional: boolean; tbc?: boolean }; set: (p: { show?: boolean; provisional?: boolean; tbc?: boolean }) => void; consoleOn: boolean; showHint?: string; noTbc?: boolean; plural?: boolean }) {
  const date = plural ? 'Dates' : 'Date';
  return (
    <div class="sw-row">
      <Toggle id={`${id}-show`} on={d.show} set={(v) => set({ show: v })}>{consoleOn ? 'Show in student console' : 'Show on student site'}{showHint ? <span class="hint"> {showHint}</span> : null}</Toggle>
      <Toggle id={`${id}-prov`} on={d.provisional} set={(v) => set({ provisional: v })} hint={FORM_TEXT.provisional}>{date} provisional</Toggle>
      {noTbc ? null : <Toggle id={`${id}-tbc`} on={!!d.tbc} set={(v) => set({ tbc: v })} hint={FORM_TEXT.tbc}>{date} TBC</Toggle>}
    </div>
  );
}

/** Whether the course lets its students into the console (`student_console: true`): the show switch then says so. */
const consoleOnOf = (p: ReadyProps): boolean => p.course.meta?.student_console === true;

/** When a chip's file ships, in words. */
function timeWords(f: FileDraft, first: Slot | undefined): string {
  if (f.atDate) return `at ${f.atDate}${f.atTime ? ` ${f.atTime}` : ''}`;
  if (!first?.date) return 'with the date';
  return first.time ? 'an hour before' : 'at the day’s start';
}

/** The files of an entry as chips, picked with the shared picker; a click opens one file's own settings. */
function FilesField({ p, id, files, set, first, readings = false, showTime = true, sources }: { p: ReadyProps; id: string; files: FileDraft[]; set: (f: FileDraft[]) => void; first: Slot | undefined; readings?: boolean; showTime?: boolean; sources: { materials: string[]; others: string[] } }) {
  const [picking, setPicking] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const patch = (i: number, x: Partial<FileDraft>) => set(files.map((f, j) => (j === i ? { ...f, ...x } : f)));
  const missing = files.filter((f) => f.repo && f.path && pathVerdict(p.files, p.course.org, f.repo, f.path) === false);
  const f = open === null ? null : files[open];
  const [dd, dt] = first ? defaultReleaseAt(first.date, first.time) : ['', ''];
  return (
    <>
      {files.length ? (
        <div class="chips">
          {files.map((x, i) => (
            <span class={`chip file-chip${missing.includes(x) ? ' bad' : ''}${open === i ? ' current' : ''}`}>
              <button type="button" class="chip-open" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>{x.path} <span class="sub">· {x.repo}{showTime ? ` · ${timeWords(x, first)}` : ''}</span></button>
              {readings ? <button type="button" class={`pub${x.public ? ' on' : ''}`} aria-pressed={x.public} onClick={() => patch(i, { public: !x.public })}>{x.public ? 'public' : 'private'}</button> : null}
              <button type="button" class="x" aria-label={`Remove ${x.path}`} onClick={() => { set(files.filter((_, j) => j !== i)); setOpen(null); }}>&times;</button>
            </span>
          ))}
        </div>
      ) : null}
      {missing.map((x) => <p class="hint">{x.path} is not in {x.repo} yet: it is copied only if it is there at the release time.</p>)}
      {f && open !== null ? (
        <div class="chip-settings">
          <div class="row-3">
            <div class="field"><label for={`${id}-at`}>Release on</label><input id={`${id}-at`} type="date" value={f.atDate || dd} onInput={(e) => patch(open, releaseAt(first ?? { date: '', time: '' }, val(e), f.atTime || dt))} /></div>
            <div class="field"><label for={`${id}-att`}>at</label><input id={`${id}-att`} type="time" value={f.atTime || (f.atDate ? '' : dt)} onInput={(e) => patch(open, releaseAt(first ?? { date: '', time: '' }, f.atDate || dd, val(e)))} /></div>
            <div class="field"><label for={`${id}-to`}>Release to path</label><input id={`${id}-to`} type="text" placeholder="same as source" value={f.toPath} onInput={(e) => patch(open, { toPath: val(e) })} /></div>
          </div>
        </div>
      ) : null}
      {picking ? <FilePicker files={p.files} org={p.course.org} repos={sources.materials} others={sources.others} onClose={() => setPicking(false)}
        onPick={(got) => set([...files, ...got.filter((g) => !files.some((x) => x.repo === g.repo && x.path === g.path)).map((g) => ({ repo: g.repo, path: g.path, toRepo: '', toPath: '', atDate: '', atTime: '', public: false }))])} /> : null}
      <PickButton onClick={() => setPicking(true)} short={false} />
    </>
  );
}

const PickButton = ({ onClick, short }: { onClick: () => void; short: boolean }) => <button class="btn small" type="button" onClick={onClick}>{short ? 'Pick…' : 'Pick from course materials…'}</button>;

/** Date, time and place: one slot, or several each with its own location and details. */
function Slots({ id, d, set, errors }: { id: string; d: EntryDraft; set: Set_<EntryDraft>; errors: Record<string, string>; }) {
  const setSlot = (i: number, x: Partial<Slot>) => set({ slots: d.slots.map((s, j) => (j === i ? { ...s, ...x } : s)) });
  const many = d.slots.length > 1;
  return (
    <>
      {d.slots.map((x, i) => (
        <div class={`row-3${many ? ' slot-row' : ''}`}>
          <div class="field"><Lbl for={`${id}-date${i}`} strong>{many ? `Date, slot ${i + 1}` : 'Date'}</Lbl><input id={`${id}-date${i}`} type="date" value={d.tbc ? '' : x.date} disabled={d.tbc} onInput={(e) => setSlot(i, { date: val(e) })} /><Err e={errors[many ? `slot${i}` : 'date']} /></div>
          <div class="field"><Lbl for={`${id}-time${i}`} strong>Time</Lbl><input id={`${id}-time${i}`} type="time" value={d.tbc ? '' : x.time} disabled={d.tbc} onInput={(e) => setSlot(i, { time: val(e) })} /></div>
          <div class="field"><Lbl for={`${id}-loc${i}`}>Location</Lbl><input id={`${id}-loc${i}`} type="text" placeholder="e.g. Room 1.10" value={x.location} onInput={(e) => setSlot(i, { location: val(e) })} /></div>
          {many ? (
            <div class="field slot-details"><Lbl for={`${id}-sdet${i}`}>Details <span class="hint">(this slot: Group 1)</span></Lbl>
              <span class="pick-typed"><input id={`${id}-sdet${i}`} type="text" value={x.details} onInput={(e) => setSlot(i, { details: val(e) })} /><button type="button" class="x" aria-label={`Remove slot ${i + 1}`} onClick={() => set({ slots: d.slots.filter((_, j) => j !== i) })}>&times;</button></span>
            </div>
          ) : null}
        </div>
      ))}
      {d.tbc ? null : <div><button class="btn small quiet" type="button" onClick={() => set({ slots: [...d.slots, { date: d.slots[0]?.date ?? '', time: d.slots[0]?.time ?? '', location: '', details: '' }] })}>+ Another time slot (a second group, a repeat)</button></div>}
    </>
  );
}

/** Release files to repo / to path, for every file of the entry (the path only with one file). */
function Destination({ id, d, set, label = 'Release files to' }: { id: string; d: EntryDraft; set: Set_<EntryDraft>; label?: string }) {
  const repos = [...new Set(d.files.map((f) => f.toRepo))];
  const one = d.files.length === 1;
  return (
    <>
      <div class="field"><Lbl for={`${id}-torepo`}>{label} repo</Lbl><input id={`${id}-torepo`} type="text" placeholder={DEFAULT_DEST_REPO} value={repos.length === 1 ? repos[0] : ''} onInput={(e) => set({ files: d.files.map((f) => ({ ...f, toRepo: val(e) })) })} /></div>
      <div class="field"><Lbl for={`${id}-topath`}>{label} path</Lbl><input id={`${id}-topath`} type="text" placeholder={one || !d.files.length ? 'same as source' : 'set on each file’s chip'} disabled={!one} value={one ? d.files[0].toPath : ''} onInput={(e) => set({ files: [{ ...d.files[0], toPath: val(e) }] })} /></div>
    </>
  );
}

/** The Number field: a unit's prefilled with its session (decision 0038 amendment rule 3), anything else's "none". */
function NumberInput({ id, d, set, errors, hint }: { id: string; d: EntryDraft; set: Set_<EntryDraft>; errors: Record<string, string>; hint?: string }) {
  const unit = SESSION_KINDS.includes(d.type);
  const shown = d.number === '' && unit ? d.session : d.number;
  return (
    <div class="field">
      <Lbl for={`${id}-num`} hint={hint}>Number</Lbl>
      <input id={`${id}-num`} type="number" min="1" max="999" placeholder="none" value={shown} onInput={(e) => { const n = intOf(val(e)); set({ number: unit && n === d.session ? '' : n }); }} />
      <Err e={errors.number} />
    </div>
  );
}

function Fold({ summary, open, children }: { summary: preact.ComponentChildren; open?: boolean; children: preact.ComponentChildren }) {
  return <details class="fold" open={open}><summary>{summary}</summary><div class="fold-body">{children}</div></details>;
}

const sub = (errors: Record<string, string>, prefix: string) => Object.fromEntries(Object.entries(errors).filter(([k]) => k.startsWith(prefix)).map(([k, v]) => [k.slice(prefix.length), v]));

// ------------------------------------------------------------------ the forms

interface FormProps<T> {
  p: ReadyProps;
  d: T;
  set: Set_<T>;
  errors: Record<string, string>;
  consoleOn: boolean;
  sources: { materials: string[]; others: string[] };
}

/** Lecture and lab (one form): session, title, slots, details, files, Advanced, then the Readings sub-form. */
function UnitForm({ p, d, set, errors, consoleOn, sources }: FormProps<EntryDraft>) {
  const tw = d.twin ?? emptyTwin(d);
  const setTwin: Set_<EntryDraft> = (x) => set({ twin: { ...tw, ...x } });
  const used = !!d.twin && (!!d.twin.id || !!d.twin.files.length || !!d.twin.guide.trim() || !!d.twin.title.trim());
  const twinErr = sub(errors, 'twin.');
  return (
    <>
      <div class="row-session">
        <div class="field"><Lbl for="e-session" strong hint={FORM_TEXT.session}>Session</Lbl><input id="e-session" type="number" min="1" max="999" value={d.session} onInput={(e) => set({ session: intOf(val(e)) })} /><Err e={errors.session} /></div>
        <div class="field"><Lbl for="e-title" strong>Title</Lbl><input id="e-title" type="text" placeholder="e.g. Regularisation" value={d.title} onInput={(e) => set({ title: val(e) })} /></div>
      </div>
      <Slots id="e" d={d} set={set} errors={errors} />
      <div class="field"><Lbl for="e-det">Details <span class="hint">{FORM_TEXT.details}</span></Lbl><textarea id="e-det" rows={2} value={d.details} onInput={(e) => set({ details: val(e) })} /></div>
      <div class="field">
        <span class="lbl-row"><b>Files to release</b><Hint small label="About files to release">{FORM_TEXT.files}</Hint></span>
        <FilesField p={p} id="e-file" files={d.files} set={(files) => set({ files })} first={d.slots[0]} sources={sources} />
        <ul class="bul">{FORM_TEXT.filesBullets.map((b) => <li>{b}</li>)}</ul>
        {Object.entries(errors).filter(([k]) => /^file\d/.test(k)).map(([, v]) => <Invalid>{v}</Invalid>)}
      </div>
      <Fold summary="Advanced" open={d.number !== '' || d.files.some((f) => f.toRepo || f.toPath)}>
        <div class="row-adv">
          <NumberInput id="e" d={d} set={set} errors={errors} hint={FORM_TEXT.number} />
          <Destination id="e" d={d} set={set} />
        </div>
        <p class="hint">{FORM_TEXT.perFile}</p>
        <Toggles id="e" d={d} set={(x) => set(x)} consoleOn={consoleOn} />
      </Fold>
      <div class="sub-division" />
      <Fold open={used} summary={<>Readings for this session{used ? null : ' (none yet)'} <Hint small label="About readings for this session">{FORM_TEXT.readingsSub}</Hint></>}>
        <p class="hint">{FORM_TEXT.readingsSub}</p>
        <div class="row-3">
          <div class="field"><Lbl for="r-date" strong>Date</Lbl><input id="r-date" type="date" value={tw.tbc ? '' : tw.slots[0].date} disabled={tw.tbc} onInput={(e) => setTwin({ slots: [{ ...tw.slots[0], date: val(e) }] })} /><Err e={twinErr.date} /></div>
          <div class="field"><Lbl for="r-time" strong>Time</Lbl><input id="r-time" type="time" value={tw.tbc ? '' : tw.slots[0].time} disabled={tw.tbc} onInput={(e) => setTwin({ slots: [{ ...tw.slots[0], time: val(e) }] })} /></div>
          <div class="field"><Lbl for="r-title">Title</Lbl><input id="r-title" type="text" placeholder={`Readings for session ${d.session || 'N'}`} value={tw.title} onInput={(e) => setTwin({ title: val(e) })} /></div>
        </div>
        <Guide id="r" d={tw} set={setTwin} />
        <div class="field">
          <span class="lbl-row"><b>Files</b><span class="hint"> {FORM_TEXT.readingsFilesSub}</span></span>
          <FilesField p={p} id="r-file" files={tw.files} set={(files) => setTwin({ files })} first={tw.slots[0]} readings showTime={false} sources={sources} />
        </div>
        <Fold summary="Advanced (readings)">
          <div class="row-adv">
            <NumberInput id="r" d={tw} set={setTwin} errors={twinErr} />
            <Destination id="r" d={tw} set={setTwin} label="Release to" />
          </div>
          <Toggles id="r" d={tw} set={(x) => setTwin(x)} consoleOn={consoleOn} />
        </Fold>
      </Fold>
    </>
  );
}

function Guide({ id, d, set }: { id: string; d: EntryDraft; set: Set_<EntryDraft> }) {
  return (
    <div class="field">
      <Lbl for={`${id}-guide`} strong hint={FORM_TEXT.guide}>Reading guide</Lbl>
      <span class="hint">{FORM_TEXT.guide}</span>
      <textarea id={`${id}-guide`} rows={3} value={d.guide} onInput={(e) => set({ guide: val(e) })} />
    </div>
  );
}

/** Readings on their own (or opened from the list): title, Attach to session, date and time, guide, files, Advanced. */
function ReadingsForm({ p, d, set, errors, consoleOn, sources, doc }: FormProps<EntryDraft> & { doc: Record<string, unknown> }) {
  const sessions = [...new Set(SESSION_KINDS.flatMap((k) => Object.values(obj(doc[pluralOf(k)])).map((e) => obj(e).session)).filter((n): n is number => Number.isInteger(n)))].sort((a, b) => a - b);
  if (d.session !== '' && !sessions.includes(d.session)) sessions.push(d.session);
  return (
    <>
      <div class="row-attach">
        <div class="field"><Lbl for="e-title" strong>Title</Lbl><input id="e-title" type="text" value={d.title} onInput={(e) => set({ title: val(e) })} /></div>
        <div class="field"><Lbl for="e-attach" hint={FORM_TEXT.attach}>{d.id && d.session !== '' ? 'Attached to session' : 'Attach to session'}</Lbl>
          <select id="e-attach" onChange={(e) => set({ session: intOf(val(e)) })}>
            <option value="" selected={d.session === ''}>None: its own row</option>
            {sessions.map((n) => <option value={n} selected={n === d.session}>Session {n}</option>)}
          </select>
        </div>
      </div>
      <div class="row-2">
        <div class="field"><Lbl for="e-date0" strong>Date</Lbl><input id="e-date0" type="date" value={d.tbc ? '' : d.slots[0].date} disabled={d.tbc} onInput={(e) => set({ slots: [{ ...d.slots[0], date: val(e) }] })} /><Err e={errors.date} /></div>
        <div class="field"><Lbl for="e-time0" strong>Time</Lbl><input id="e-time0" type="time" value={d.tbc ? '' : d.slots[0].time} disabled={d.tbc} onInput={(e) => set({ slots: [{ ...d.slots[0], time: val(e) }] })} /></div>
      </div>
      <Guide id="e" d={d} set={set} />
      <div class="field">
        <span class="lbl-row"><b>Files</b><span class="hint"> {FORM_TEXT.readingsFiles}</span></span>
        <FilesField p={p} id="e-file" files={d.files} set={(files) => set({ files })} first={d.slots[0]} readings showTime={false} sources={sources} />
      </div>
      <Fold summary="Advanced" open={d.number !== ''}>
        <div class="row-adv">
          <NumberInput id="e" d={d} set={set} errors={errors} />
          <Destination id="e" d={d} set={set} label="Release to" />
        </div>
        <Toggles id="e" d={d} set={(x) => set(x)} consoleOn={consoleOn} />
      </Fold>
    </>
  );
}

/** Exam and event (the event without the number), and any other kind of the institution's. */
function EventForm({ p, d, set, errors, consoleOn, sources }: FormProps<EntryDraft>) {
  return (
    <>
      <div class="field"><Lbl for="e-title" strong>Title</Lbl><input id="e-title" type="text" value={d.title} onInput={(e) => set({ title: val(e) })} /><Err e={errors.title} /></div>
      <Slots id="e" d={d} set={set} errors={errors} />
      <div class="field"><Lbl for="e-det">Details</Lbl><input id="e-det" type="text" value={d.details} onInput={(e) => set({ details: val(e) })} /></div>
      <Fold summary="Advanced" open={d.number !== '' || d.files.length > 0}>
        {numberedField(d.type) ? <div class="row-adv"><NumberInput id="e" d={d} set={set} errors={errors} /></div> : null}
        <Toggles id="e" d={d} set={(x) => set(x)} consoleOn={consoleOn} />
        <div class="field">
          <span class="hint">{FORM_TEXT.examFiles}</span>
          <FilesField p={p} id="e-file" files={d.files} set={(files) => set({ files })} first={d.slots[0]} sources={sources} />
        </div>
      </Fold>
    </>
  );
}

/** Supporting files: a name for the instructor, the release time, the files; shown off by default. */
function AssetsForm({ p, d, set, errors, consoleOn, sources }: FormProps<EntryDraft>) {
  return (
    <>
      <div class="row-assets">
        <div class="field"><Lbl for="e-title" strong>Name</Lbl><span class="hint">{FORM_TEXT.assetsName}</span><input id="e-title" type="text" value={d.title} onInput={(e) => set({ title: val(e) })} /></div>
        <div class="field"><Lbl for="e-date0" strong>Release on</Lbl><input id="e-date0" type="date" value={d.tbc ? '' : d.slots[0].date} disabled={d.tbc} onInput={(e) => set({ slots: [{ ...d.slots[0], date: val(e) }] })} /><Err e={errors.date} /></div>
        <div class="field"><Lbl for="e-time0" strong>at</Lbl><input id="e-time0" type="time" value={d.tbc ? '' : d.slots[0].time} disabled={d.tbc} onInput={(e) => set({ slots: [{ ...d.slots[0], time: val(e) }] })} /></div>
      </div>
      <div class="field">
        <span class="lbl-row"><b>Files</b></span>
        <FilesField p={p} id="e-file" files={d.files} set={(files) => set({ files })} first={d.slots[0]} showTime={false} sources={sources} />
      </div>
      <Fold summary="Advanced" open={d.show || d.files.some((f) => f.toRepo || f.toPath)}>
        <div class="row-2"><Destination id="e" d={d} set={set} label="Release to" /></div>
        <Toggles id="e" d={d} set={(x) => set(x)} consoleOn={consoleOn} showHint="(default off)" />
      </Fold>
    </>
  );
}

function SemesterEntryForm({ d, set, errors, consoleOn }: FormProps<SemesterEntryDraft>) {
  const isBreak = d.type === 'break';
  return (
    <>
      <div class="row-type">
        <div class="field"><Lbl for="e-stype" strong>Type</Lbl>
          <select id="e-stype" onChange={(e) => set({ type: val(e) as SemesterType })}>{(['break', 'start', 'end', 'archive'] as SemesterType[]).map((t) => <option value={t} selected={t === d.type}>{SEMESTER_LABEL[t]}</option>)}</select>
          <Err e={errors.type} />
        </div>
        <div class="field"><Lbl for="e-title" strong>Title</Lbl><input id="e-title" type="text" placeholder={d.type === 'archive' ? 'Semester archived' : ''} value={d.title} onInput={(e) => set({ title: val(e) })} /></div>
      </div>
      <div class="row-3">
        <div class="field"><Lbl for="e-from" strong>From</Lbl><input id="e-from" type="date" value={d.from} onInput={(e) => set({ from: val(e) })} /><Err e={errors.from} />{d.type === 'archive' && !d.from ? <span class="hint">Empty: {archiveFallbackWords}.</span> : null}</div>
        <div class="field"><Lbl for="e-to" strong>To</Lbl><span class="hint">{FORM_TEXT.breaksOnly}</span><input id="e-to" type="date" value={isBreak ? d.to : ''} disabled={!isBreak} onInput={(e) => set({ to: val(e) })} /><Err e={errors.to} /></div>
        <div class="field"><Lbl for="e-det">Details</Lbl><input id="e-det" type="text" value={d.details} onInput={(e) => set({ details: val(e) })} /></div>
      </div>
      <Fold summary="Advanced">
        <Toggles id="e" d={d} set={(x) => set(x)} consoleOn={consoleOn} noTbc />
      </Fold>
    </>
  );
}

/** A run setting's value as the form shows it: the entry's own, else the inherited one. */
function runShown(d: AssignmentDraft, inherited: Record<string, unknown>, k: string): unknown {
  return d.run && k in d.run ? d.run[k] : inherited[k];
}

function AssignmentForm({ p, d, set, errors, consoleOn, templates, tiered, handedOut, baseNumber }: FormProps<AssignmentDraft> & { templates: { repo: string }[]; tiered: Tiered[]; handedOut: boolean; baseNumber: number | '' | undefined }) {
  const cfg = d.template ? gradingConfig(p, d.template) : {};
  const tplGroup = cfg.type === 'group' ? 'group' : 'individual';
  const tplRoute = typeof cfg.submit_via === 'string' && cfg.submit_via in ROUTE_LABEL ? cfg.submit_via : SUBMIT_VIA_DEFAULT;
  const group = (d.group || tplGroup) === 'group';
  const route = d.submitVia || tplRoute;
  const auto = cfg.autograde === true || String(cfg.autograde ?? '').toLowerCase() === 'yes';
  const af = assignmentsFile(p.files, p.cohort.org);
  const layers = semesterLayers(p, af && af !== 'loading' ? af.doc : {}, d.id);
  const inherited: Record<string, unknown> = Object.fromEntries(RUN_KEYS.map((k) => [k, resolve(k, layers).value]));
  const setRun = (k: string, v: unknown) => {
    const run = { ...(d.run ?? {}) };
    if (v === inherited[k] || v === '') delete run[k];
    else run[k] = v;
    set({ run });
  };
  const forced = route === 'shared_dropbox_repo' || route === 'external';
  const vis = forced ? 'private' : String(runShown(d, inherited, 'visibility') ?? 'private');
  const pct = String(runShown(d, inherited, 'late_penalty_per_day') ?? '').replace(/%$/, '');
  const n = d.number || 'N';
  const solutionOff = vis !== 'private' ? 'Not available: student repos are not private, so the solution cannot be pushed automatically.' : d.manual ? 'Needs a hand-out time; the solution must follow it.' : null;
  const opt = (value: string, label: string, from: boolean) => <option value={from ? '' : value} selected={from ? !(value === d.group || value === d.submitVia) && (value === tplGroup || value === tplRoute) : value === d.group || value === d.submitVia}>{label}{from ? ' (from template)' : ''}</option>;
  return (
    <>
      <div class="row-tpl">
        <div class="field"><Lbl for="e-tpl" strong hint="The course’s assignment template this semester hands out. Only a ready template can be chosen.">Course template</Lbl>
          <select id="e-tpl" onChange={(e) => set({ template: val(e) })}>
            {!d.template ? <option value="" selected>Choose a course template</option> : null}
            {templates.map((t) => {
              const r = templateReadinessIn(p.status.course, t.repo, tiered);
              const ready = !r || r.state === 'ready';
              return <option value={t.repo} selected={t.repo === d.template} disabled={!ready && t.repo !== d.template}>{t.repo} ({ready ? 'ready' : r?.state === 'not_ready' ? repoWords(r).replace(/^not ready: /i, 'not ready: ') : 'has a problem'})</option>;
            })}
            {d.template && !templates.some((t) => t.repo === d.template) ? <option value={d.template} selected>{d.template}</option> : null}
          </select>
          <Err e={errors.template} />
        </div>
        <div class="field"><Lbl for="e-num" strong>Number</Lbl><input id="e-num" type="number" min="1" max="999" value={d.number ?? ''} onInput={(e) => set({ number: intOf(val(e)) })} /><Err e={errors.number} /></div>
      </div>
      {d.id && handedOut && d.number !== baseNumber ? <CheckLine cls="warn">Handed out already: the key {d.id} stays, since it names the student repos and the mark sheet.</CheckLine> : null}
      <div class="row-2">
        <div class="field"><Lbl for="e-group" strong hint={FORM_TEXT.template}>Group or solo</Lbl>
          <select id="e-group" onChange={(e) => set({ group: (val(e) as AssignmentDraft['group']) })}>
            {['group', 'individual'].map((g) => opt(g, GROUP_LABEL[g], g === tplGroup))}
          </select>
        </div>
        <div class="field"><Lbl for="e-route" strong>Submission route</Lbl>
          <select id="e-route" onChange={(e) => set({ submitVia: val(e) })}>
            {Object.keys(ROUTE_LABEL).map((r) => opt(r, ROUTE_LABEL[r], r === tplRoute))}
          </select>
        </div>
      </div>
      {route === 'external' && auto ? <CheckLine cls="warn">{FORM_TEXT.external}</CheckLine> : null}
      <div class="row-4">
        <div class="field"><Lbl for="e-how" strong>Hand out</Lbl>
          <select id="e-how" disabled={d.datesTbc} onChange={(e) => set({ manual: val(e) === 'hand' })}><option value="time" selected={!d.manual}>At a time</option><option value="hand" selected={d.manual}>By hand, later</option></select>
        </div>
        <div class="field"><Lbl for="e-hod">on</Lbl><input id="e-hod" type="date" value={d.handoutDate} disabled={d.manual || d.datesTbc} onInput={(e) => set({ handoutDate: val(e) })} /><Err e={errors.handout} /></div>
        <div class="field"><Lbl for="e-hot">at</Lbl><input id="e-hot" type="time" value={d.handoutTime} disabled={d.manual || d.datesTbc} onInput={(e) => set({ handoutTime: val(e) })} /></div>
      </div>
      <div class="row-4">
        <div class="field"><Lbl for="e-due" strong>Due</Lbl><input id="e-due" type="date" value={d.dueDate} disabled={d.datesTbc} onInput={(e) => set({ dueDate: val(e) })} /><Err e={errors.due} /></div>
        <div class="field"><Lbl for="e-duet">at</Lbl><input id="e-duet" type="time" value={d.dueTime} disabled={d.datesTbc} onInput={(e) => set({ dueTime: val(e) })} /></div>
      </div>
      <div class="field"><Lbl for="e-det">Details <span class="hint">{FORM_TEXT.detailsAssignment}</span></Lbl><input id="e-det" type="text" value={d.details} onInput={(e) => set({ details: val(e) })} /></div>
      <Fold summary="Solutions and marks" open={d.solutionOn || d.marksOn}>
        {solutionOff ? <p class="footnote">{solutionOff}</p> : null}
        <div class="row-3">
          <div class="field"><Lbl for="e-sol">Release solutions on</Lbl><input id="e-sol" type="date" placeholder="never automatically" disabled={!!solutionOff} value={d.solutionOn ? d.solutionDate : ''} onInput={(e) => set({ solutionOn: !!val(e), solutionDate: val(e) })} /><Err e={errors.solution} /></div>
          <div class="field"><Lbl for="e-solt">at</Lbl><input id="e-solt" type="time" disabled={!!solutionOff || !d.solutionOn} value={d.solutionTime} onInput={(e) => set({ solutionTime: val(e) })} /></div>
          <div class="field"><Lbl for="e-marks">Return marks on</Lbl><input id="e-marks" type="date" placeholder="by hand" value={d.marksOn ? d.marksDate : ''} onInput={(e) => set({ marksOn: !!val(e), marksDate: val(e) })} /><Err e={errors.marks} /></div>
        </div>
        {d.solutionOn && !solutionOff ? <SolutionNote /> : null}
        <Toggle id="e-mpq" on={runShown(d, inherited, 'marks_per_question') !== false} set={(v) => setRun('marks_per_question', v)} hint={RUN_REASON.marks_per_question}>Marks per question</Toggle>
      </Fold>
      <Fold summary={<>How this semester runs it <span class="hint">{FORM_TEXT.runHeading}</span></>} open={Object.keys(d.run ?? {}).some((k) => k !== 'marks_per_question')}>
        <div class="row-3">
          <div class="field"><Lbl for="e-lw">Late window (days)</Lbl><input id="e-lw" type="number" min="0" value={String(runShown(d, inherited, 'late_window_days') ?? '')} onInput={(e) => setRun('late_window_days', intOf(val(e)))} /><Err e={errors.late_window_days} /></div>
          <div class="field"><Lbl for="e-lp">Late penalty per day</Lbl><span class="pct"><input id="e-lp" type="text" inputMode="decimal" value={pct} onInput={(e) => setRun('late_penalty_per_day', val(e).trim() === '' ? '' : `${val(e).trim()}%`)} /><span>%</span></span><Err e={errors.late_penalty_per_day} /></div>
          {group ? <div class="field"><Lbl for="e-tf">Team formation</Lbl><select id="e-tf" onChange={(e) => setRun('team_formation', val(e))}>{TEAM_FORMATIONS.map((t) => <option value={t} selected={t === runShown(d, inherited, 'team_formation')}>{TEAM_FORMATION[t] ?? t}</option>)}</select></div> : null}
          {group ? <div class="field"><Lbl for="e-ts">Max team size</Lbl><input id="e-ts" type="number" min="1" value={String(runShown(d, inherited, 'max_team_size') ?? '')} onInput={(e) => setRun('max_team_size', intOf(val(e)))} /></div> : null}
          <div class="field"><Lbl for="e-vis" hint={forced ? (forcedVisibility({ submit_via: route }) ?? undefined) : undefined}>Repo visibility</Lbl><select id="e-vis" disabled={forced} onChange={(e) => setRun('visibility', val(e))}>{VISIBILITIES.map((v) => <option value={v} selected={v === vis}>{VISIBILITY[v] ?? v}</option>)}</select></div>
          <div class="field"><Lbl for="e-url">Submission URL <span class="hint">{FORM_TEXT.submitUrl}</span></Lbl><input id="e-url" type="url" disabled={route !== 'external'} value={route === 'external' ? String(runShown(d, inherited, 'submit_url') ?? '') : ''} onInput={(e) => setRun('submit_url', val(e))} /><Err e={errors.submit_url} /></div>
        </div>
        <p class="hint">{FORM_TEXT.run}</p>
      </Fold>
      <Fold summary="Advanced" open={!!d.repoName}>
        <div class="row-2"><div class="field"><Lbl for="e-repo" hint={FORM_TEXT.repoName}>Repo name</Lbl><input id="e-repo" type="text" placeholder={`assignment-${n}-<handle or team> (default)`} value={d.repoName ?? ''} onInput={(e) => set({ repoName: val(e) })} /><Err e={errors.repoName} /></div></div>
        <Toggles id="e" d={{ show: d.show, provisional: d.tbc, tbc: d.datesTbc }} set={(x) => set({ ...(x.show !== undefined ? { show: x.show } : {}), ...(x.provisional !== undefined ? { tbc: x.provisional } : {}), ...(x.tbc !== undefined ? { datesTbc: x.tbc } : {}) })} consoleOn={consoleOn} plural />
      </Fold>
      {d.id ? <a class="textlink" href={`#assignment-${d.id}`}>Open the assignment</a> : null}
    </>
  );
}

export function SemesterForm({ d, set, errors }: { d: SemesterDraft; set: Set_<SemesterDraft>; errors: Record<string, string> }) {
  return (
    <>
      <div class="row-2">
        <div class="field"><Lbl for="e-ts" hint="Everything on the site’s calendar hangs off these.">Semester starts</Lbl><input id="e-ts" type="date" value={d.start} onInput={(e) => set({ start: val(e) })} /></div>
        <div class="field"><Lbl for="e-te">Semester ends</Lbl><input id="e-te" type="date" value={d.end} onInput={(e) => set({ end: val(e) })} /><Err e={errors.end} /></div>
      </div>
      <Fold summary="Advanced" open={!!d.tz && d.tz !== DEFAULT_TIMEZONE}>
        <div class="field"><Lbl for="e-tz" hint="Every date in this schedule is in this timezone.">Timezone</Lbl>
          <select id="e-tz" onChange={(e) => set({ tz: val(e) })}>
            <option value="" selected={!d.tz}>{DEFAULT_TIMEZONE} (default)</option>
            {[...new Set([...TIMEZONES, d.tz].filter(Boolean))].map((t) => <option value={t} selected={t === d.tz}>{t}</option>)}
          </select>
        </div>
      </Fold>
    </>
  );
}

/** What archiving does, beside the archive row and on its sheet. */
function ArchiveHelp({ org }: { org: string }) {
  return (
    <Hint small label="About archiving" doc="10-grade-and-return-assignments.md">
      Archiving makes every repo in {org} read-only: student repos, marks repos, the student site, the join form, the settings repo. Students and instructors keep read access; nothing is deleted and the student site stays online.
      It happens on the archive entry’s date, or {archiveFallbackWords} when the entry names none; students get an Updates notice two weeks before. Without an archive entry it never happens by itself.
    </Hint>
  );
}

// ------------------------------------------------------------------ the Add menu

interface MenuItem {
  kind: string;
  label: string;
  colour: string;
  hint: string;
}

/** The Add menu: the institution's kinds in its order and colours, then Assignment, Supporting files, Semester. */
export function addMenu(doc: Record<string, unknown>): MenuItem[][] {
  const item = (kind: string): MenuItem => {
    const k = KINDS.find((x) => x.key === kind)!;
    const hint = SESSION_KINDS.includes(kind) ? `session ${nextSession(doc, kind)} next` : kind === 'assignment' || (k.numbered && !k.system) ? `number ${nextNumber(doc, kind)} next` : !k.shown ? 'no student row' : kind === 'semester' ? 'start · end · break · archive' : '';
    return { kind, label: k.label, colour: k.colour, hint };
  };
  const content = KINDS.filter((k) => !k.system && k.shown).map((k) => item(k.key));
  const hidden = KINDS.filter((k) => !k.system && !k.shown).map((k) => item(k.key));
  const groups = [content, KINDS.some((k) => k.key === 'assignment') ? [item('assignment')] : [], hidden, KINDS.some((k) => k.key === 'semester') ? [item('semester')] : []];
  return groups.filter((g) => g.length);
}

function AddMenu({ doc, pick }: { doc: Record<string, unknown>; pick: (kind: string) => void }) {
  return (
    <div class="add-menu" role="menu" aria-label="Add to the schedule">
      {addMenu(doc).map((g, i) => (
        <>
          {i ? <div class="sep" role="separator" /> : null}
          {g.map((m) => <button type="button" role="menuitem" data-kind={m.kind} onClick={() => pick(m.kind)}><i style={`background:${m.colour}`} />{m.label}{m.hint ? <span>{m.hint}</span> : null}</button>)}
        </>
      ))}
    </div>
  );
}

/** What a draft is called in its head: "New lecture", or the row's identifier and title. */
function headOf(d: Draft, isNew: boolean, row: Row | undefined): { eyebrow: string; title: string; colour?: string } {
  if (d.kind === 'semester') return { eyebrow: 'Semester', title: 'Semester dates' };
  const kind = d.kind === 'assignments' ? 'assignment' : d.kind === 'semesterEntry' ? 'semester' : d.type;
  const k = KINDS.find((x) => x.key === kind);
  const label = k?.label ?? kind;
  if (isNew) return { eyebrow: label, title: d.kind === 'semesterEntry' ? 'Semester entry' : d.kind === 'entry' && hiddenKind(d.type) ? 'Release supporting files' : `New ${label.toLowerCase()}`, colour: k?.colour };
  if (d.kind === 'semesterEntry') return { eyebrow: label, title: d.title || SEMESTER_LABEL[d.type], colour: k?.colour };
  return { eyebrow: label, title: row ? `${row.ident}: ${row.name}` : d.title || d.id, colour: k?.colour };
}

/** The primary button's words for a new entry of `d`'s kind. */
function addWord(d: Draft): string {
  if (d.kind === 'assignments') return 'Add assignment';
  if (d.kind !== 'entry') return 'Add';
  if (hiddenKind(d.type)) return 'Add';
  return `Add ${(KINDS.find((k) => k.key === d.type)?.label ?? d.type).toLowerCase()}`;
}

// ------------------------------------------------------------------ the page

function View(p: ReadyProps) {
  const { status, now } = p;
  const env = useEnv();
  // Every problem with its time, and each held or late release's mark, once per render (decision 0034).
  const tiered = useMemo(() => tier(status), [status]);
  const marks = useMemo(() => releaseMarks(status, tiered, now), [status, tiered, now]);
  const tz = tzOf(status), year = yearOf(now, tz);
  const scope = cohortScope(p);
  // The archive date the engine reports, whether it has come, and whether the semester is archived already.
  const archiveDate = status.semester?.archive_date ?? null;
  const archiveWhen = archiveDate ? fmtDay(archiveDate, tz, year) : null;
  const archivePassed = !!archiveDate && archiveDate.slice(0, 10) <= todayOf(now, tz);
  const archived = status.semester?.live === false;
  const [filters, setFilters] = useState<Record<Block, boolean>>({ releases: true, assignments: true, events: true });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [removed, setRemoved] = useState<string[]>([]);
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });
  const file = useSchedFile(p);
  const sf = file && file !== 'loading' ? file : null;
  const schedExists = p.files.file(p.cohort.org, CONFIG_REPO, 'schedule.yml').kind !== 'absent';
  const doc = sf?.doc ?? {};
  const sched = useMemo(() => (sf && !sf.error ? parseSchedule(sf.text) : null), [sf?.text]);
  const rows = scheduleRows(status, sched, now, tz);
  const assignmentOf = new Map((status.assignments ?? []).map((a) => [a.slug, a]));
  const key = p.entry;
  const current = key && key !== 'new' && key !== 'semester' ? rows.find((r) => r.entry === key) : undefined;
  const repos = (status.course?.materials ?? []).map((m) => m.repo);
  const sources = sourceRepos(p, repos);
  const templates = status.course?.templates ?? [];
  const consoleOn = consoleOnOf(p);
  const handedOut = (k: string) => { const a = assignmentOf.get(k); return !!a?.handout && Date.parse(a.handout) <= now; };

  const baseOf = (k: string): Draft | null => (k === 'new' ? null : readDraft(doc, k));
  // `?template=` opens a new assignment entry for that template (New assignment's last step).
  const prefilled = (): Draft | null => (p.prefill && key === 'new' && sf ? { ...(newDraft('assignment', doc) as AssignmentDraft), template: p.prefill } : null);
  const draftOf = (k: string): Draft | null => drafts[k] ?? (k === 'new' ? prefilled() : baseOf(k));
  const dirtyKeys = Object.keys(drafts).filter((k) => k === 'new' || !deepEqual(drafts[k], baseOf(k)));
  const dirty = dirtyKeys.length + removed.length;
  const setDraft = (k: string, d: Draft) => {
    setDrafts({ ...drafts, [k]: d });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  // An assignment draft's late cutoff, off the cascade with its own run settings first. No stated window is none: the due date.
  const cutoffFor = (d: Draft): string | null => {
    if (d.kind !== 'assignments') return null;
    const af = assignmentsFile(p.files, p.cohort.org);
    const layers = semesterLayers(p, af && af !== 'loading' ? af.doc : {}, d.id);
    const own = d.run && 'late_window_days' in d.run ? d.run.late_window_days : resolve('late_window_days', layers).value;
    return cutoffOf(d.dueDate, d.dueTime, typeof own === 'number' ? own : 0);
  };
  const runErrorsOf = (d: Draft): Record<string, string> => {
    if (d.kind !== 'assignments' || !d.run) return {};
    const e: Record<string, string> = {};
    const lp = d.run.late_penalty_per_day;
    if (lp !== undefined && lp !== '') { const msg = penaltyError(lp); if (msg) e.late_penalty_per_day = msg; }
    const lw = d.run.late_window_days;
    if (lw !== undefined && lw !== '' && !(Number.isInteger(lw) && (lw as number) >= 0)) e.late_window_days = 'A whole number of days, 0 or more.';
    const route = d.submitVia || String(gradingConfig(p, d.template).submit_via ?? SUBMIT_VIA_DEFAULT);
    if (route === 'external' && !String(d.run.submit_url ?? resolve('submit_url', semesterLayers(p, {}, '')).value ?? '').trim()) e.submit_url = 'Give the address students submit to.';
    return e;
  };
  const errorsOf = (d: Draft) => ({ ...draftErrors(d, { doc, removed }, cutoffFor(d)), ...runErrorsOf(d) });

  const doSave = async () => {
    if (!sf || sf.error) return;
    const firstBad = dirtyKeys.map((k) => Object.values(errorsOf(drafts[k]))[0]).find(Boolean);
    if (firstBad) return setSave({ kind: 'bad', text: firstBad });
    // The dates sheet first, then every entry: new keys generated, renumbered ones renamed, blocks re-sorted.
    const y = new YamlText(sf.text);
    const dates = dirtyKeys.map((k) => drafts[k]).find((d) => d.kind === 'semester');
    if (dates) writeDraft(y, dates, doc);
    const entryKeys = dirtyKeys.filter((k) => drafts[k].kind !== 'semester');
    const out = applyChanges(y.text, (y.toJS() ?? {}) as Record<string, unknown>, changesOf(entryKeys.map((k) => drafts[k]), entryKeys.map((k) => baseOf(k))), removed, { handedOut });
    if (!validSchedule(parse(out.text) ?? {})) return setSave({ kind: 'bad', text: invalidText('the schedule', validSchedule) });
    const newId = entryKeys.includes('new') ? out.keys[changesOf(entryKeys.slice(0, entryKeys.indexOf('new')).map((k) => drafts[k]), entryKeys.slice(0, entryKeys.indexOf('new')).map((k) => baseOf(k))).length] : null;
    // A removed assignment leaves no block in assignments.yml, which the engine would fault.
    const gone = removed.filter((k) => blockOf(doc, k) === ASSIGNMENTS_BLOCK);
    let second: Step | null = null;
    if (gone.length) {
      const af = assignmentsFile(p.files, p.cohort.org);
      if (af && af !== 'loading' && !af.error) {
        const text = assignmentsAfterSchedule(af, null, gone);
        if (text !== null) {
          if (!validAssignments(parse(text) ?? {})) return setSave({ kind: 'bad', text: invalidText(ASSIGNMENTS_FILE, validAssignments) });
          second = { target: { owner: p.cohort.org, repo: CONFIG_REPO, path: ASSIGNMENTS_FILE }, text, sha: af.sha, opts: { message: `assignments: remove ${gone.join(', ')}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] } };
        }
      }
    }
    const what = dirty === 1 && dirtyKeys.length === 1 ? (dirtyKeys[0] === 'new' ? `add ${newId}` : `edit ${dirtyKeys[0]}`) : dirtyKeys.length === 0 ? `remove ${removed.join(', ')}` : `${dirty} changes`;
    const first: Step = { target: { owner: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }, text: out.text, sha: sf.sha, opts: { message: `schedule: ${what}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] } };
    await saveSteps(env, setSave, first, second, 'Entry removed, but its block is still in assignments.yml', () => {
      setDrafts({});
      setRemoved([]);
      if (newId && typeof location !== 'undefined') location.hash = `#schedule-${newId}`;
    });
  };

  const counts: Record<Block, number> = { releases: 0, assignments: 0, events: 0 };
  const seen = new Set<string>();
  for (const r of rows) if (r.entry !== 'semester' && r.type !== 'archive' && !seen.has(`${r.block}:${r.entry}`)) { seen.add(`${r.block}:${r.entry}`); counts[r.block]++; }
  // The lede counts the held releases in their time words (decision 0034): was skipped, will be skipped, not ready yet.
  const held = [...marks.values()].filter((m) => !m.late);
  const heldLine = (['now', 'soon', 'later'] as const).map((b) => [b, held.filter((m) => m.bites === b).length] as const)
    .filter(([, n]) => n).map(([b, n]) => heldSentence(b, n)).join(' ');
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  let todayDone = false;
  const items = [];
  for (const r of rows) {
    if (!todayDone && (!r.when || sortKey(r.when, tz) >= nowKey)) {
      todayDone = true;
      items.push(<li class="today-line">Today, {fmtDay(new Date(now).toISOString(), tz, year)}</li>);
    }
    if (!filters[r.block]) continue;
    const rel = r.block !== 'assignments' ? (status.releases ?? []).find((x) => x.id === r.entry) : undefined;
    const rk = r.entry;
    const gone = removed.includes(rk);
    const ref = rel ? releaseRef(rel, tz, year) : null;
    const m = rowMark(marks, r);
    const st = gone ? (
      <><span>Removed.</span><button class="textlink" type="button" style="min-height:0;padding:0" onClick={() => setRemoved(removed.filter((x) => x !== rk))}>Undo</button></>
    ) : m ? <><ReleaseMarks m={m} entry={r.entry} />{m.late ? <a class="textlink" href={`#release-${r.entry}`}>Details</a> : null}</>
      : rel && !ref && r.block === 'releases' ? <><span class="st-note">{NOTHING_TO_RELEASE}</span><a class="textlink" href={`#schedule-${rk}`}>Edit</a></>
      : ref && rel?.state === 'planned' ? <><span class="st-chip">planned</span><OpOpen def={releaseEarly(scope, ref)} cls="btn small" label="Release early" /></>
      : <span class="st-chip">{r.state}</span>;
    items.push(
      <li class={`trow ${TYPE_CLASS[r.type] ?? 'evt'}${markClass(m)}${key === rk ? ' current' : ''}${gone ? ' removed' : ''}${r.show ? '' : ' off-site'}`} data-entry={r.entry}>
        <span class="k">{siteRowWord(r.type)}</span>
        <span class="d">{r.when ? fmtDay(r.when, tz, year) : 'No date yet'}{r.when && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}{r.tbc ? ' (TBC)' : ''}</span> : r.tbc && r.when ? <span>(TBC)</span> : null}</span>
        <span class="ttl"><a href={`#schedule-${rk}`}><b>{r.ident}</b>: {r.name}</a></span>
        <Details r={r} a={r.block === 'assignments' ? assignmentOf.get(r.entry) : undefined} now={now} />
        <span class="st">{st}</span>
      </li>,
    );
  }

  let sheet = null;
  if (key && sf) {
    const d = draftOf(key);
    const close = <a class="x" href="#schedule" aria-label="Close entry" style="text-decoration:none;display:grid;place-items:center" onClick={() => { const n = { ...drafts }; delete n[key]; setDrafts(n); }}>&times;</a>;
    if (key === 'new' && !d) {
      sheet = (
        <div class="entry" role="dialog" aria-labelledby="entry-title">
          <div class="entry-head"><div><div class="eyebrow">New entry</div><h2 id="entry-title">Add to the schedule</h2></div>{close}</div>
          <div class="entry-body"><AddMenu doc={doc} pick={(kind) => setDraft('new', newDraft(kind, doc))} /><SaveLine state={save} /></div>
        </div>
      );
    } else if (d) {
      const isNew = key === 'new';
      const errors = key in drafts || isNew ? errorsOf(d) : {};
      const set = <T,>(patch: Partial<T>) => setDraft(key, { ...(d as object), ...patch } as unknown as Draft);
      const rel = d.kind === 'entry' && !isNew ? (status.releases ?? []).find((x) => x.id === key) : undefined;
      const ref = rel ? releaseRef(rel, tz, year) : null;
      const head = headOf(d, isNew, current);
      const probs = (status.problems ?? []).filter((x) => x.fix?.entry === key && x.fix.path === 'schedule.yml');
      const base = isNew ? null : baseOf(key);
      const firstProblem = Object.values(errors)[0];
      const fp = { p, errors, consoleOn, sources };
      sheet = (
        <div class="entry" role="dialog" aria-labelledby="entry-title">
          <div class="entry-head">
            <div>
              <div class="eyebrow"><span class="kind-dot" style={head.colour ? `background:${head.colour}` : undefined} />{head.eyebrow}</div>
              <h2 id="entry-title">{head.title}{d.kind === 'semesterEntry' && d.type === 'archive' ? <> <ArchiveHelp org={p.cohort.org} /></> : null}</h2>
              {d.kind === 'entry' && SESSION_KINDS.includes(d.type) && d.session !== '' ? <div class="hint">Session {d.session} <Hint small label="About the session">{FORM_TEXT.session}</Hint></div> : null}
              {d.kind === 'assignments' && d.number ? <div class="hint">Number {d.number} <Hint small label="About the number">{FORM_TEXT.number.replace('Lecture 3', 'Assignment 3')}</Hint></div> : null}
              {rel ? <div style="margin-top:6px"><StateChip state={rel.state} m={marks.get(rel.id) ?? null} /></div> : null}
            </div>
            {close}
          </div>
          <div class="entry-body">
            {probs.length && rel?.state !== 'will_be_skipped' ? <ProblemCards list={probs} /> : null}
            {rel?.state === 'released' && rel.when ? <p class="note">Released {fmtDay(rel.when, tz, year)}: changes affect the site and later copies only.</p> : null}
            <div class="form">
              {d.kind === 'entry' && SESSION_KINDS.includes(d.type) ? <UnitForm {...fp} d={d} set={set} />
                : d.kind === 'entry' && d.type === READINGS_KIND ? <ReadingsForm {...fp} d={d} set={set} doc={doc} />
                : d.kind === 'entry' && hiddenKind(d.type) ? <AssetsForm {...fp} d={d} set={set} />
                : d.kind === 'entry' ? <EventForm {...fp} d={d} set={set} />
                : d.kind === 'assignments' ? <AssignmentForm {...fp} d={d} set={set} templates={templates} tiered={tiered} handedOut={!isNew && handedOut(key)} baseNumber={base?.kind === 'assignments' ? base.number : undefined} />
                : d.kind === 'semesterEntry' ? <SemesterEntryForm {...fp} d={d} set={set} />
                : <SemesterForm d={d} set={set} errors={errors} />}
            </div>
          </div>
          <div class="entry-foot">
            {rel && ref ? (
              rel.state === 'planned' ? <div class="savebar"><span class="footnote">Goes out at its time without you.</span><OpOpen def={releaseEarly(scope, ref)} cls="btn small outline" label="Release early…" /></div>
              : rel.state === 'will_be_skipped' && needsANumber(status, rel.id) ? <div class="savebar"><span class="st-note">Give it a number first; it cannot be released until it has one.</span></div>
              : rel.state === 'released' ? <div class="savebar"><span class="footnote">Released.</span><a class="btn small quiet" href={`#release-${rel.id}`}>Release again…</a></div>
              : null
            ) : null}
            {d.kind === 'semesterEntry' && d.type === 'archive' && !isNew && !archived ? <div class="savebar"><span class="footnote">{archivePassed ? 'The archive date has passed.' : 'Archiving before the scheduled date asks you to confirm.'}</span><OpButtons def={archive(scope, archiveWhen, archivePassed)} /></div> : null}
            <SaveLine state={save} />
            <div class="savebar">
              {!isNew && key !== 'semester' && blockOf(doc, key) && !removed.includes(key) ? (
                <button class="btn small quiet danger-quiet" type="button" onClick={() => { setRemoved([...removed, key]); const n = { ...drafts }; delete n[key]; setDrafts(n); if (typeof location !== 'undefined') location.hash = '#schedule'; }}>Delete this entry</button>
              ) : null}
              <span style="margin-left:auto" />
              <a class="btn" href="#schedule" onClick={() => { const n = { ...drafts }; delete n[key]; setDrafts(n); }}>Cancel</a>
              <button class="btn primary" type="button" disabled={save.kind === 'busy' || !dirty || !!firstProblem} onClick={() => void doSave()}>{firstProblem ?? (isNew ? addWord(d) : 'Save')}</button>
            </div>
            <EditFile org={p.cohort.org} repo={CONFIG_REPO} path="schedule.yml" exists={schedExists} line={!isNew && key !== 'semester' ? lineOf(sf.text, key, 2) : undefined} />
          </div>
        </div>
      );
    } else {
      sheet = <div class="entry"><div class="entry-head"><div><h2 id="entry-title">Not in the schedule</h2></div>{close}</div><div class="entry-body"><p>There is no entry called {key} in schedule.yml.</p></div></div>;
    }
  }

  const archiveKey = sched?.archive?.id;
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">Schedule <Hint doc="07-schedule-releases.md">The schedule drives everything automatic: releases, hand outs, collection and the student site’s calendar. Dates are in the semester’s timezone.</Hint></h2>
          <p class="lede">{counts.releases + counts.assignments + counts.events} entries. {heldLine || 'Every planned release has its associated folder.'}</p>
        </div>
        <div class="actions"><CheckNow p={p} label="Check" about={{ text: SCHEDULE_CHECK_HINT, doc: 'reference/actions-reference.md' }} /><a class="btn outline" href="#schedule-new">Add entry</a></div>
      </div>
      <div class="term-meta">
        {sched?.start && sched?.end ? <span>Semester <b>{fmtDay(sched.start, tz, year)} to {fmtDay(sched.end, tz)}</b></span> : null}
        <span>Timezone <b>{sched?.timezone ?? tz}</b></span>
        <span>Archive <b>{archived ? 'done: every repo is read-only' : archiveWhen ?? 'never'}</b> <ArchiveHelp org={p.cohort.org} /></span>
        <a class="textlink" href="#schedule-semester">Edit semester dates</a>
        <a class="textlink" href={archiveKey ? `#schedule-${archiveKey}` : '#schedule-new'} onClick={() => { if (!archiveKey) setDraft('new', { ...(newDraft('semester', doc) as SemesterEntryDraft), type: 'archive' }); }}>Archive settings</a>
      </div>
      {file === 'loading' ? <p class="footnote" style="margin-bottom:12px">Reading schedule.yml…</p> : null}
      {file === null ? <p class="footnote" style="margin-bottom:12px">There is no schedule.yml to edit.</p> : null}
      {sf?.error ? <p class="check-line bad" style="margin-bottom:12px"><span>schedule.yml does not parse ({sf.error}); fix it with Edit the file directly before editing here.</span></p> : null}
      <div class={`sched-layout${sheet ? '' : ' no-entry'}`}>
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
              <div class="savebar"><span class="footnote">Release a folder that is not in the schedule, for a one-off or a correction.</span><OpOpen def={releaseAdhoc(scope, [...repos, ...sources.others])} cls="btn small outline" label="Release something unscheduled…" /></div>
              <div class="savebar"><span class="footnote">See what automation’s next scheduled release run would do.</span><OpOpen def={scheduledPreview(scope)} cls="btn small outline" label="Preview scheduled releases" /></div>
            </div>
          </details>
          <div style="margin-top:14px"><EditFile org={p.cohort.org} repo={CONFIG_REPO} path="schedule.yml" exists={schedExists} /></div>
        </div>
        {sheet}
      </div>
      {!key ? <SaveLine state={save} /> : null}
      <UnsavedBar count={dirty} busy={save.kind === 'busy'} onDiscard={() => { setDrafts({}); setRemoved([]); setSave({ kind: 'idle' }); }} onSave={() => void doSave()} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml', exists: schedExists }} />
    </>
  );
}

export function ScheduleScreen(p: CohortProps) {
  return <WithStatus props={p} title="Schedule">{(r) => <View {...r} />}</WithStatus>;
}

// --------------------------------------------------------------------------- S11

/** A release's state chip: a held or late one in its time word (red now or soon, dotted later), else its state. */
function StateChip({ state, m }: { state: Release['state']; m: ReleaseMark | null }) {
  const cls = m ? (m.bites === 'later' ? 'suggest' : 'bad') : state === 'released' ? 'ok' : '';
  return <span class={`chip ${cls}`}>{m ? markWord(m) : RELEASE_WORD[state]}</span>;
}

function ReleaseDetail(p: ReadyProps & { rel: Release }) {
  const { status, now, rel } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const mark = useMemo(() => releaseMarks(status, tier(status), now).get(rel.id) ?? null, [status, now, rel.id]);
  const ident = releaseIdent(rel);
  const st = rel.state;
  const scope = cohortScope(p);
  const ref = releaseRef(rel, tz, year);
  if (!ref) {
    return (
      <>
        <div class="page-head">
          <div>
            <h2 class="h1"><b>{ident}</b>: {rel.title}</h2>
            <p class="lede">{NOTHING_TO_RELEASE}.</p>
          </div>
          <div class="actions"><a class="btn" href={`#schedule-${rel.id}`}>Edit entry</a></div>
        </div>
      </>
    );
  }
  // The problems that hold it back, joined on `release` (a placeholder stub's fix names its repo, not the entry).
  const problems = releaseProblems(status, rel);
  const last = (status.operations ?? []).find((o) => o.op.startsWith('release.') && o.summary.includes(`${ident}:`));
  const dest = rel.dest?.repo || DEFAULT_DEST_REPO, destPath = rel.dest?.path || ref.source.path;
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1"><b>{ident}</b>: {rel.title} <Hint doc="08-release-materials-to-cohort.md">{st === 'released'
            ? 'Edits students should see: push to the semester copy, or release again after fixing the course copy. Edits future semesters should keep: keep for future semesters.'
            : st === 'will_be_skipped' ? 'Fix it in the schedule entry (Edit entry); automation releases it at its time once fixed.'
            : st === 'late' ? 'Reason codes tell you whether the source, the schedule or the scheduler was at fault.'
            : 'Nothing to do; it goes out at the scheduled time. You can release it early.'}</Hint></h2>
          <p class="lede"><StateChip state={st} m={mark} />{fmtWhen(rel.when, tz, year)}</p>
        </div>
        <div class="actions"><a class="btn quiet" href={`#schedule-${rel.id}`}>Edit entry</a></div>
      </div>
      <div class="grid-2">
        <section class="panel section">
          <h2>From and to</h2>
          <dl class="kv">
            <dt>From</dt><dd><a href={ghUrl(p.course.org, ref.source.repo, ref.source.path, 'main', 'tree')} target="_blank" rel="noopener">{p.course.org}/{ref.source.repo}/{ref.source.path}</a></dd>
            <dt>To</dt><dd><a href={ghUrl(p.cohort.org, dest, destPath, 'main', 'tree')} target="_blank" rel="noopener">{p.cohort.org}/{dest}/{destPath}</a></dd>
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
          {st === 'planned' ? <OpButtons def={releaseEarly(scope, ref)} label="Release early" />
            : st === 'released' ? <><OpButtons def={releaseAgain(scope, ref)} label="Release again" /><OpOpen def={keepFuture(scope)} cls="btn quiet" label="Keep for future semesters" /></>
            : st === 'late' ? <OpButtons def={releaseNow(scope, ref)} label="Release now" />
            : <span class="footnote">Nothing to run until the problem above is fixed.</span>}
        </div>
        <div class="savebar"><span class="footnote">After changing the course copy, the student site may need an update.</span><a class="btn small quiet" href="#site">Update site on the Site page</a></div>
      </section>
    </>
  );
}

export function ReleaseScreen(p: CohortProps) {
  return (
    <WithStatus props={p} title="Release">
      {(r) => {
        const rel = (r.status.releases ?? []).find((x) => x.id === p.entry);
        return rel ? <ReleaseDetail {...r} rel={rel} /> : <NotFound what={`No release called ${p.entry} in the schedule.`} back="#schedule" />;
      }}
    </WithStatus>
  );
}
