// A semester's settings (decision 0043 rule 5): the Dashboard's summary panel, one line per
// setting with its effective value and source (status.json), and ONE modal with three folds -
// Assignments (the semester's run defaults), Releases (`autosync_released_edits`) and Semester
// dates - each heading carrying its line. Assignments and Releases are both `defaults:` keys of
// semester.yml and the dates are schedule.yml's, so a save is at most one commit per file
// (`saveSteps`). The Assignments page's Semester defaults button opens it on Assignments.

import { useEffect, useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { saveSteps, useSave, type Step } from '../edit/save';
import { compact, deepEqual } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { defaultsOf } from '../model/config';
import { fmtDate } from '../model/format';
import { CONFIG_REPO, SEMESTER_FILE } from '../model/names';
import { draftErrors, readDraft, type SemesterDraft } from '../model/scheduleEdit';
import { SOURCE_WORD, effectiveOf, semesterDefaults, semesterFile, valueWord, type Block } from '../model/settings';
import type { Status } from '../model/types';
import { defaultsTiers } from '../tiers/course';
import type { Tiers, Values } from '../tiers/types';
import { CheckLine, EditFile, Loading } from '../ui/bits';
import { SaveBar } from '../ui/edit';
import { Modal } from '../ui/Modal';
import { defaultsLine, semesterDefaultsAfter, semesterInherited } from './RunSettings';
import { SemesterForm, scheduleAfter, useSchedFile } from './Schedule';
import type { ReadyProps } from './types';

const RELEASE_KEY = 'autosync_released_edits';
/** Where the Site page and the New semester wizard open the modal (`#settings`). */
export const SETTINGS_HASH = '#settings';

/** The panel's lines, which are also the modal's fold headings: the run defaults, the release setting, the semester's dates. */
export function settingsLines(status: Status): { assignments: string; releases: string; dates: string } {
  const sync = effectiveOf(semesterDefaults(status), RELEASE_KEY);
  const sem = status.semester;
  const tz = sem?.timezone;
  return {
    assignments: defaultsLine(semesterDefaults(status)),
    releases: `Send fixes to released files automatically: ${valueWord(RELEASE_KEY, sync.value)} (${SOURCE_WORD[sync.source]}).`,
    dates: sem?.start && sem.end ? `Semester dates: ${fmtDate(sem.start, tz)} to ${fmtDate(sem.end, tz)}.` : 'Semester dates: not set yet.',
  };
}

/** The Dashboard's Semester settings panel: its lines and Edit. `open` (the `#settings` route) opens the modal on arrival. */
export function SemesterSettingsPanel({ p, open = false }: { p: ReadyProps; open?: boolean }) {
  const [shown, setShown] = useState(open);
  const draft = useState<Draft>(NO_DRAFT);
  useEffect(() => {
    if (open) setShown(true);
  }, [open]);
  const lines = settingsLines(p.status);
  const close = () => {
    setShown(false);
    if (open && typeof location !== 'undefined') location.hash = '#dashboard';
  };
  return (
    <section class="panel section">
      <div class="section-head">
        <h2>Semester settings</h2>
        <button class="btn small quiet" type="button" aria-haspopup="dialog" onClick={() => setShown(true)}>Edit</button>
      </div>
      <ul class="settings-lines">
        <li>{lines.assignments}</li>
        <li>{lines.releases}</li>
        <li>{lines.dates}</li>
      </ul>
      {shown ? <SemesterSettings p={p} draft={draft} onClose={close} /> : null}
    </section>
  );
}

/** The Assignments page's Semester defaults button: the modal, opened on Assignments. */
export function SemesterDefaults({ p }: { p: ReadyProps }) {
  const [open, setOpen] = useState(false);
  const draft = useState<Draft>(NO_DRAFT);
  return (
    <>
      <button class="btn quiet" type="button" aria-haspopup="dialog" onClick={() => setOpen(true)}>Semester defaults</button>
      {open ? <SemesterSettings p={p} draft={draft} expand="assignments" onClose={() => setOpen(false)} /> : null}
    </>
  );
}

const toValues = (b: Block): Values => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v === null ? undefined : v]));
const pick = (t: Tiers, keep: (k: string) => boolean): Tiers => Object.fromEntries(Object.entries(t).filter(([k]) => keep(k)));

/** The modal's unsaved edits, held by what opens it so an edit survives closing it: semester.yml's `defaults:` and the dates. null: unchanged. */
interface Draft {
  defaults: Values | null;
  dates: SemesterDraft | null;
}
const NO_DRAFT: Draft = { defaults: null, dates: null };

