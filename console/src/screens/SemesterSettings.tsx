// A semester's defaults (decision 0043 rule 5): the Semester defaults screen (`#defaults`), one
// form of three folds, all open - Assignments (the semester's run defaults), Releases
// (`autosync_released_edits`) and Semester dates - each heading carrying its one-line summary
// with its effective value and source (status.json). Assignments and Releases are both
// `defaults:` keys of semester.yml and the dates are schedule.yml's, so a save is at most one
// commit per file (`saveSteps`). The timezone is the `defaults:` one (decision 0039), in the
// Assignments fold. Releases also holds where a release may land and where it lands by default
// (plan 2026-11-04, decision 7): the Release into chips (`release_destinations`, the course's
// grey) and the Default destination (`materials_repo`) over them. `#defaults-<fold>` focuses that fold's heading on arrival (New semester's
// card; the old `#settings` route lands here). The same form also opens in a modal, at the fold
// that fits where it was opened (the Assignments, Schedule and Site pages), the others folded.

import { useEffect, useRef, useState } from 'preact/hooks';
import { Hint } from '../ui/Hint';
import { useEnv } from '../env';
import { saveSteps, useSave, type Step } from '../edit/save';
import { compact, deepEqual } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { defaultsOf } from '../model/config';
import { fmtDate } from '../model/format';
import { CONFIG_REPO, SEMESTER_FILE } from '../model/names';
import { draftErrors, readDraft, type SemesterDraft } from '../model/scheduleEdit';
import { SOURCE_NAME, SOURCE_WORD, effectiveOf, semesterDefaults, semesterFile, valueWord } from '../model/settings';
import { releaseIntoOf, ReleaseInto } from '../ui/ReleaseChips';
import type { Status } from '../model/types';
import { defaultsTiers } from '../tiers/course';
import type { Tiers, Values } from '../tiers/types';
import type { RefObject } from 'preact';
import { CheckLine, EditFile, Loading } from '../ui/bits';
import { SaveBar } from '../ui/edit';
import { Modal } from '../ui/Modal';
import { defaultsLine, semesterDefaultsAfter, semesterInherited, toValues } from './RunSettings';
import { SemesterForm, scheduleAfter, useSchedFile } from './Schedule';
import { WithStatus } from './common';
import type { CohortProps, ReadyProps } from './types';

const RELEASE_KEY = 'autosync_released_edits';
const DEST_KEY = 'materials_repo';
const INTO_KEY = 'release_destinations';
/** The Releases fold's fields; every other `defaults:` field is the Assignments fold's. */
const RELEASE_KEYS = [RELEASE_KEY, DEST_KEY, INTO_KEY];
type Fold = 'assignments' | 'releases' | 'dates';
const FOLDS: Fold[] = ['assignments', 'releases', 'dates'];
/** The link to the Semester defaults screen, with one fold's heading focused on arrival. */
export const defaultsHref = (fold?: Fold) => `#defaults${fold ? `-${fold}` : ''}`;

/** The fold headings' one-line summaries: the run defaults, the release settings, the semester's dates. */
export function settingsLines(status: Status): { assignments: string; releases: string; dates: string } {
  const sync = effectiveOf(semesterDefaults(status), RELEASE_KEY);
  const dest = effectiveOf(semesterDefaults(status), DEST_KEY);
  const sem = status.semester;
  const tz = sem?.timezone;
  return {
    assignments: defaultsLine(semesterDefaults(status)),
    releases: `Send fixes to released files automatically: ${valueWord(RELEASE_KEY, sync.value)} (${SOURCE_WORD[sync.source]}). Lands in ${valueWord(DEST_KEY, dest.value)} (${SOURCE_WORD[dest.source]}).`,
    dates: sem?.start && sem.end ? `Semester dates: ${fmtDate(sem.start, tz)} to ${fmtDate(sem.end, tz)}.` : 'Semester dates: not set yet.',
  };
}

const pick = (t: Tiers, keep: (k: string) => boolean): Tiers => Object.fromEntries(Object.entries(t).filter(([k]) => keep(k)));

/** The edits not saved yet: semester.yml's `defaults:` and the dates. null: unchanged. */
interface Draft {
  defaults: Values | null;
  dates: SemesterDraft | null;
}
const NO_DRAFT: Draft = { defaults: null, dates: null };

/** The late keys' `?`: what marking still lets an instructor do with each late unit. */
const LATE_HINT = 'A default. When marking, each late submission’s penalty can be switched off or replaced with your own percentage.';
const withHints = (t: Tiers): Tiers => Object.fromEntries(Object.entries(t).map(([k, v]) => [k, k === 'late_penalty_per_day' || k === 'late_window_days' ? { ...v, hint: { text: LATE_HINT } } : v]));

