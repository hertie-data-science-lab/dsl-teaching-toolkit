// Course-side editors: S3 Course details (dsl-course.yml), S20 Public website
// (opencourse.yml), S19 Materials repo settings (materials.yml, .releaseignore).

import { useState } from 'preact/hooks';
import courseSchema from '../../schemas/dsl_course.schema.json';
import materialsSchema from '../../schemas/materials.schema.json';
import opencourseSchema from '../../schemas/opencourse.schema.json';
import { useEnv, type Env } from '../env';
import { badgeFiles, denylisted, neverMaterial } from '../edit/badges';
import { invalidText, useSave } from '../edit/save';
import { YamlText, compact, deepEqual, obj } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { KIND_LABEL } from '../model/format';
import { CONTENT_KINDS, DEFAULT_SYLLABUS, MATERIALS_FILE, NOTHING_DECLARED, inferKind, readDeclared, withMark } from '../model/materialsRules';
import { validator } from '../model/validate';
import { generateSyllabus, publishWebsite, type Scope } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { ABOUT, COURSE_FACTS, courseDefaultTiers } from '../tiers/course';
import { formatsList } from '../tiers/grading';
import { WEBSITE_OFF_LIVE, publishWebsite as publishTiers } from '../tiers/ops';
import type { Values } from '../tiers/types';
import { CheckLine, Crumbs, EditFile, Lives, Loading } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveBar } from '../ui/edit';
import { PatternTree } from '../ui/PatternTree';
import { Check, Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { courseView, CourseHeaderActions, MaterialsChecklist, StateChip } from './Course';
import type { CourseProps } from './types';
import { COURSE_REPO, OPENCOURSE_FILE } from '../model/names';

const validCourse = validator(courseSchema);

export function courseScope(p: Pick<CourseProps, 'course'>): Scope {
  return { courseOrg: p.course.org, where: p.course.name };
}

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
    const rawBy = (h: string) => raws.find((r) => String(r.github_handle ?? '') === h) ?? {};
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

/** The first new admin handle with no GitHub account, or null (a failed lookup counts as there). */
export async function missingAdmin(env: Env | null, before: Admin[], after: Admin[]): Promise<string | null> {
  if (!env) return null;
  for (const a of after.filter((x) => x.github_handle.trim() && !before.some((b) => b.github_handle === x.github_handle))) {
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

/** The public website's on/off, from opencourse.yml's `enabled`: no file is off (`opencourse.parse`), one not loaded is not known. */
export function websiteWord(files: CourseProps['files'], org: string): string {
  const site = files.file(org, COURSE_REPO, OPENCOURSE_FILE);
  if (site.kind === 'absent') return 'Off.';
  const y = site.kind === 'ready' ? new YamlText(site.text) : null;
  return y && !y.errors.length ? (websiteOf(obj(y.toJS())).enabled ? 'On.' : 'Off.') : 'Not known yet.';
}

/** The live public website. */
export const websiteUrl = (org: string) => `https://${org}.github.io`;

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
  const ready = courseView(p).course?.ready ?? false;
  const siteOn = websiteWord(p.files, course.org);
  const set = (patch: Partial<Details>) => {
    setDraft({ ...d, ...patch });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const errs = { ...fieldErrors(null, ABOUT, d.about), ...fieldErrors(null, COURSE_FACTS, d.about), ...fieldErrors(null, courseDefaultTiers(), d.defaults) };
  const doSave = async () => {
    if (!y || file.kind !== 'ready') return;
    if (p.migrated === false) return setSave({ kind: 'bad', text: 'Not saved: the console has not yet confirmed this course uses the current names.' });
    if (Object.keys(errs).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const after = d;
    const out = courseFileAfter(file.text, before, after, meta);
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    const missing = await missingAdmin(env, before.admins, after.admins);
    if (missing) return setSave({ kind: 'bad', text: `There is no GitHub account called ${missing}.` });
    if (await runSave({ owner: course.org, repo: COURSE_REPO, path: 'dsl-course.yml' }, out.text, file.sha, { message: 'course: edit the course details, from the DSL Teaching Console', statusRepo: [course.org, COURSE_REPO] })) {
      setDraft(null);
    }
  };
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Course details' }]} />
      <div class="page-head">
        <div><h1>Course details <Hint doc="01-new-course-org.md">Every semester of the course starts from these settings. Each semester and each assignment can set its own defaults.</Hint></h1><p class="lede">What every semester’s student site shows about the course, and the course’s defaults.</p></div>
        <CourseHeaderActions course={course} ready={ready} />
      </div>
      {file.kind === 'loading' ? <Loading what="Reading dsl-course.yml" /> : null}
      {file.kind === 'absent' ? <CheckLine cls="bad">There is no dsl-course.yml in {course.org}/.github.</CheckLine> : null}
      {y?.errors.length ? <CheckLine cls="bad">dsl-course.yml does not parse ({y.errors[0]}); fix it with Edit the file.</CheckLine> : null}
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
                <dt>Public website</dt><dd>{siteOn} <a class="textlink" href="#website">Manage</a></dd>
              </dl>
            </div>
            <div class="form-section">
              <h3>Course admins <Hint label="Course admins, instructors and teaching assistants">Course admins can change everything in the course, in every semester. Instructors and teaching assistants are set for each semester under Instructors, and change only that semester and the course’s materials and assignment templates.</Hint></h3>
              <AdminRows admins={d.admins} id="cda" onChange={(admins) => set({ admins })} />
            </div>
            <div class="form-section">
              <h3>Defaults for this course’s assignments <Hint label="About the defaults">Sets the course’s default; each assignment can override. Left empty, the institution’s value in grey applies.</Hint></h3>
              <SchemaForm id="cdx" schema={null} tiers={courseDefaultTiers()} values={d.defaults} onChange={(v) => set({ defaults: v })} />
            </div>
            <div class="form-section">
              <h3>Site links</h3>
              <div class="field">
                <label for="cdk">File types the student site links to</label>
                <input type="text" id="cdk" value={d.links} placeholder="pdf, html" onInput={(e) => set({ links: (e.target as HTMLInputElement).value })} />
                <p class="why">Released files of these types get a direct link on every semester’s student site. Separate them with commas.</p>
              </div>
            </div>
            <div class="form-section">
              <SaveBar state={save} onSave={() => void doSave()} disabled={!draft || deepEqual(draft, before) || p.migrated === false} file={{ org: course.org, repo: COURSE_REPO, path: 'dsl-course.yml' }} />
              <Lives org={course.org} repo={COURSE_REPO} path="dsl-course.yml" />
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

/** The withhold tree over a repo, with the pattern box beneath it (two-way) and the rules that match nothing. */
function WithholdEditor({ id, label, files, text, onText, loading, partial, org, repo, branch, kinds, withheldWord, releasedWord, fixed }: {
  id: string; label: string; files: string[]; text: string; onText: (t: string) => void; loading: boolean; partial: boolean;
  org: string; repo: string; branch: string | null; kinds?: Record<string, string>; withheldWord: string; releasedWord: string; fixed?: (path: string) => string | null;
}) {
  const b = badgeFiles(files, linesOf(text));
  return (
    <>
      {partial ? <CheckLine cls="bad">GitHub returned only part of this repo’s file list; badges may be incomplete.</CheckLine> : null}
      {loading ? <Loading what="Reading the repo" /> : (
        <PatternTree files={files} patterns={linesOf(text)} onChange={(l) => onText(textOf(l))} org={org} repo={repo} branch={branch} kinds={kinds} withheldWord={withheldWord} releasedWord={releasedWord} fixed={fixed} />
      )}
      <div class="pattern-grid">
        <div class="field">
          <label for={id}>{label}</label>
          <textarea class="code" id={id} onInput={(e) => onText((e.target as HTMLTextAreaElement).value)} value={text} />
          <p class="hint">One pattern per line, as in .gitignore. Clicking the tree writes them here.</p>
        </div>
        <div class="field"><span class="label">Rules</span><Unmatched rules={b.unmatched} total={b.rules} loading={loading} partial={partial} /></div>
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
  if (y.errors.length) return { error: `${OPENCOURSE_FILE} does not parse; fix it with Edit the file.` };
  const val = (w: Website) => ({ ...w, withhold: w.withhold.split('\n').map((l) => l.trim()).filter(Boolean) });
  const b = val(before), a = val(after);
  for (const k of Object.keys(a) as (keyof Website)[]) if (!deepEqual(b[k], a[k]) || text === null) y.assign([k], a[k] === '' ? null : a[k]);
  if (a.enabled && !a.source_repo) return { error: 'Choose the source materials before turning the website on.' };
  return validWebsite(y.toJS()) ? { text: y.text } : { error: invalidText(OPENCOURSE_FILE, validWebsite) };
}

/**
 * The newest materials repo by its semester tag (`workflows_render._newest_materials`): spring
 * before autumn within a year. The status lists repos by name, oldest first, so its first one is
 * never the default. Without a dated repo, the first one.
 */
export function newestRepo(repos: string[]): string | undefined {
  const key = (r: string) => {
    const m = /-([fswu])(\d{4})$/.exec(r);
    return m ? Number(m[2]) * 2 + (m[1] === 'f' ? 1 : 0) : -1;
  };
  return repos.reduce<string | undefined>((best, r) => (best === undefined || key(r) > key(best) ? r : best), undefined);
}

/** A course fact the website shows, read-only here: its value, or that it is not set. */
export function Fact({ label, value }: { label: string; value: unknown }) {
  const v = typeof value === 'string' ? value.trim() : value ? JSON.stringify(value) : '';
  return <><dt>{label}</dt><dd>{v || <span class="footnote">Not set</span>}</dd></>;
}

export function WebsiteScreen(p: CourseProps) {
  const { course } = p;
  const env = useEnv();
  const v = courseView(p);
  const repos = (v.course?.materials ?? []).map((m) => m.repo);
  const published = v.course?.stages?.C6 === 'done';
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
  const branch = (gh.kind === 'ready' ? gh.repos.find((r) => r.name === src)?.default_branch : undefined) ?? null;
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
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Public website' }]} />
      <div class="page-head">
        <div>
          <h1>Public website <Hint doc="reference/actions-reference.md">An open version of one materials repo, for anyone. Save, then publish; it updates daily while on.</Hint></h1>
          <p class="lede"><span class={`chip ${published ? 'ok' : ''}`}>{published ? 'Published' : siteExists && !before.enabled ? 'Off' : 'Not published'}</span>{published ? 'Updates daily.' : siteExists && !before.enabled ? WEBSITE_OFF_LIVE : 'Optional: an open version of your materials for anyone.'}</p>
        </div>
        <div class="actions"><OpButtons def={publishWebsite(courseScope(p), published)} /></div>
      </div>
      {file.kind === 'loading' ? <Loading what={`Reading ${OPENCOURSE_FILE}`} /> : null}
      {y?.errors.length ? <CheckLine cls="bad">{OPENCOURSE_FILE} does not parse ({y.errors[0]}); fix it with Edit the file.</CheckLine> : null}
      <div class="grid-2">
        <section class="panel section">
          <h2>Settings</h2>
          {repos.length ? <SchemaForm id="ws" schema={null} tiers={publishTiers(repos, siteExists)} values={values} onChange={onForm} /> : <p class="footnote">No materials repo yet: create one first.</p>}
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
          <h2>Kept off the website <Hint label="About keeping files off the website">Click a file or folder to keep it off the public website; click again to put it back. Files withheld from students, solutions, tests and grading files never appear anyway.</Hint></h2>
          <WithholdEditor id="ws-withhold" label="Kept-off patterns" files={files} text={d.withhold} onText={(t) => set({ withhold: t })} loading={tree.kind === 'loading'} partial={tree.kind === 'ready' && tree.truncated}
            org={course.org} repo={src} branch={branch} withheldWord="kept off" releasedWord="public" fixed={(f) => (neverMaterial(f) || denylisted(f) ? 'never public' : null)} />
        </section>
      ) : null}
      <section class="panel section">
        <SaveBar state={save} onSave={() => void doSave()} disabled={!draft || deepEqual(draft, before) || p.migrated === false || !!y?.errors.length} file={{ org: course.org, repo: COURSE_REPO, path: OPENCOURSE_FILE }} />
        <Lives org={course.org} repo={COURSE_REPO} path={OPENCOURSE_FILE} />
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

/** Each top-level folder's kind, and where it came from: `materials.yml`, the folder's name, or the default. */
export function folderKinds(folders: string[], kinds: Record<string, string>): { folder: string; kind: string; from: 'declared' | 'name' | 'default' }[] {
  const all = [...new Set([...folders, ...Object.keys(kinds)])].sort((a, b) => a.localeCompare(b));
  return all.map((folder) => {
    const declared = kinds[folder.toLowerCase()];
    if (declared) return { folder, kind: declared, from: 'declared' as const };
    const { kind, named } = inferKind(folder);
    return { folder, kind, from: named ? ('name' as const) : ('default' as const) };
  });
}

const FROM_WORD = { declared: 'set here', name: 'from its name', default: 'the default' };

/** The override's "not set here" option: the kind the folder gets without `materials.yml`, and why. */
export function resetLabel(folder: string): string {
  const { kind, named } = inferKind(folder);
  return `${KIND_LABEL[kind] ?? kind} (${named ? FROM_WORD.name : FROM_WORD.default})`;
}

/** `materials.yml` with the syllabus and kinds written: blank syllabus and no kinds remove the keys. */
export function writeHolds(text: string | null, h: Holds): string | null {
  const y = new YamlText(text ?? MATERIALS_STUB);
  if (y.errors.length) return null;
  y.assign(['syllabus'], h.syllabus.trim() || undefined);
  y.assign(['kinds'], Object.keys(h.kinds).length ? h.kinds : undefined);
  return y.text;
}

export function MaterialsScreen(p: CourseProps) {
  const { course, entry } = p;
  const repo = entry ?? '';
  const env = useEnv();
  const v = courseView(p);
  const m = v.course?.materials?.find((x) => x.repo === repo);
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
  // The top folders a release can copy (`status_json._released_folders`): never-released ones need no kind.
  const folders = tree.kind === 'ready' ? tree.paths.filter((x) => x.dir && !x.path.includes('/') && !x.path.startsWith('.') && !neverMaterial(x.path) && !denylisted(x.path)).map((x) => x.path) : [];
  const kinds = folderKinds(folders, curKinds);
  const syllabus = (syl ?? baseSyl).trim() || DEFAULT_SYLLABUS;
  const sylFile = p.files.file(course.org, repo, syllabus);
  const ignText = ignFile.kind === 'ready' ? ignFile.text : '';
  const [ign, setIgn] = useState<string | null>(null);
  const [ignSave, runIgn] = useSave(env);
  const scope = newestScope(p);
  // The syllabus line of the checklist; a status without one reads it off `ready`.
  const sylDone = m ? (m.checks?.find((c) => c.id === 'syllabus')?.done ?? m.state === 'ready') : false;
  const repos = p.files.repos(course.org);
  const branch = (repos.kind === 'ready' ? repos.repos.find((r) => r.name === repo)?.default_branch : undefined) ?? null;
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
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Materials', href: '#materials' }, { t: repo }]} />
      <div class="page-head">
        <div><h1>{repo} <Hint doc="02-add-materials-to-course.md">Materials stay here, private to instructors, until a scheduled release copies them to a semester. Files withheld here never reach students.</Hint></h1><p class="lede">Materials repo settings. <span class="slug">{course.org}/{repo}</span></p></div>
        <div class="actions"><OpenButton org={course.org} repo={repo} quiet /></div>
      </div>
      <div class="stack">
        {m?.checks?.length ? (
          <section class="panel section">
            <div class="section-head"><h2>Checklist</h2><StateChip state={m.state} todo="Not ready yet" /></div>
            <MaterialsChecklist checks={m.checks} />
          </section>
        ) : null}
        <section class="panel section">
          <h2>Syllabus</h2>
          {m ? (sylDone ? <div class="check-line ok"><Check /><span>Written.</span></div> : sylFile.kind === 'ready' ? <CheckLine cls="bad">Still the template text. Students would see the placeholder at the first release.</CheckLine> : sylFile.kind === 'absent' ? <CheckLine cls="bad">There is no {syllabus} yet.</CheckLine> : <p class="footnote">Not ready yet.</p>) : <p class="footnote">Not checked yet.</p>}
          <div class="actions"><EditFile org={course.org} repo={repo} path={syllabus} /></div>
          <div class="field">
            <label for="m-syl">Syllabus file <span class="default">default: {DEFAULT_SYLLABUS}</span></label>
            <input type="text" id="m-syl" list="m-syl-files" placeholder={DEFAULT_SYLLABUS} value={syl ?? baseSyl} onInput={(e) => setSyl((e.target as HTMLInputElement).value)} />
            <datalist id="m-syl-files">{files.filter((f) => !f.includes('/')).map((f) => <option value={f} />)}</datalist>
            <p class="hint">The file the student site pins as the syllabus, at the repo’s top level.</p>
          </div>
          {declared === null ? <CheckLine cls="bad">{MATERIALS_FILE} does not parse; fix it with Edit the file.</CheckLine> : null}
          <SaveBar state={sylSave} onSave={() => void saveMat({ syllabus: syl ?? baseSyl, kinds: baseKinds }, runSyl, setSylSave, () => setSyl(null))} small disabled={syl === null || syl === baseSyl || declared === null} file={{ org: course.org, repo, path: MATERIALS_FILE }} />
          {scope ? (
            <>
              <p class="footnote">Builds a paste-ready ‘Course sessions and readings’ block from the semester schedule and its readings entries. Write saves it as <code>SYLLABUS.sessions.md</code> in this repo; <code>{syllabus}</code> is yours and is never touched.</p>
              <div class="actions"><OpButtons def={generateSyllabus(scope, repo)} small previewLabel="Preview the session list" /></div>
            </>
          ) : null}
        </section>
        <section class="panel section">
          <h2>Folder kinds <Hint label="About folder kinds">A release that names no kind takes the kind of the top folder its files land in. A folder named lectures, labs, readings or similar is that kind; anything else counts as a lecture unless you change it here.</Hint></h2>
          {tree.kind === 'loading' ? <Loading /> : kinds.length ? (
            <table class="grid kinds">
              <thead><tr><th>Folder</th><th>Kind, and why</th><th>Change</th></tr></thead>
              <tbody>
                {kinds.map((k) => (
                  <tr>
                    <td><code>{k.folder}/</code></td>
                    <td><span class="chip">{KIND_LABEL[k.kind] ?? k.kind}</span> <span class="footnote">{FROM_WORD[k.from]}</span></td>
                    <td><select aria-label={`Kind of ${k.folder}`} onChange={(e) => setKind(k.folder, (e.target as HTMLSelectElement).value)}>
                      <option value="" selected={k.from !== 'declared'}>{resetLabel(k.folder)}</option>
                      {CONTENT_KINDS.map((x) => <option value={x} selected={k.from === 'declared' && k.kind === x}>{KIND_LABEL[x] ?? x}</option>)}
                    </select></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p class="footnote">No folders yet.</p>}
          <SaveBar state={kindSave} onSave={() => void saveMat({ syllabus: baseSyl, kinds: curKinds }, runKind, setKindSave, () => setKindsDraft(null))} small disabled={kindsDraft === null || deepEqual(kindsDraft, baseKinds) || declared === null} file={{ org: course.org, repo, path: MATERIALS_FILE }} />
          <Lives org={course.org} repo={repo} path={MATERIALS_FILE} />
        </section>
        <section class="panel section">
          <h2>Withheld from students <Hint label="About withheld files">Withheld files and folders are never copied to a semester, so students never see them. Click one in the tree to withhold it; click again to release it.</Hint></h2>
          {tree.kind === 'absent' ? <p class="footnote">Could not read the repo’s files.</p> : (
            <WithholdEditor id="ign-pat" label="Withheld patterns" files={files} text={ign ?? ignText} onText={setIgn} loading={tree.kind === 'loading'} partial={partial}
              org={course.org} repo={repo} branch={branch} kinds={Object.fromEntries(kinds.map((k) => [k.folder, KIND_LABEL[k.kind] ?? k.kind]))} withheldWord="withheld" releasedWord="released to students" />
          )}
          <p class="footnote">This reads the repo’s top-level .releaseignore; one in a subfolder still applies there.</p>
          <SaveBar state={ignSave} onSave={() => void saveIgn()} small disabled={ignFile.kind === 'loading' || (ignFile.kind === 'ready' && ignOut === ignText)} file={{ org: course.org, repo, path: '.releaseignore' }} />
          <Lives org={course.org} repo={repo} path=".releaseignore" />
        </section>
      </div>
    </>
  );
}