/** The modal: Assignments, Releases and Semester dates, each a fold; `expand` opens and focuses one. */
export function SemesterSettings({ p, draft, expand, onClose }: { p: ReadyProps; draft: [Draft, (d: Draft) => void]; expand?: 'assignments'; onClose: () => void }) {
  const env = useEnv();
  const org = p.cohort.org;
  const f = semesterFile(p.files, org);
  const sf = useSchedFile(p);
  const ready = sf && sf !== 'loading' && !sf.error ? sf : null;
  const [kept, keep] = draft;
  const [save, , setSave] = useSave(env);
  const setKept = (v: Draft) => {
    keep(v);
    setSave({ kind: 'idle' });
  };
  const focus = useRef<HTMLElement>(null);
  const lines = settingsLines(p.status);

  const yaml = f && f !== 'loading' && !f.error ? f : null;
  const before = yaml ? toValues(defaultsOf(yaml.doc)) : {};
  const inherited = semesterInherited(p);
  const tiers = defaultsTiers(inherited, before);
  const cur = kept.defaults ?? before;
  const defaultsDirty = kept.defaults !== null && !deepEqual(compact(kept.defaults), compact(before));
  const datesBefore = ready ? (readDraft(ready.doc, 'semester') as SemesterDraft) : null;
  const dates = kept.dates ?? datesBefore;
  const datesDirty = kept.dates !== null && !deepEqual(kept.dates, datesBefore);
  const dateErrors = dates ? draftErrors(dates) : {};
  const semExists = p.files.file(org, CONFIG_REPO, SEMESTER_FILE).kind !== 'absent';

  const doSave = async () => {
    if (Object.keys(fieldErrors(null, tiers, cur)).length || Object.keys(dateErrors).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const steps: [keyof Draft, Step][] = [];
    if (defaultsDirty && yaml) {
      const out = semesterDefaultsAfter(yaml, cur, inherited);
      if ('error' in out) return setSave({ kind: 'bad', text: out.error });
      steps.push(['defaults', { target: { owner: org, repo: CONFIG_REPO, path: SEMESTER_FILE }, text: out.text, sha: yaml.sha, opts: { message: 'semester: edit the semester’s defaults, from the DSL Teaching Console', statusRepo: [org, CONFIG_REPO] } }]);
    }
    if (datesDirty && ready && dates) {
      const out = scheduleAfter(ready, [dates]);
      if ('error' in out) return setSave({ kind: 'bad', text: out.error });
      steps.push(['dates', { target: { owner: org, repo: CONFIG_REPO, path: 'schedule.yml' }, text: out.text, sha: ready.sha, opts: { message: 'schedule: edit the semester dates, from the DSL Teaching Console', statusRepo: [org, CONFIG_REPO] } }]);
    }
    // One commit per file: semester.yml (Assignments and Releases together), then schedule.yml.
    // What a commit saved leaves the draft as soon as it lands, so a retry never writes it twice.
    let left = kept;
    const saved = (k: keyof Draft) => keep((left = { ...left, [k]: null }));
    const [first, second] = steps;
    if (await saveSteps(env, setSave, first?.[1] ?? null, second?.[1] ?? null, 'Defaults saved; the semester dates are not', () => saved(first[0])) && second) saved(second[0]);
  };

  const defaultsBody = (keys: Tiers, id: string) =>
    f === 'loading' ? <Loading what={`Reading ${SEMESTER_FILE}`} />
      : f === null ? <CheckLine cls="bad">Could not read {SEMESTER_FILE}.</CheckLine>
      : f.error ? <><CheckLine cls="bad">{SEMESTER_FILE} does not parse ({f.error}); fix it with Edit the file directly.</CheckLine><EditFile org={org} repo={CONFIG_REPO} path={SEMESTER_FILE} exists={semExists} /></>
      : <SchemaForm id={id} schema={null} tiers={keys} values={cur} onChange={(v) => setKept({ ...kept, defaults: v })} />;

  return (
    <Modal title="Semester settings" onClose={onClose} initial={expand ? focus : undefined}>
      <details class="fold settings-fold" open={expand === 'assignments'}>
        <summary ref={focus}><span>Assignments</span><span class="fold-line">{lines.assignments}</span></summary>
        <div class="fold-body">
          <p class="footnote">Every assignment this semester runs on these unless its own page says otherwise. Left empty, the value in grey applies: the course’s default, else the institution’s. <a class="textlink" href={`?course=${p.course.org}#details`}>Change the course’s defaults</a></p>
          {defaultsBody(pick(tiers, (k) => k !== RELEASE_KEY), 'sd')}
        </div>
      </details>
      <details class="fold settings-fold">
        <summary><span>Releases</span><span class="fold-line">{lines.releases}</span></summary>
        <div class="fold-body">{defaultsBody(pick(tiers, (k) => k === RELEASE_KEY), 'sr')}</div>
      </details>
      <details class="fold settings-fold">
        <summary><span>Semester dates</span><span class="fold-line">{lines.dates}</span></summary>
        <div class="fold-body">
          {sf === 'loading' ? <Loading what="Reading the schedule" /> : sf === null ? <p class="footnote">No schedule.yml yet.</p> : sf.error ? <CheckLine cls="bad">schedule.yml does not parse ({sf.error}); fix it with Edit the file directly.</CheckLine> : null}
          {dates ? <SemesterForm d={dates} set={(patch) => setKept({ ...kept, dates: { ...dates, ...patch } })} errors={dateErrors} /> : null}
        </div>
      </details>
      <SaveBar state={save} onSave={() => void doSave()} disabled={!defaultsDirty && !datesDirty} file={{ org, repo: CONFIG_REPO, path: SEMESTER_FILE, exists: semExists }}>
        <EditFile org={org} repo={CONFIG_REPO} path="schedule.yml" exists={sf !== null} />
      </SaveBar>
    </Modal>
  );
}
