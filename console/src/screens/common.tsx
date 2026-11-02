import type { ComponentChildren, VNode } from 'preact';
import { useMemo, useState } from 'preact/hooks';
import { parse } from 'yaml';
import { useEnv } from '../env';
import { invalidText, useSave, type SaveState } from '../edit/save';
import { YamlText } from '../edit/yamlText';
import { validGrading } from '../model/cascade';
import { cohortName } from '../model/discovery';
import { dayKey, zoned } from '../model/format';
import type { FileState, TreeState } from '../model/files';
import { CONFIG_REPO, COURSE_REPO } from '../model/names';
import { DEFAULT_TIMEZONE, LEGACY_SUBMIT_VIA } from '../model/policy';
import type { Operation, Status } from '../model/types';
import { checkNow, keepFuture, previewNext, type Scope } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { mergeOperations, type OpDef } from '../ops/session';
import { CheckLine, Loading, Soon, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import type { CohortProps, CourseProps, ReadyProps } from './types';

export { cohortName };

export const CHECK_NOW_SOON = 'Sign in to refresh.';

/** The `?` beside every Refresh. */
export const REFRESH_HINT = 'Reads the course and its semesters from GitHub again and re-runs every check. Automation does the same every night.';

export const tzOf = (s: Status) => s.semester?.timezone ?? DEFAULT_TIMEZONE;
export const yearOf = (now: number, tz: string) => zoned(new Date(now).toISOString(), tz).y;
export const todayOf = (now: number, tz: string) => dayKey(new Date(now).toISOString(), tz);

/** What reading a template's files needs: the course (its org) and the file cache. */
type TemplateReader = Pick<ReadyProps, 'course' | 'files'>;

const configText = (p: TemplateReader, template: string): string | null => {
  const f = p.files.file(p.course.org, template, 'grading_config.yml', 'solution');
  return f.kind === 'ready' ? f.text : null;
};

/** grading_config.yml parsed, `submit_via` in its current spelling (course.canonical_submit_via), so every shape test reads one word. */
function parseConfig(text: string | null): Record<string, unknown> {
  if (text === null) return {};
  try {
    const d = parse(text);
    if (!d || typeof d !== 'object') return {};
    const cfg = d as Record<string, unknown>;
    const via = typeof cfg.submit_via === 'string' ? cfg.submit_via.trim().toLowerCase() : null;
    return via === null ? cfg : { ...cfg, submit_via: LEGACY_SUBMIT_VIA[via] ?? via };
  } catch {
    return {};
  }
}

/** A template's grading_config.yml as parsed, or {} while it is missing or unreadable. */
export function gradingConfig(p: ReadyProps, template: string): Record<string, unknown> {
  return parseConfig(configText(p, template));
}

/** gradingConfig for a component: parsed again only when the file's text changes. */
export function useGradingConfig(p: TemplateReader, template: string): Record<string, unknown> {
  const text = configText(p, template);
  return useMemo(() => parseConfig(text), [text]);
}

/** A template's grading_config.yml to edit: the file, its parsed config, its save line, and `commit`. */
export interface GradingConfigEdit {
  file: FileState;
  cfg: Record<string, unknown>;
  save: SaveState;
  setSave: (s: SaveState) => void;
  /** Applies `edit` to the file's text, checks the result against the schema (saying what is wrong
   *  on the save line), and saves it; whether it saved. */
  commit: (edit: (y: YamlText) => void, message: string) => Promise<boolean>;
}

/**
 * Edits a template's grading_config.yml on its `solution` branch: the template's settings page and
 * the Marks tab's points per question both write through here, so the two cannot drift on the
 * branch, the schema check or the status repo a save reports to.
 */
export function useGradingConfigEdit(p: TemplateReader, template: string): GradingConfigEdit {
  const env = useEnv();
  const org = p.course.org;
  const file = p.files.file(org, template, 'grading_config.yml', 'solution');
  const cfg = useGradingConfig(p, template);
  const [save, run, setSave] = useSave(env);
  const commit = async (edit: (y: YamlText) => void, message: string) => {
    if (file.kind !== 'ready') return false;
    const y = new YamlText(file.text);
    edit(y);
    if (!validGrading(y.toJS())) {
      setSave({ kind: 'bad', text: invalidText('grading_config.yml', validGrading) });
      return false;
    }
    return run({ owner: org, repo: template, path: 'grading_config.yml', branch: 'solution' }, y.text, file.sha, { message: `${message}, from the DSL Teaching Console`, statusRepo: [org, COURSE_REPO] });
  };
  return { file, cfg, save, setSave, commit };
}

/** A template's files a question can be marked from: every file but the dot-files, [] until the tree is read. */
export function questionFiles(tree: TreeState): string[] {
  return tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir && !x.path.startsWith('.')).map((x) => x.path) : [];
}

/** Who an operation on the course acts for. */
export function courseScope(p: Pick<CourseProps, 'course'>): Scope {
  return { courseOrg: p.course.org, where: p.course.name };
}

/** Who an operation on this semester acts for. */
export function cohortScope(p: Pick<CohortProps, 'course' | 'cohort'>): Scope {
  return { courseOrg: p.course.org, cohortOrg: p.cohort.org, where: p.cohort.termLabel };
}