/** The Semester defaults screen. `entry` (`#defaults-assignments`) names the fold to focus on arrival. */
function DefaultsPage({ p }: { p: ReadyProps }) {
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">Semester defaults <Hint doc="04-new-cohort-org.md">Each value comes from the institution, then the course, then this semester; the semester’s wins. An empty field shows the inherited value in grey and where it comes from. Most can be changed again where they are used, such as on an assignment’s own page.</Hint></h2>
          <p class="lede">What every assignment this semester runs on, how fixes to released files are sent, and the semester’s dates.</p>
        </div>
      </div>
      <section class="panel section">
        <DefaultsForm p={p} at={FOLDS.find((f) => f === p.entry)} />
      </section>
    </>
  );
}

export function SemesterDefaultsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Semester defaults">{(r) => <DefaultsPage p={r} />}</WithStatus>;
}

type DraftState = [Draft, (d: Draft) => void];

/**
 * The form in a modal at `at`: that fold open and focused, the other two folded. The opener
 * holds the draft, so an unsaved edit survives closing and reopening the modal. `modal` goes
 * where the opener can render it outside any styled text (a footnote's type would cascade in).
 */
export function useDefaultsModal(p: ReadyProps, at: Fold) {
  const [shown, setShown] = useState(false);
  const draft = useState<Draft>(NO_DRAFT);
  const focus = useRef<HTMLElement>(null);
  const modal = shown ? (
    <Modal title="Semester defaults" onClose={() => setShown(false)} initial={focus}>
      <p class="footnote"><a class="textlink" href={defaultsHref(at)}>Open the full page</a></p>
      <DefaultsForm p={p} at={at} folded draft={draft} focusRef={focus} />
    </Modal>
  ) : null;
  return { open: () => setShown(true), modal };
}

/** A Semester defaults button that opens the modal at `at`. */
export function SemesterDefaultsButton({ p, at }: { p: ReadyProps; at: Fold }) {
  const m = useDefaultsModal(p, at);
  return (
    <>
      <button class="btn quiet" type="button" aria-haspopup="dialog" onClick={m.open}>Semester defaults</button>
      {m.modal}
    </>
  );
}

/**
 * Assignments, Releases and Semester dates, each a fold, and their SaveBar. All open, unless
 * `folded` (the modal): then only `at` is. `at`'s heading takes the focus on arrival, through
 * `focusRef` when the caller focuses it (the modal does). `draft`, when given, is held by the
 * caller so it outlives the form.
 */
