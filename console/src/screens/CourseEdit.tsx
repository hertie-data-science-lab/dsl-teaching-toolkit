// Course-side editors: S3 Course details (dsl-course.yml), S20 Public OpenCourse website
// (opencourse.yml), S19 Course materials repo settings (materials.yml, .releaseignore).

import { useRef, useState } from 'preact/hooks';
import materialsSchema from '../../schemas/materials.schema.json';
import opencourseSchema from '../../schemas/opencourse.schema.json';
import { useEnv, type Env } from '../env';
import { badgeFiles, denylisted, neverMaterial } from '../edit/badges';
import { invalidText, useSave, type SaveState } from '../edit/save';
import { YamlText, compact, deepEqual, obj } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { termRank } from '../model/catalogue';
import { KIND_LABEL } from '../model/format';
import { sameHandle } from '../model/people';
import { ALIAS_DEPTH, DEFAULT_SYLLABUS, FOLDER_KINDS, MATERIALS_FILE, NOTHING_DECLARED, aliasKind, inferKind, readDeclared, withMark } from '../model/materialsRules';
import { validCourse } from '../model/cascade';
import { validator } from '../model/validate';
import { generateSyllabus, publishWebsite, type Scope } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { ABOUT, COURSE_FACTS, courseDefaultTiers } from '../tiers/course';
import { formatsList } from '../tiers/grading';
import { WEBSITE_OFF_LIVE, publishWebsite as publishTiers } from '../tiers/ops';
import type { Values } from '../tiers/types';
import { CheckLine, EditFile, Loading } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveBar } from '../ui/edit';
import { PatternTree } from '../ui/PatternTree';
import { Check, Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { courseOf, MaterialsChecklist } from './Course';
import { ItemRow, RepoChip, RepoWhy } from '../ui/SetupPanel';
import { materialsReadiness } from '../model/readiness';
import { courseScope } from './common';
import type { CourseProps } from './types';
import { COURSE_REPO, OPENCOURSE_FILE } from '../model/names';


/** The course's newest semester, for the course-page ops the engine runs per semester. */
export function newestScope(p: Pick<CourseProps, 'course'>): Scope | null {
  const k = p.course.cohorts[0];
  return k ? { courseOrg: p.course.org, cohortOrg: k.org, where: k.termLabel } : null;
}


// --------------------------------------------------------------------------- details

export interface Admin {
  github_handle: string;
  email: string;
  start: string;
  end: string;
}

export function detailsOf(meta: Record<string, unknown>) {
  const ad = obj(meta.assignment_defaults);
  const people = obj(meta.people);
  const admins = (Array.isArray(people.course_admins) ? people.course_admins : []).map((a) => {
    const x = obj(a);
    return { github_handle: String(x.github_handle ?? ''), email: String(x.email ?? ''), start: String(x.start ?? ''), end: String(x.end ?? '') };
  });
  const links = Array.isArray(meta.site_link_extensions) ? meta.site_link_extensions.map(String).join(', ') : typeof meta.site_link_extensions === 'string' ? meta.site_link_extensions : '';
  return {
    about: compact({ course_name: meta.course_name, course_code: meta.course_code, course_description: meta.course_description, contact: meta.contact, licence: meta.licence }),
    defaults: compact({ ...ad, formats: formatsList(ad.formats)[0] }),
    admins,
    links,
  };
}

export type Details = ReturnType<typeof detailsOf>;

/** The course's `formats` list with its first (runnable) entry replaced: the others stay. Cleared means the toolkit's default, so no list. */
export function formatsAfter(meta: Record<string, unknown>, first: unknown): string[] | undefined {
  if (!first) return undefined;
  const rest = formatsList(obj(meta.assignment_defaults).formats).slice(1);
  return [String(first), ...rest.filter((f) => f !== first)];
}

/** Write only what changed, key by key, into dsl-course.yml. */
export function writeDetails(y: YamlText, before: Details, after: Details, meta: Record<string, unknown>): void {
  for (const k of Object.keys({ ...before.about, ...after.about })) if (!deepEqual(before.about[k], after.about[k])) y.assign([k], after.about[k]);
  for (const k of Object.keys({ ...before.defaults, ...after.defaults }))
    if (!deepEqual(before.defaults[k], after.defaults[k])) y.assign(['assignment_defaults', k], k === 'formats' ? formatsAfter(meta, after.defaults[k]) : after.defaults[k]);
  if (!deepEqual(before.admins, after.admins)) {
    const raws = (Array.isArray(obj(meta.people).course_admins) ? (obj(meta.people).course_admins as unknown[]) : []).map(obj);
    const rawBy = (h: string) => raws.find((r) => sameHandle(String(r.github_handle ?? ''), h)) ?? {};
    y.assign(['people', 'course_admins'], after.admins.filter((a) => a.github_handle.trim()).map((a) => ({
      ...rawBy(a.github_handle), github_handle: a.github_handle.trim(), email: a.email.trim() || undefined, start: a.start || undefined, end: a.end || undefined,
    })));
  }
  if (before.links !== after.links) {
    const list = after.links.split(/[,\s]+/).map((s) => s.trim().replace(/^\./, '')).filter(Boolean);
    y.assign(['site_link_extensions'], list.length ? list : undefined);
  }
  const emptyMap = (k: string) => {
    const v = y.get([k]);
    if (v && typeof v === 'object' && !Object.keys(v as object).length) y.delete([k]);
  };
  emptyMap('assignment_defaults');
}

/** dsl-course.yml after `after`, or why it would not be valid against the exported schema. */
export function courseFileAfter(text: string, before: Details, after: Details, meta: Record<string, unknown>): { text: string } | { error: string } {
  const out = new YamlText(text);
  writeDetails(out, before, after, meta);
  return validCourse(out.toJS()) ? { text: out.text } : { error: invalidText('dsl-course.yml', validCourse) };
}

/**
 * Save dsl-course.yml with `after` written over `before`: refused on the save line, before any
 * write, while the course's names are unconfirmed, while `errs` marks a field, when the file
 * would not be valid, or when a new admin has no GitHub account. True once written.
 */
export async function saveCourseFile(
  env: Env | null,
  [run, report]: [ReturnType<typeof useSave>[1], (s: SaveState) => void],
  o: { org: string; migrated?: boolean; file: { text: string; sha: string }; meta: Record<string, unknown>; before: Details; after: Details; errs: Record<string, string> },
): Promise<boolean> {
  const refuse = (text: string) => (report({ kind: 'bad', text }), false);
  if (o.migrated === false) return refuse('Not saved: the console has not yet confirmed this course uses the current names.');
  if (Object.keys(o.errs).length) return refuse('Fix the fields marked in red first.');
  const out = courseFileAfter(o.file.text, o.before, o.after, o.meta);
  if ('error' in out) return refuse(out.error);
  const missing = await missingAdmin(env, o.before.admins, o.after.admins);
  if (missing) return refuse(`There is no GitHub account called ${missing}.`);
  return run({ owner: o.org, repo: COURSE_REPO, path: 'dsl-course.yml' }, out.text, o.file.sha, { message: 'course: edit the course details, from the DSL Teaching Console', statusRepo: [o.org, COURSE_REPO] });
}

/** The first new admin handle with no GitHub account, or null (a failed lookup counts as there). */
export async function missingAdmin(env: Env | null, before: Admin[], after: Admin[]): Promise<string | null> {
  if (!env) return null;
  for (const a of after.filter((x) => x.github_handle.trim() && !before.some((b) => sameHandle(b.github_handle, x.github_handle)))) {
    let ok = true;
    try {
      ok = await env.client.userExists(a.github_handle.trim());
    } catch {
      ok = true;
    }
    if (!ok) return a.github_handle;
  }
  return null;
}

/** Course admins as rows: handle and email, optional dates, add and remove. */
export function AdminRows({ admins, onChange, id }: { admins: Admin[]; onChange: (a: Admin[]) => void; id: string }) {
  const admin = (i: number, patch: Partial<Admin>) => onChange(admins.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  return (
    <>
      {admins.map((a, i) => (
        <div class="deploy">
          <div class="row-2">
            <div class="field"><label for={`${id}-h${i}`}>GitHub handle</label><input type="text" id={`${id}-h${i}`} value={a.github_handle} onInput={(e) => admin(i, { github_handle: (e.target as HTMLInputElement).value })} /><p class="why">Checked: the account must exist.</p></div>
            <div class="field"><label for={`${id}-e${i}`}>Email</label><input type="email" id={`${id}-e${i}`} value={a.email} onInput={(e) => admin(i, { email: (e.target as HTMLInputElement).value })} /><p class="why">Fault mails go here; without one, the lab’s list is used.</p></div>
          </div>
          <details class="fold" open={!!(a.start || a.end)}>
            <summary>Dates</summary>
            <div class="fold-body"><div class="row-2">
              <div class="field"><label for={`${id}-s${i}`}>From <span class="default">optional</span></label><input type="date" id={`${id}-s${i}`} value={a.start} onInput={(e) => admin(i, { start: (e.target as HTMLInputElement).value })} /></div>
              <div class="field"><label for={`${id}-n${i}`}>Until <span class="default">optional</span></label><input type="date" id={`${id}-n${i}`} value={a.end} onInput={(e) => admin(i, { end: (e.target as HTMLInputElement).value })} /></div>
            </div></div>
          </details>
          {admins.length > 1 ? <div><button class="btn small quiet" type="button" onClick={() => onChange(admins.filter((_, j) => j !== i))}>Remove</button></div> : null}
        </div>
      ))}
      <div><button class="btn small quiet" type="button" onClick={() => onChange([...admins, { github_handle: '', email: '', start: '', end: '' }])}>Add an admin</button></div>
    </>
  );
}

/** The live public website. */
export const websiteUrl = (org: string) => `https://${org}.github.io`;

/**
 * The public website's on/off as a switch: a flip writes `enabled` in opencourse.yml at once
 * (seeding the file when there is none), apart from the page's Save.
 */
export function WebsiteSwitch(p: CourseProps) {
  const { course } = p;
  const env = useEnv();
  const [save, runSave, setSave] = useSave(env);
  const [pending, setPending] = useState(false);
  const file = p.files.file(course.org, COURSE_REPO, OPENCOURSE_FILE);
  const y = file.kind === 'ready' ? new YamlText(file.text) : null;
  const broken = !!y?.errors.length;
  const before = websiteOf(y && !broken ? obj(y.toJS()) : {});
  const busy = save.kind === 'busy';
  // Only a file that was read (or read as absent) may be written: never seed over one not read.
  const known = file.kind === 'ready' || file.kind === 'absent';
  const flip = async (on: boolean) => {
    if (!known) return;
    const repos = (courseOf(p)?.materials ?? []).map((m) => m.repo);
    const out = websiteFileAfter(file.kind === 'ready' ? file.text : null, before, { ...before, enabled: on, source_repo: before.source_repo || (on ? newestRepo(repos) : null) || '' });
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    setPending(on);
    await runSave({ owner: course.org, repo: COURSE_REPO, path: OPENCOURSE_FILE }, out.text, file.kind === 'ready' ? file.sha : null, {
      message: `course: turn the public website ${on ? 'on' : 'off'}, from the DSL Teaching Console`, statusRepo: [course.org, COURSE_REPO],
    });
  };
  const word =
    file.kind === 'loading' ? 'Not known yet.'
    : file.kind === 'error' ? 'Could not read the website settings.'
    : broken ? `${OPENCOURSE_FILE} does not parse; fix it in Manage.`
    : p.migrated === false ? 'Not yet: the console has not confirmed this course uses the current names.'
    : busy ? save.text
    : before.enabled ? 'On: updates daily' : 'Off';
  return (
    <div class="field web-switch">
      <label class="switch">
        <input type="checkbox" role="switch" id="cd-web" aria-describedby="cd-web-state" checked={busy ? pending : before.enabled} disabled={busy || broken || !known || p.migrated === false} onChange={(e) => void flip((e.target as HTMLInputElement).checked)} />
        <span>Public OpenCourse website <span class="chip">Beta</span></span>
      </label>
      <span class="footnote" id="cd-web-state" aria-live="polite">{word}</span> <a class="textlink" href="#website">Manage</a>
      {save.kind === 'bad' ? <CheckLine cls="bad">{save.text}</CheckLine> : null}
    </div>
  );
}

/** The one dsl-course.yml key the semester sites read for which files a row links (`site._link_extensions`). */
function LinkKindsField({ id, value, onInput }: { id: string; value: string; onInput: (v: string) => void }) {
  return (
    <div class="field">
      <label for={id}>File types linked on each semester’s student site <Hint label="About linked file types">
        Each semester has its own student site, with a row for every session. A release copies files from your materials into the semester, and the row links them. Left empty, a row links the files at the top of each released folder, and one link for each subfolder. With types listed, a row links only files of those types, such as notebooks, PDFs and slides, at any depth, plus one link to browse the folder. Other files are still released: students reach them through the repo. For example: ipynb, pdf, html. The public website is not affected.
      </Hint></label>
      <input type="text" id={id} value={value} placeholder="pdf, html" onInput={(e) => onInput((e.target as HTMLInputElement).value)} />
      <p class="why">Released files of these types get a direct link on every semester’s student site. Separate them with commas.</p>
    </div>
  );
}

/** The course's `.github` default branch, for its file links (main until the repo list is read). */
function courseBranch(files: CourseProps['files'], org: string): string {
  const gh = files.repos(org);
  return (gh.kind === 'ready' ? gh.repos.find((r) => r.name === COURSE_REPO)?.default_branch : undefined) ?? 'main';
}

export function DetailsScreen(p: CourseProps) {
  const { course } = p;
  const env = useEnv();
  const file = p.files.file(course.org, COURSE_REPO, 'dsl-course.yml');
  const [draft, setDraft] = useState<Details | null>(null);
  const [save, runSave, setSave] = useSave(env);
  const y = file.kind === 'ready' ? new YamlText(file.text) : null;
  const meta = y && !y.errors.length ? obj(y.toJS()) : {};
  const before = detailsOf(meta);
  const d = draft ?? before;
  const set = (patch: Partial<Details>) => {
    setDraft({ ...d, ...patch });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const defaultErrs = fieldErrors(null, courseDefaultTiers(before.defaults), d.defaults);
  const errs = { ...fieldErrors(null, ABOUT, d.about), ...fieldErrors(null, COURSE_FACTS, d.about), ...defaultErrs };
  const doSave = async () => {
    if (!y || file.kind !== 'ready') return;
    if (await saveCourseFile(env, [runSave, setSave], { org: course.org, migrated: p.migrated, file, meta, before, after: d, errs })) setDraft(null);
  };
  return (
    <>
      <div class="page-head">
        <div><h2 class="h1">Course details <Hint doc="01-new-course-org.md">Every semester of the course starts from these settings. Each semester and each assignment can set its own defaults.</Hint></h2><p class="lede">What every semester’s student site shows about the course, and the course’s defaults.</p></div>
        <div class="actions"><EditFile cls="btn quiet" org={course.org} repo={COURSE_REPO} path="dsl-course.yml" branch={courseBranch(p.files, course.org)} exists={file.kind !== 'absent'} /></div>
      </div>
      {file.kind === 'loading' ? <Loading what="Reading dsl-course.yml" /> : null}
      {file.kind === 'absent' ? <CheckLine cls="bad">There is no dsl-course.yml in {course.org}/.github.</CheckLine> : null}
      {y?.errors.length ? <CheckLine cls="bad">dsl-course.yml does not parse ({y.errors[0]}); fix it with Edit the file directly.</CheckLine> : null}
      {y && !y.errors.length ? (
        <div class="panel">
          <div class="form" style="max-width:720px">
            <div class="form-section">
              <h3>About the course</h3>
              <SchemaForm id="cd" schema={null} tiers={ABOUT} values={d.about} onChange={(v) => set({ about: v })} />
              <SchemaForm id="cdf" schema={null} tiers={COURSE_FACTS} values={d.about} onChange={(v) => set({ about: v })} />
              <dl class="kv">
                <dt>Org</dt><dd>{course.org} <span class="footnote">cannot be renamed here</span></dd>
                <dt>Engine version</dt><dd>{String(meta.central_ref ?? 'release')} <span class="footnote">set by the lab</span></dd>
              </dl>
              <WebsiteSwitch {...p} />
            </div>
            <div class="form-section">
              <h3>Course admins <Hint label="Course admins, instructors and teaching assistants">Course admins can change everything in the course, in every semester. Instructors and teaching assistants are set for each semester under Instructors, and change only that semester and the course’s materials and assignment templates.</Hint></h3>
              <AdminRows admins={d.admins} id="cda" onChange={(admins) => set({ admins })} />
            </div>
            {/* Folded by default; open while a field in it is marked, so the mark is seen. */}
            <details class="fold" open={Object.keys(defaultErrs).length > 0}>
              <summary>Advanced</summary>
              <div class="fold-body">
                <div class="form-section">
                  <h3>Defaults for this course’s assignments <Hint label="About the defaults">Sets the course’s default; each assignment can override. Left empty, the institution’s value in grey applies.</Hint></h3>
                  <SchemaForm id="cdx" schema={null} tiers={courseDefaultTiers(before.defaults)} values={d.defaults} onChange={(v) => set({ defaults: v })} />
                </div>
                <div class="form-section">
                  <h3>Student site links</h3>
                  <LinkKindsField id="cdk" value={d.links} onInput={(links) => set({ links })} />
                </div>
              </div>
            </details>
            <div class="form-section">
              <SaveBar state={save} onSave={() => void doSave()} disabled={!draft || deepEqual(draft, before) || p.migrated === false} file={{ org: course.org, repo: COURSE_REPO, path: 'dsl-course.yml', exists: file.kind !== 'absent' }} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

// --------------------------------------------------------------------------- public website

/** Beside a rule list: the rules that match no file, or that every rule matches one. */
function Unmatched({ rules, total, loading, partial }: { rules: string[]; total: number; loading: boolean; partial: boolean }) {
  if (loading) return <Loading />;
  if (!total) return <p class="footnote">No rules yet.</p>;
  if (partial) return <p class="footnote">Cannot tell which rules match nothing: the file list is incomplete.</p>;
  return rules.length ? (
    <ul class="unmatched">{rules.map((r) => <li><code>{r}</code> matches no file</li>)}</ul>
  ) : <p class="footnote">Every rule matches at least one file.</p>;
}

/** A pattern list as the textarea shows it, and back: one line each, blank lines dropped at the ends. */
const linesOf = (text: string) => text.split('\n');
const textOf = (lines: string[]) => lines.join('\n');

/** The line a tree click added or changed: the first index where the two lists differ; null when nothing changed or a line was removed (nothing to show). */
export function changedLine(before: string[], after: string[]): number | null {
  if (after.length < before.length) return null;
  if (before.length === after.length && before.every((l, i) => l === after[i])) return null;
  let i = 0;
  while (i < before.length && before[i] === after[i]) i++;
  return i;
}

/** A line to flash in the pattern box; `n` counts clicks, so the same line flashes again. */
export interface Flash {
  line: number;
  n: number;
}

/** The pattern textarea over a mirror of its lines, where the line a tree click changed flashes (decision 0026 rule 5). */
function PatternBox({ id, text, onText, flash }: { id: string; text: string; onText: (t: string) => void; flash: Flash | null }) {
  const mirror = useRef<HTMLPreElement>(null);
  return (
    <div class="pattern-box">
      <pre class="pattern-mirror" aria-hidden="true" ref={mirror}>
        {linesOf(text).map((l, i) => (flash?.line === i ? <span key={`f${flash.n}`} class="pm-line flash">{l || ' '}</span> : <span key={`l${i}`} class="pm-line">{l || ' '}</span>))}
      </pre>
      <textarea class="code" id={id} value={text} onInput={(e) => onText((e.target as HTMLTextAreaElement).value)}
        onScroll={(e) => { if (mirror.current) mirror.current.scrollTop = (e.target as HTMLTextAreaElement).scrollTop; }} />
    </div>
  );
}

/** The withhold tree over a repo beside its pattern box (two-way; stacked on narrow screens), and the rules that match nothing. */
function WithholdEditor({ id, label, files, text, onText, loading, partial, kinds, withheldWord, releasedWord, fixed }: {
  id: string; label: string; files: string[]; text: string; onText: (t: string) => void; loading: boolean; partial: boolean;
  kinds?: Record<string, string>; withheldWord: string; releasedWord: string; fixed?: (path: string) => string | null;
}) {
  const [flash, setFlash] = useState<Flash | null>(null);
  const b = badgeFiles(files, linesOf(text));
  const fromTree = (lines: string[]) => {
    const line = changedLine(linesOf(text), lines);
    if (line !== null) setFlash({ line, n: (flash?.n ?? 0) + 1 });
    onText(textOf(lines));
  };
  return (
    <>
      {partial ? <CheckLine cls="bad">GitHub returned only part of this repo’s file list; badges may be incomplete.</CheckLine> : null}
      <div class="grid-2 withhold">
        <div>
          {loading ? <Loading what="Reading the repo" /> : (
            <PatternTree files={files} patterns={linesOf(text)} onChange={fromTree} kinds={kinds} withheldWord={withheldWord} releasedWord={releasedWord} fixed={fixed} />
          )}
        </div>
        <div>
          <div class="field">
            <label for={id}>{label}</label>
            <PatternBox id={id} text={text} onText={(t) => { setFlash(null); onText(t); }} flash={flash} />
            <p class="hint">One pattern per line, as in .gitignore. Clicking the tree writes them here.</p>
          </div>
          <div class="field"><span class="label">Rules</span><Unmatched rules={b.unmatched} total={b.rules} loading={loading} partial={partial} /></div>
        </div>
      </div>
    </>
  );
}

export interface Website {
  enabled: boolean;
  source_repo: string;
  readings_mode: string;
  include_lectures: boolean;
  /** The withhold list as the textarea shows it. */
  withhold: string;
}

/** `opencourse.yml`'s settings with the engine's defaults (`opencourse.parse`) filled in. */
export function websiteOf(doc: Record<string, unknown>): Website {
  return {
    enabled: doc.enabled === true,
    source_repo: String(doc.source_repo ?? ''),
    readings_mode: String(doc.readings_mode ?? 'reading-list'),
    include_lectures: doc.include_lectures !== false,
    withhold: Array.isArray(doc.withhold) ? doc.withhold.map(String).join('\n') : '',
  };
}

const OPENCOURSE_STUB = '# INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.\n# The public website. Edit it on the console’s Public website tab, or here.\n';
const validWebsite = validator(opencourseSchema);

/** `opencourse.yml` after `after`, key by key, or why it would not be valid. */
export function websiteFileAfter(text: string | null, before: Website, after: Website): { text: string } | { error: string } {
  const y = new YamlText(text ?? OPENCOURSE_STUB);
  if (y.errors.length) return { error: `${OPENCOURSE_FILE} does not parse; fix it with Edit the file directly.` };
  const val = (w: Website) => ({ ...w, withhold: w.withhold.split('\n').map((l) => l.trim()).filter(Boolean) });
  const b = val(before), a = val(after);
  for (const k of Object.keys(a) as (keyof Website)[]) if (!deepEqual(b[k], a[k]) || text === null) y.assign([k], a[k] === '' ? null : a[k]);
  if (a.enabled && !a.source_repo) return { error: 'Choose the source materials before turning the website on.' };
  return validWebsite(y.toJS()) ? { text: y.text } : { error: invalidText(OPENCOURSE_FILE, validWebsite) };
}

/**
 * The newest materials repo by its semester tag (`workflows_render._newest_materials`, in
 * `catalogue.termRank`'s order): spring before autumn within a year. The status lists repos by
 * name, oldest first, so its first one is never the default. Without a dated repo, the first one.
 */
export function newestRepo(repos: string[]): string | undefined {
  return repos.reduce<string | undefined>((best, r) => (best === undefined || termRank(r) > termRank(best) ? r : best), undefined);
}

/** A course fact the website shows, read-only here: its value, or that it is not set. */
export function Fact({ label, value }: { label: string; value: unknown }) {
  const v = typeof value === 'string' ? value.trim() : value ? JSON.stringify(value) : '';
  return <><dt>{label}</dt><dd>{v || <span class="footnote">Not set</span>}</dd></>;
}

export function WebsiteScreen(p: CourseProps) {
  const { course } = p;
  const env = useEnv();
  const c = courseOf(p);
  const repos = (c?.materials ?? []).map((m) => m.repo);
  const published = c?.stages?.C6 === 'done';
  const file = p.files.file(course.org, COURSE_REPO, OPENCOURSE_FILE);
  const y = file.kind === 'ready' ? new YamlText(file.text) : null;
  const before = websiteOf(y && !y.errors.length ? obj(y.toJS()) : {});
  const [draft, setDraft] = useState<Website | null>(null);
  const [save, runSave, setSave] = useSave(env);
  const d = draft ?? before;
  const set = (patch: Partial<Website>) => {
    setDraft({ ...d, ...patch });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const meta = p.files.file(course.org, COURSE_REPO, 'dsl-course.yml');
  const my = meta.kind === 'ready' ? new YamlText(meta.text) : null;
  const facts = my && !my.errors.length ? obj(my.toJS()) : {};
  const src = d.source_repo || newestRepo(repos) || '';
  const tree = p.files.tree(course.org, src);
  const files = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir).map((x) => x.path) : [];
  const gh = p.files.repos(course.org);
  const siteExists = gh.kind === 'ready' && gh.repos.some((r) => r.name.toLowerCase() === `${course.org}.github.io`.toLowerCase());
  const doSave = async () => {
    if (y?.errors.length) return;
    if (p.migrated === false) return setSave({ kind: 'bad', text: 'Not saved: the console has not yet confirmed this course uses the current names.' });
    const out = websiteFileAfter(file.kind === 'ready' ? file.text : null, before, { ...d, source_repo: d.source_repo || (d.enabled ? src : '') });
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    if (await runSave({ owner: course.org, repo: COURSE_REPO, path: OPENCOURSE_FILE }, out.text, file.kind === 'ready' ? file.sha : null, { message: 'course: edit the public website settings, from the DSL Teaching Console', statusRepo: [course.org, COURSE_REPO] })) setDraft(null);
  };
  const values: Values = { enabled: d.enabled, source_repo: src, readings_mode: d.readings_mode, include_lectures: d.include_lectures };
  const onForm = (nv: Values) => set({ enabled: nv.enabled === true, source_repo: String(nv.source_repo ?? ''), readings_mode: String(nv.readings_mode ?? 'reading-list'), include_lectures: nv.include_lectures !== false });
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">Public OpenCourse website <span class="chip">Beta</span> <Hint doc="reference/actions-reference.md">An open version of one course materials repo, for anyone. Save, then publish; it updates daily while on.</Hint></h2>
          <p class="lede"><span class={`chip ${published ? 'ok' : ''}`}>{published ? 'Published' : siteExists && !before.enabled ? 'Off' : 'Not published'}</span>{published ? 'Updates daily.' : siteExists && !before.enabled ? WEBSITE_OFF_LIVE : 'Optional: an open version of this course and its materials, available to anyone on the internet.'}</p>
          <p class="footnote">Beta: leave this off for now - work in progress.</p>
        </div>
        <div class="actions">
          <OpButtons def={publishWebsite(courseScope(p), published)} />
          <EditFile cls="btn quiet" org={course.org} repo={COURSE_REPO} path={OPENCOURSE_FILE} branch={courseBranch(p.files, course.org)} exists={file.kind !== 'absent'} />
        </div>
      </div>
      {file.kind === 'loading' ? <Loading what={`Reading ${OPENCOURSE_FILE}`} /> : null}
      {y?.errors.length ? <CheckLine cls="bad">{OPENCOURSE_FILE} does not parse ({y.errors[0]}); fix it with Edit the file directly.</CheckLine> : null}
      <div class="grid-2">
        <section class="panel section">
          <h2>Settings</h2>
          {repos.length ? <SchemaForm id="ws" schema={null} tiers={publishTiers(repos, siteExists)} values={values} onChange={onForm} /> : <p class="footnote">No course materials repo yet: create one first.</p>}
          {before.enabled ? null : <p class="footnote">The website is off: Publish refuses until it is on and saved.</p>}
        </section>
        <section class="panel section">
          <h2>About the course</h2>
          <dl class="kv">
            <dt>Address</dt><dd>{published ? <a href={websiteUrl(course.org)} target="_blank" rel="noopener">{course.org}.github.io <Ext /></a> : `${course.org}.github.io`}</dd>
            <Fact label="Description" value={facts.course_description} />
            <Fact label="Contact" value={facts.contact} />
            <Fact label="Licence" value={facts.licence} />
          </dl>
          <p class="footnote"><a href="#details">Change these in Course details</a></p>
        </section>
      </div>
      {src ? (
        <section class="panel section">
          <h2>Kept off the website <Hint label="About keeping files off the website">Click a file or folder to keep it off the public website; click again to put it back. A course whose website is off starts with the system folder kept off, and anything whose name has the word solution, exam, grade, marks or private in it. Files withheld from students, solutions, tests and grading files never appear anyway.</Hint></h2>
          <WithholdEditor id="ws-withhold" label="Kept-off patterns" files={files} text={d.withhold} onText={(t) => set({ withhold: t })} loading={tree.kind === 'loading'} partial={tree.kind === 'ready' && tree.truncated}
            withheldWord="kept off" releasedWord="public" fixed={(f) => (neverMaterial(f) || denylisted(f) ? 'never public' : null)} />
        </section>
      ) : null}
      <section class="panel section">
        <SaveBar state={save} onSave={() => void doSave()} disabled={!draft || deepEqual(draft, before) || p.migrated === false || !!y?.errors.length} file={{ org: course.org, repo: COURSE_REPO, path: OPENCOURSE_FILE, exists: file.kind !== 'absent' }} />
      </section>
    </>
  );
}

// --------------------------------------------------------------------------- materials repo settings

const MATERIALS_STUB = '# INSTRUCTOR-OWNED - yours. What the folder names cannot say: the syllabus file and folder kinds.\n';
const validMaterials = validator(materialsSchema);

interface Holds {
  syllabus: string;
  kinds: Record<string, string>;
}

/** Each folder's kind label, by its path: the kind `materials.yml` declares, else the one its name gives, else the default (supporting files). */
export function folderKinds(folders: Iterable<string>, kinds: Record<string, string>): Record<string, string> {
  const all = new Set([...folders, ...Object.keys(kinds)]);
  return Object.fromEntries([...all].map((folder) => {
    const kind = kinds[folder.toLowerCase()] ?? inferKind(folder).kind;
    return [folder, KIND_LABEL[kind] ?? kind];
  }));
}

const FROM_WORD = { declared: 'set here', name: 'from its name', default: 'by default' };

/**
 * The folders that carry a kind (`status_json.kind_folders`), out of a repo's folder paths: every
 * released top folder, a folder one level down in a top folder no kind names when its own name
 * names one, and a folder `materials.yml` declares at any depth. Any other folder is part of the
 * folder above it.
 */
export function kindFolderPaths(dirs: string[], kinds: Record<string, string>): string[] {
  return dirs.filter((d) => {
    const parts = d.split('/');
    return !parts.some((x) => x.startsWith('.')) && !neverMaterial(d) && !denylisted(d)
      && (parts.length === 1 || !!kinds[d.toLowerCase()] || (parts.length === ALIAS_DEPTH && !aliasKind(parts[0], kinds) && !!aliasKind(d)));
  });
}

/**
 * The Folder kinds tree's per-folder picker. A kind folder's first option is the kind its name
 * gives, or the default; any other folder's is the folder above's ("part of"). Then each folder
 * kind to declare; a declared one reads "set here", so the detected default is told from a saved
 * choice, and only a choice is ever written.
 */
function KindPicker({ path, declared, kindFolder, onKind }: { path: string; declared?: string; kindFolder: boolean; onKind: (kind: string) => void }) {
  const parent = path.slice(0, path.lastIndexOf('/'));
  return (
    <>
      {declared ? <span class="footnote">{FROM_WORD.declared}</span> : null}
      <select class="ft-kind" aria-label={`Kind of ${path}`} onChange={(e) => onKind((e.target as HTMLSelectElement).value)}>
        <option value="" selected={!declared || (kindFolder && resetSelected(path, declared))}>{kindFolder ? resetLabel(path) : `Part of ${parent}/`}</option>
        {kindChoices(path, declared, kindFolder).map((x) => <option value={x} selected={declared === x}>{KIND_LABEL[x] ?? x}</option>)}
      </select>
    </>
  );
}

/** The hint on Folder kinds, also in docs/02-add-materials-to-course.md. */
export const FOLDER_KINDS_HINT = 'Which folders hold the lectures, labs and readings, so the sites can sort them. Pick any folder, including one inside another; a kind this course does not teach stays unassigned.';

/** The override's "not set here" option: the kind the folder gets by its name, or by default. */
export function resetLabel(folder: string): string {
  const { kind, named } = inferKind(folder);
  return `${KIND_LABEL[kind] ?? kind} (${named ? FROM_WORD.name : FROM_WORD.default})`;
}

/**
 * The kinds the dropdown offers a folder (decision 0031 rule 10): lecture, lab, readings,
 * supporting files, less the one its name already gives it; a kind `materials.yml` already
 * declares stays offered. A folder whose kind is only the default is offered Supporting files
 * explicitly: choosing it writes the kind, which settles the course's problem about it. A
 * folder that is no kind folder (`kindFolder` false) is offered every kind: its first option is
 * the folder above's.
 */
export function kindChoices(folder: string, declared?: string, kindFolder = true): string[] {
  const { kind, named } = inferKind(folder);
  const extra = declared && !FOLDER_KINDS.includes(declared) ? [declared] : [];
  return [...FOLDER_KINDS, ...extra].filter((k) => !kindFolder || !named || k !== kind);
}

/** A kind folder's declared kind is the one its name gives anyway, so "not set here" stays selected. */
const resetSelected = (folder: string, declared: string) => {
  const own = inferKind(folder);
  return own.named && declared === own.kind;
};

/** A syllabus the weekly plan can be written into. */
const isMarkdown = (path: string) => /\.(md|markdown)$/i.test(path);

/** `materials.yml` with the syllabus and kinds written: blank syllabus and no kinds remove the keys. */
export function writeHolds(text: string | null, h: Holds): string | null {
  const y = new YamlText(text ?? MATERIALS_STUB);
  if (y.errors.length) return null;
  y.assign(['syllabus'], h.syllabus.trim() || undefined);
  y.assign(['kinds'], Object.keys(h.kinds).length ? h.kinds : undefined);
  return y.text;
}

/** The syllabus select's choices: the default, the repo's top-level Markdown files, and the current value if it is neither. */
export function syllabusChoices(files: string[], current: string): string[] {
  const md = files.filter((f) => !f.includes('/') && /\.md$/i.test(f)).sort((a, b) => a.localeCompare(b));
  return [...new Set([DEFAULT_SYLLABUS, ...md, ...(current ? [current] : [])])];
}

export function MaterialsScreen(p: CourseProps) {
  const { course, entry } = p;
  const repo = entry ?? '';
  const env = useEnv();
  const m = courseOf(p)?.materials?.find((x) => x.repo === repo);
  const r = m ? materialsReadiness(m) : null;
  const tree = p.files.tree(course.org, repo);
  const files = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir).map((x) => x.path) : [];
  const ignFile = p.files.file(course.org, repo, '.releaseignore');
  const matFile = p.files.file(course.org, repo, MATERIALS_FILE);
  const declared = matFile.kind === 'loading' ? NOTHING_DECLARED : readDeclared(matFile.kind === 'ready' ? matFile.text : null);
  const baseSyl = declared?.declared ? declared.syllabus : '';
  const baseKinds = declared?.kinds ?? {};
  const [syl, setSyl] = useState<string | null>(null);
  const [kindsDraft, setKindsDraft] = useState<Record<string, string> | null>(null);
  const [sylSave, runSyl, setSylSave] = useSave(env);
  const [kindSave, runKind, setKindSave] = useSave(env);
  const curKinds = kindsDraft ?? baseKinds;
  const dirs = tree.kind === 'ready' ? tree.paths.filter((x) => x.dir).map((x) => x.path) : [];
  // Folders a kind is read for without a declaration: the tree's other folders are part of the one above.
  const kindSet = new Set(kindFolderPaths(dirs, {}));
  // The folders that carry a kind (`status_json.kind_folders`, `kindFolderPaths` with the draft's
  // kinds): a top folder declared a kind holds none of the detected ones, a declared path is one at any depth.
  const folders = new Set([...kindSet].filter((d) => !d.includes('/') || !aliasKind(d.split('/')[0], curKinds)));
  for (const d of kindFolderPaths(dirs.filter((x) => x.includes('/') && curKinds[x.toLowerCase()]), curKinds)) folders.add(d);
  const kinds = folderKinds(folders, curKinds);
  // A content kind with no folder (the engine's suggested `kind_folder:<kind>`): a notice, never an error.
  const missingKinds = (m?.checks ?? []).filter((c) => c.id.startsWith('kind_folder:') && !c.done);
  // The chosen file: the draft, else the declared one, else the default. Choosing the default writes no key.
  const savedSyl = baseSyl.trim() || DEFAULT_SYLLABUS;
  const syllabus = syl ?? savedSyl;
  const sylFile = p.files.file(course.org, repo, syllabus);
  // Why Write is not offered (Copy still is): a file the plan cannot go into.
  const noWrite = !isMarkdown(syllabus)
    ? `${syllabus} is not Markdown, so the plan cannot be written into it: copy it and paste it in.`
    : sylFile.kind === 'absent' && !files.includes(syllabus)
      ? `There is no ${syllabus} yet: write the syllabus first, or copy the plan.`
      : null;
  const ignText = ignFile.kind === 'ready' ? ignFile.text : '';
  const [ign, setIgn] = useState<string | null>(null);
  const [ignSave, runIgn] = useSave(env);
  const scope = newestScope(p);
  // The syllabus line of the checklist; a status without one reads it off `ready`.
  const sylDone = m ? (m.checks?.find((c) => c.id === 'syllabus')?.done ?? m.state === 'ready') : false;
  const repos = p.files.repos(course.org);
  const branch = (repos.kind === 'ready' ? repos.repos.find((r) => r.name === repo)?.default_branch : undefined);
  // Each file's link: the editor while it exists, GitHub's new-file page while it does not (0024 rule 9).
  const matRef = { org: course.org, repo, path: MATERIALS_FILE, branch, exists: matFile.kind !== 'absent' };
  const ignRef = { org: course.org, repo, path: '.releaseignore', branch, exists: ignFile.kind !== 'absent' };
  const partial = tree.kind === 'ready' && tree.truncated;
  const setKind = (folder: string, kind: string) => {
    const next = { ...curKinds };
    delete next[folder.toLowerCase()];
    if (kind) next[folder.toLowerCase()] = kind;
    setKindsDraft(next);
  };
  const saveMat = async (h: Holds, run: typeof runSyl, setState: typeof setSylSave, done: () => void) => {
    const text = writeHolds(matFile.kind === 'ready' ? matFile.text : null, h);
    if (text === null) return;
    if (!validMaterials(new YamlText(text).toJS() ?? {})) return setState({ kind: 'bad', text: invalidText(MATERIALS_FILE, validMaterials) });
    if (await run({ owner: course.org, repo, path: MATERIALS_FILE }, text, matFile.kind === 'ready' ? matFile.sha : null, { message: 'materials: edit the syllabus file and folder kinds, from the DSL Teaching Console', statusRepo: [course.org, COURSE_REPO] })) done();
  };
  const ignDraft = ign ?? ignText;
  // What Save writes: a list that withholds nothing is marked reviewed.
  const ignOut = withMark(ignDraft.endsWith('\n') || !ignDraft ? ignDraft : `${ignDraft}\n`);
  const saveIgn = async () => {
    if (await runIgn({ owner: course.org, repo, path: '.releaseignore' }, ignOut, ignFile.kind === 'ready' ? ignFile.sha : null, { message: 'materials: edit what is withheld from students, from the DSL Teaching Console', statusRepo: [course.org, COURSE_REPO] })) setIgn(null);
  };
  return (
    <>
      <div class="page-head">
        <div><h2 class="h1">{repo} <Hint doc="02-add-materials-to-course.md">Course materials stay here, private to instructors, until a scheduled release copies them to a semester. Files withheld here never reach students.</Hint></h2><p class="lede">Course materials repo settings. <span class="slug">{course.org}/{repo}</span></p></div>
        <div class="actions">{r ? <RepoChip r={r} /> : null}<OpenButton org={course.org} repo={repo} quiet /></div>
      </div>
      <div class="stack">
        {m?.checks?.length ? (
          <section class="panel section">
            <MaterialsChecklist checks={m.checks} />
          </section>
        ) : r ? <RepoWhy r={r} /> : null}
        <section class="panel section">
          <h2>Syllabus <Hint label="About the syllabus">The syllabus is one file at the repo’s top level that the student site pins. Pick which file it is here; the default is SYLLABUS.md. The list shows this repo’s top-level Markdown files; pick the one that is your syllabus.</Hint></h2>
          <div class="field">
            <label for="m-syl">Syllabus file <span class="default">default: {DEFAULT_SYLLABUS}</span></label>
            <select id="m-syl" onChange={(e) => setSyl((e.target as HTMLSelectElement).value)}>
              {syllabusChoices(files, savedSyl).map((f) => <option value={f} selected={f === syllabus}>{f}</option>)}
            </select>
            <p class="hint">The Markdown file at the repo’s top level that the student site pins as the syllabus.</p>
          </div>
          {/* A suggestion (decision 0034): never red. Done, or the dotted optional row with what is missing. */}
          {m ? (sylDone ? <div class="check-line ok"><Check /><span>Written.</span></div> : (
            <ul class="setup"><ItemRow it={{ id: 'syllabus', label: 'Syllabus written', kind: 'step', need: 'suggested', state: 'open', why: sylFile.kind === 'absent' ? `There is no ${syllabus} yet.` : 'Still the template text. Students would see the placeholder at the first release.' }} /></ul>
          )) : <p class="footnote">Not checked yet.</p>}
          <div class="actions"><EditFile org={course.org} repo={repo} path={syllabus} branch={branch} exists={sylFile.kind !== 'absent' || files.includes(syllabus)} /></div>
          {declared === null ? <CheckLine cls="bad">{MATERIALS_FILE} does not parse; fix it with Edit the file directly.</CheckLine> : null}
          <SaveBar state={sylSave} onSave={() => void saveMat({ syllabus: syllabus === DEFAULT_SYLLABUS ? '' : syllabus, kinds: baseKinds }, runSyl, setSylSave, () => setSyl(null))} small disabled={syl === null || syl === savedSyl || declared === null} file={matRef} />
          <h3>Weekly plan for the syllabus <Hint label="About the weekly plan">Built from the semester schedule: every session with its date and readings. Copy shows it to paste anywhere; Write puts it into your syllabus file.</Hint></h3>
          {scope ? (
            <div class="actions">
              {noWrite === null ? (
                <>
                  <OpButtons def={generateSyllabus(scope, repo, syllabus)} small label="Write" />
                  <Hint label="About Write">Write puts the plan into {syllabus} between the lines &lt;!-- dsl:weekly-plan --&gt; and &lt;!-- /dsl:weekly-plan --&gt;, adding them under “## Weekly plan” at the end the first time. Move the marked block anywhere in the file and Write updates it there; nothing else changes.</Hint>
                </>
              ) : (
                <>
                  <button class="btn small" type="button" onClick={() => env?.ops.open(generateSyllabus(scope, repo, syllabus), 'preview')}>Copy</button>
                  <span class="footnote">{noWrite}</span>
                </>
              )}
            </div>
          ) : <p class="footnote">Needs a semester: the plan is built from its schedule.</p>}
        </section>
        <section class="panel section">
          <h2>Folder kinds <Hint label="About folder kinds">{FOLDER_KINDS_HINT}</Hint></h2>
          {tree.kind === 'loading' ? <Loading /> : tree.kind === 'ready' ? (
            <PatternTree files={files} patterns={linesOf(ign ?? ignText)} onChange={() => {}} withheldWord="withheld" releasedWord="released to students"
              control={(path, dir) => (dir && !neverMaterial(path) && !denylisted(path) ? <KindPicker path={path} declared={curKinds[path.toLowerCase()]} kindFolder={kindSet.has(path)} onKind={(kind) => setKind(path, kind)} /> : null)} />
          ) : <p class="footnote">Could not read the repo’s folders; it is tried again shortly.</p>}
          {missingKinds.length ? (
            <ul class="setup">{missingKinds.map((c) => <ItemRow it={{ id: c.id, label: c.label, kind: 'step', need: 'suggested', state: 'open', why: c.why ?? undefined }} />)}</ul>
          ) : null}
          <SaveBar state={kindSave} onSave={() => void saveMat({ syllabus: baseSyl, kinds: curKinds }, runKind, setKindSave, () => setKindsDraft(null))} small disabled={kindsDraft === null || deepEqual(kindsDraft, baseKinds) || declared === null} file={matRef} />
        </section>
        <section class="panel section">
          <h2>Withheld from students <Hint label="About withheld files">Everything in this repo is released to students as it stands, by the schedule or by hand, unless a line here withholds it. Click a file or folder to withhold it; click again to release it. Use it for solutions, drafts and anything private.</Hint></h2>
          {tree.kind === 'absent' || tree.kind === 'error' ? <p class="footnote">Could not read the repo’s files.</p> : (
            <WithholdEditor id="ign-pat" label="Withheld patterns" files={files} text={ign ?? ignText} onText={setIgn} loading={tree.kind === 'loading'} partial={partial}
              kinds={kinds} withheldWord="withheld" releasedWord="released to students" />
          )}
          <p class="footnote">This reads the repo’s top-level .releaseignore; one in a subfolder still applies there.</p>
          <SaveBar state={ignSave} onSave={() => void saveIgn()} small disabled={ignFile.kind === 'loading' || (ignFile.kind === 'ready' && ignOut === ignText)} file={ignRef} />
        </section>
      </div>
    </>
  );
}