/** Refresh: read everything again and re-run every check (semester.check), with its `?`. */
export function CheckNow({ small, p, label, about }: { small?: boolean; p?: Pick<CohortProps, 'course' | 'cohort'>; label?: string; about?: { text: string; doc: string } }) {
  const hint = about ? <Hint label={`About ${label ?? 'Refresh'}`} doc={about.doc}>{about.text}</Hint> : <Hint label="About Refresh">{REFRESH_HINT}</Hint>;
  if (!p) return <><Soon label="Refresh" cls={small ? 'btn small' : 'btn'} title={CHECK_NOW_SOON} />{hint}</>;
  return <><OpButtons def={checkNow(cohortScope(p))} small={small} label={label} />{hint}</>;
}

/** The operations of this semester: the status file's record plus this session's runs. */
export function useOperations(fromStatus: Operation[] | undefined, cohortOrg: string): Operation[] {
  const env = useEnv();
  const mine = (env?.ops.runs.value ?? []).filter((r) => r.cohort === cohortOrg);
  return mergeOperations(fromStatus ?? [], mine);
}

function ExportInfo({ org }: { org: string }) {
  const f = (path: string) => ghUrl(org, CONFIG_REPO, path, 'main', path.includes('.') ? 'blob' : 'tree');
  const row = (t: string, sub: string, path: string) => (
    <li><span class="r-title">{t}</span><span class="r-sub">{sub}</span><span class="r-side"><a class="btn small quiet" href={f(path)} target="_blank" rel="noopener">Open <Ext /></a></span></li>
  );
  return (
    <>
      <ul class="rows">
        {row('Roster', 'students.csv', 'students.csv')}
        {row('Semester grades', 'Written each time marks are returned', 'registrar')}
        {row('Schedule', 'schedule.yml', 'schedule.yml')}
      </ul>
      <p class="footnote">Each opens on GitHub, where you can download it.</p>
    </>
  );
}

/** The semester header's overflow menu (revision brief section 9). */
export function MoreMenu({ p }: { p?: Pick<CohortProps, 'course' | 'cohort'> }) {
  const [open, setOpen] = useState(false);
  const env = useEnv();
  const scope = p ? cohortScope(p) : null;
  const go = (def: OpDef | null) => {
    setOpen(false);
    if (def) env?.ops.open(def);
  };
  const exportDef = scope && p ? { ...checkNow(scope), key: 'export', name: 'Export', title: 'Download a copy', intro: '', info: <ExportInfo org={p.cohort.org} /> } : null;
  return (
    <div class="menu-wrap">
      <button class="btn outline" type="button" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>More</button>
      <div class="popmenu" hidden={!open} role="menu">
        <button type="button" role="menuitem" disabled={!scope} onClick={() => go(scope && previewNext(scope))}>Preview the next automatic run</button>
        <button type="button" role="menuitem" disabled={!scope} onClick={() => go(scope && keepFuture(scope))}>Keep for future semesters</button>
        <button type="button" role="menuitem" disabled={!scope} onClick={() => go(exportDef)}>Export <span class="pm-sub">roster, marks, schedule</span></button>
        <a href="#schedule-archive" role="menuitem" onClick={() => setOpen(false)}>Archive this semester</a>
      </div>
    </div>
  );
}

/** A status older than the files it was computed from. */
export function StaleNote({ stale }: { stale: string[] }) {
  if (!stale.length) return null;
  const files = stale.map((s) => s.replace(/^course\//, '')).join(', ');
  return (
    <p class="note" style="margin-bottom:18px">
      <b>Not yet checked.</b> {files} changed since this semester was last checked, so what you see may be out of date. Refresh brings it up to date.
    </p>
  );
}

/** The page every semester screen shows before an org has been refreshed on the new engine; an archived one never will be. */
export function NotComputed({ title, p, archived }: { title: string; p?: Pick<CohortProps, 'course' | 'cohort'>; archived?: boolean }) {
  if (archived)
    return (
      <>
        <div class="page-head"><div><h2 class="h1">{title}</h2><p class="lede">Archived: every repo is read-only.</p></div></div>
        <section class="panel section stub">
          <h2>No status</h2>
          <p>This semester was archived before the console's engine checked it, so there is no status to show. Students keep access, and nothing was deleted.</p>
        </section>
      </>
    );
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">{title}</h2>
          <p class="lede">Status not computed yet.</p>
        </div>
        <div class="actions"><CheckNow p={p} /></div>
      </div>
      <section class="panel section stub">
        <h2>Status not computed yet</h2>
        <p>This semester has not been checked since it moved to the console's engine, so there is no status to show: no problems list, no semester strip, no counts.</p>
        <p>Refresh computes it. Automation also computes it every night.</p>
      </section>
    </>
  );
}

/**
 * Render a semester screen once its status is ready, or the loading, absent, invalid and error
 * states every semester screen shares.
 */
export function WithStatus({
  props,
  title,
  children,
}: {
  props: CohortProps;
  title: string;
  children: (p: ReadyProps) => VNode | ComponentChildren;
}) {
  const l = props.loaded;
  if (l.kind === 'loading') return <Loading what="Reading the semester's status" />;
  if (l.kind === 'absent') return <NotComputed title={title} p={props} archived={l.archived} />;
  if (l.kind === 'invalid' || l.kind === 'error')
    return (
      <>
        <div class="page-head"><div><h2 class="h1">{title}</h2></div><div class="actions"><CheckNow p={props} /></div></div>
        <section class="panel section">
          <CheckLine cls="bad">
            {l.kind === 'invalid' ? `The semester's status file does not match the expected shape: ${l.errors.slice(0, 3).join('; ')}.` : `Could not read the semester's status: ${l.message}`}
          </CheckLine>
        </section>
      </>
    );
  return (
    <>
      <StaleNote stale={l.stale} />
      {children({ ...props, status: l.status })}
    </>
  );
}