function DefaultsForm({ p, at, folded = false, draft, focusRef }: { p: ReadyProps; at?: Fold; folded?: boolean; draft?: DraftState; focusRef?: RefObject<HTMLElement> }) {
  const env = useEnv();
  const org = p.cohort.org;
  const f = semesterFile(p.files, org);
  const sf = useSchedFile(p);
  const ready = sf && sf !== 'loading' && !sf.error ? sf : null;
  const own = useState<Draft>(NO_DRAFT);
  const [kept, keep] = draft ?? own;
  const [save, , setSave] = useSave(env);
  const setKept = (v: Draft) => {
    keep(v);
    setSave({ kind: 'idle' });
  };
  const lines = settingsLines(p.status);
  const ownFocus = useRef<HTMLElement>(null);
  const head = (fold: Fold) => (fold === at ? focusRef ?? ownFocus : undefined);
  const isOpen = (fold: Fold) => !folded || fold === at;
  useEffect(() => {
    const el = focusRef ? null : ownFocus.current;
    if (!el) return;
    el.focus();
    el.scrollIntoView?.({ block: 'start' });
  }, [at]);

  const yaml = f && f !== 'loading' && !f.error ? f : null;
  const before = yaml ? toValues(defaultsOf(yaml.doc)) : {};
  const inherited = semesterInherited(p);
  const cur = kept.defaults ?? before;
  // Release into: what the course gives (grey), then the semester's own; the default destination chooses among them.
  const rel = releaseIntoOf({ inherited, nearest: 'the course', own: cur[INTO_KEY], org, repos: p.files.repos(org), status: p.status });
  const tiers = withHints(defaultsTiers(inherited, before, rel.into));
  const intoSource = rel.fixed.some((c) => c.from === SOURCE_NAME.course) ? SOURCE_WORD.course : SOURCE_WORD.institution;
  const defaultsDirty = kept.defaults !== null && !deepEqual(compact(kept.defaults), compact(before));
  const datesBefore = ready ? (readDraft(ready.doc, 'semester') as SemesterDraft) : null;
  const dates = kept.dates ?? datesBefore;
  const datesDirty = kept.dates !== null && !deepEqual(kept.dates, datesBefore);
  const dateErrors = dates ? draftErrors(dates) : {};
  const semExists = p.files.file(org, CONFIG_REPO, SEMESTER_FILE).kind !== 'absent';

  const doSave = async () => {
    // Only what was edited is checked: a fault in a fold left alone never blocks the other's save.
    if ((defaultsDirty && Object.keys(fieldErrors(null, tiers, cur)).length) || (datesDirty && Object.keys(dateErrors).length)) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    let defaultsStep: Step | null = null, datesStep: Step | null = null;
    if (defaultsDirty && yaml) {
      const out = semesterDefaultsAfter(yaml, cur, inherited);
      if ('error' in out) return setSave({ kind: 'bad', text: out.error });
      defaultsStep = { target: { owner: org, repo: CONFIG_REPO, path: SEMESTER_FILE }, text: out.text, sha: yaml.sha, opts: { message: 'semester: edit the semester’s defaults, from the DSL Teaching Console', statusRepo: [org, CONFIG_REPO] } };
    }
    if (datesDirty && ready && dates) {
      const out = scheduleAfter(ready, [dates]);
      if ('error' in out) return setSave({ kind: 'bad', text: out.error });
      datesStep = { target: { owner: org, repo: CONFIG_REPO, path: 'schedule.yml' }, text: out.text, sha: ready.sha, opts: { message: 'schedule: edit the semester dates, from the DSL Teaching Console', statusRepo: [org, CONFIG_REPO] } };
    }
    // One commit per file: semester.yml (Assignments and Releases together), then schedule.yml.
    // The defaults leave the draft as soon as their commit lands, so a retry never writes them twice.
    const afterDefaults = { ...kept, defaults: null };
    const first = defaultsStep ?? datesStep, second = defaultsStep ? datesStep : null;
    if (await saveSteps(env, setSave, first, second, 'Defaults saved; the semester dates are not', () => keep(defaultsStep ? afterDefaults : NO_DRAFT))) keep(NO_DRAFT);
  };

  const defaultsForm = (keys: Tiers, id: string) => (yaml ? <SchemaForm id={id} schema={null} tiers={keys} values={cur} onChange={(v) => setKept({ ...kept, defaults: v })} /> : null);

  return (
    <>
      {/* semester.yml holds both the Assignments and the Releases fields: said once, above them. */}
      {f === 'loading' ? <Loading what={`Reading ${SEMESTER_FILE}`} />
        : f === null ? <CheckLine cls="bad">Could not read {SEMESTER_FILE}.</CheckLine>
        : f.error ? <CheckLine cls="bad">{SEMESTER_FILE} does not parse ({f.error}); fix it with Edit the file directly.</CheckLine>
        : null}
      <details class="fold settings-fold" open={isOpen('assignments')}>
        <summary ref={head('assignments')}><span>Assignments</span><span class="fold-line">{lines.assignments}</span></summary>
        <div class="fold-body">
          <p class="footnote">Every assignment this semester runs on these unless its own page says otherwise. Left empty, the value in grey applies: the course’s default, else the institution’s. <a class="textlink" href={`?course=${p.course.org}#details`}>Change the course’s defaults</a></p>
          {defaultsForm(pick(tiers, (k) => !RELEASE_KEYS.includes(k)), 'sd')}
        </div>
      </details>
      <details class="fold settings-fold" open={isOpen('releases')}>
        <summary ref={head('releases')}><span>Releases</span><span class="fold-line">{lines.releases}</span></summary>
        <div class="fold-body rel-section">
          {defaultsForm(pick(tiers, (k) => k === RELEASE_KEY), 'sr')}
          {yaml ? (
            <ReleaseInto id="sr-into" fixed={rel.fixed} own={rel.own} defaultLabel={`${intoSource}: ${rel.into.join(', ')}`}
              hint="The repos of this semester a release may write into: the course’s list, plus any added here. The course’s ones are greyed; change them on Course details."
              rows={rel.rows} reserved={rel.reserved} onChange={(list) => setKept({ ...kept, defaults: { ...cur, [INTO_KEY]: list } })} />
          ) : null}
          {defaultsForm(pick(tiers, (k) => k === DEST_KEY), 'sr')}
        </div>
      </details>
      <details class="fold settings-fold" open={isOpen('dates')}>
        <summary ref={head('dates')}><span>Semester dates</span><span class="fold-line">{lines.dates}</span></summary>
        <div class="fold-body">
          {sf === 'loading' ? <Loading what="Reading the schedule" /> : sf === null ? <p class="footnote">No schedule.yml yet.</p> : sf.error ? <CheckLine cls="bad">schedule.yml does not parse ({sf.error}); fix it with Edit the file directly.</CheckLine> : null}
          {dates ? <SemesterForm d={dates} set={(patch) => setKept({ ...kept, dates: { ...dates, ...patch } })} errors={dateErrors} /> : null}
        </div>
      </details>
      <SaveBar state={save} onSave={() => void doSave()} disabled={!defaultsDirty && !datesDirty} file={{ org, repo: CONFIG_REPO, path: SEMESTER_FILE, exists: semExists }}>
        <EditFile org={org} repo={CONFIG_REPO} path="schedule.yml" exists={sf !== null} />
      </SaveBar>
    </>
  );
}
