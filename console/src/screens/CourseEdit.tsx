// Course-side editors: S3 Course details (dsl-course.yml), S20 Public website, S19
// Materials repo settings (publish.yml, .releaseignore).

import { useState } from 'preact/hooks';
import courseSchema from '../../schemas/dsl_course.schema.json';
import materialsSchema from '../../schemas/materials.schema.json';
import materialsRules from '../../schemas/materials.json';
import { useEnv, type Env } from '../env';
import { badgeFiles } from '../edit/badges';
import { invalidText, useSave } from '../edit/save';
import { YamlText, compact, deepEqual, obj } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { KIND_LABEL } from '../model/format';
import { CONTENT_KINDS, DEFAULT_SYLLABUS, MATERIALS_FILE, NOTHING_DECLARED, PUBLISH_FILE, inferKind, readDeclared } from '../model/materialsRules';
import { validator } from '../model/validate';
import { generateSyllabus, publishWebsite, type Scope } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { ABOUT, courseDefaultTiers } from '../tiers/course';
import { formatsList } from '../tiers/grading';
import { publishWebsite as publishTiers } from '../tiers/ops';
import type { Values } from '../tiers/types';
import { CheckLine, Crumbs, EditFile, Help, Lives, Loading } from '../ui/bits';
import { SaveBar } from '../ui/edit';
import { FileTree } from '../ui/FileTree';
import { Check, Ext } from '../ui/icons';
import { courseView, CourseHeaderActions } from './Course';
import type { CourseProps } from './types';
import { COURSE_REPO } from '../model/names';

const validCourse = validator(courseSchema);
const BUNDLE_WORDS = materialsRules.bundle_dirs.map((d) => `${d}/`).join(', ');

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
    about: compact({ course_name: meta.course_name, course_code: meta.course_code, course_description: meta.course_description }),
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
  const set = (patch: Partial<Details>) => {
    setDraft({ ...d, ...patch });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const errs = { ...fieldErrors(null, ABOUT, d.about), ...fieldErrors(null, courseDefaultTiers(), d.defaults) };
  const doSave = async () => {
    if (!y || file.kind !== 'ready') return;
    if (p.migrated === false) return setSave({ kind: 'bad', text: 'Not saved: the console has not yet confirmed this course uses the current names.' });
    if (Object.keys(errs).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const after = d;
    const out = courseFileAfter(file.text, before, after, meta);
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    const missing = await missingAdmin(env, before.admins, after.admins);
    if (missing) return setSave({ kind: 'bad', text: `There is no GitHub account called ${missing}.` });
    if (await runSave({ owner: course.org, repo: COURSE_REPO, path: 'dsl-course.yml' }, out.text, file.sha, { message: 'course: edit the course details, from the Instructor Console', statusRepo: [course.org, COURSE_REPO] })) {
      setDraft(null);
    }
  };
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Course details' }]} />
      <div class="page-head">
        <div><h1>Course details</h1><p class="lede">What every semester’s student site shows about the course, and the course’s defaults.</p></div>
        <CourseHeaderActions course={course} ready={ready} />
      </div>
      <Help title="Course admins and defaults" doc="01-setup-course-org.md">
        <p>Course admins keep every button for this course across years. They can differ from a given semester’s instructors, who are set per semester under Instructors.</p>
        <p>These are the course’s defaults. Left empty, the institution’s value in grey applies. Each semester can set its own, and each assignment its own.</p>
      </Help>
      {file.kind === 'loading' ? <Loading what="Reading dsl-course.yml" /> : null}
      {file.kind === 'absent' ? <CheckLine cls="bad">There is no dsl-course.yml in {course.org}/.github.</CheckLine> : null}
      {y?.errors.length ? <CheckLine cls="bad">dsl-course.yml does not parse ({y.errors[0]}); fix it with Edit the file.</CheckLine> : null}
      {y && !y.errors.length ? (
        <div class="panel">
          <div class="form" style="max-width:720px">
            <div class="form-section">
              <h3>About the course</h3>
              <SchemaForm id="cd" schema={null} tiers={ABOUT} values={d.about} onChange={(v) => set({ about: v })} />
              <dl class="kv"><dt>Org</dt><dd>{course.org} <span class="footnote">cannot be renamed here</span></dd><dt>Engine version</dt><dd>{String(meta.central_ref ?? 'release')} <span class="footnote">set by the lab</span></dd></dl>
            </div>
            <div class="form-section">
              <h3>Course admins</h3>
              <p class="footnote">Course admins keep every button for this course across years. They can differ from a given semester’s instructors, who are set per semester under Instructors.</p>
              <AdminRows admins={d.admins} id="cda" onChange={(admins) => set({ admins })} />
            </div>
            <div class="form-section">
              <h3>Defaults for this course’s assignments</h3>
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

export function WebsiteScreen(p: CourseProps) {
  const { course } = p;
  const v = courseView(p);
  const repos = (v.course?.materials ?? []).map((m) => m.repo);
  const published = v.course?.stages?.C6 === 'done';
  const [values, setValues] = useState<Values>({ source_repo: repos[0], readings_mode: 'reading-list', include_lectures: true });
  const tiers = publishTiers(repos);
  const src = String(values.source_repo ?? repos[0] ?? '');
  const def = publishWebsite(courseScope(p), repos, { source_repo: src, readings_mode: values.readings_mode as string, include_lectures: values.include_lectures !== false }, published);
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Public website' }]} />
      <div class="page-head">
        <div>
          <h1>Public website</h1>
          <p class="lede"><span class={`chip ${published ? 'ok' : ''}`}>{published ? 'Published' : 'Not published'}</span>{published ? 'Updates daily.' : 'Optional: an open version of your materials for anyone.'}</p>
        </div>
        <div class="actions"><OpButtons def={def} /></div>
      </div>
      <Help title="What goes public" doc="reference/actions-reference.md">
        <p>The public website follows the settings here for now. A materials repo’s publish.yml selects files for it once it is rebuilt; until then it is only recorded. Withheld files never appear. The first publish saves these settings; a daily update keeps the site current.</p>
      </Help>
      <div class="grid-2">
        <section class="panel section">
          <h2>Settings</h2>
          {repos.length ? <SchemaForm id="ws" schema={null} tiers={tiers} values={values} onChange={setValues} /> : <p class="footnote">No materials repo yet: create one first.</p>}
        </section>
        <section class="panel section">
          <h2>State</h2>
          <dl class="kv">
            <dt>Address</dt><dd>{published ? <a href={`https://${course.org}.github.io`} target="_blank" rel="noopener">{course.org}.github.io <Ext /></a> : `${course.org}.github.io`}</dd>
          </dl>
        </section>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- materials repo settings

/** Beside a rule list: the rules that match no file, or that every rule matches one. */
function Unmatched({ rules, total, loading, partial }: { rules: string[]; total: number; loading: boolean; partial: boolean }) {
  if (loading) return <Loading />;
  if (!total) return <p class="footnote">No rules yet.</p>;
  if (partial) return <p class="footnote">Cannot tell which rules match nothing: the file list is incomplete.</p>;
  return rules.length ? (
    <ul class="unmatched">{rules.map((r) => <li><code>{r}</code> matches no file</li>)}</ul>
  ) : <p class="footnote">Every rule matches at least one file.</p>;
}

const PUBLISH_STUB = '# INSTRUCTOR-OWNED - yours. What the public website may publish; same syntax as .gitignore.\npublic:\n';
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
  const pubFile = p.files.file(course.org, repo, PUBLISH_FILE);
  const ignFile = p.files.file(course.org, repo, '.releaseignore');
  const matFile = p.files.file(course.org, repo, MATERIALS_FILE);
  const declared = matFile.kind === 'loading' ? NOTHING_DECLARED : readDeclared(matFile.kind === 'ready' ? matFile.text : null);
  const baseHolds: Holds = { syllabus: declared?.declared ? declared.syllabus : '', kinds: declared?.kinds ?? {} };
  const [holds, setHolds] = useState<Holds | null>(null);
  const [matSave, runMat, setMatSave] = useSave(env);
  const cur = holds ?? baseHolds;
  const folders = tree.kind === 'ready' ? tree.paths.filter((x) => x.dir && !x.path.includes('/') && !x.path.startsWith('.')).map((x) => x.path) : [];
  const kinds = folderKinds(folders, cur.kinds);
  const syllabus = cur.syllabus.trim() || DEFAULT_SYLLABUS;
  const pubY = pubFile.kind === 'ready' ? new YamlText(pubFile.text) : null;
  const pubList = pubY && !pubY.errors.length ? obj(pubY.toJS()).public : null;
  const pubText = Array.isArray(pubList) ? pubList.map(String).join('\n') : '';
  const ignText = ignFile.kind === 'ready' ? ignFile.text : '';
  const [pub, setPub] = useState<string | null>(null);
  const [ign, setIgn] = useState<string | null>(null);
  const [pubSave, runPub] = useSave(env);
  const [ignSave, runIgn] = useSave(env);
  const pubLines = (pub ?? pubText).split('\n').map((l) => l.trim()).filter(Boolean);
  const ignLines = (ign ?? ignText).split('\n');
  const scope = newestScope(p);
  const badged = badgeFiles(files, pubLines, ignLines);
  const repos = p.files.repos(course.org);
  const branch = (repos.kind === 'ready' ? repos.repos.find((r) => r.name === repo)?.default_branch : undefined) ?? null;
  const partial = tree.kind === 'ready' && tree.truncated;
  const savePub = async () => {
    if (pub === null) return;
    const y = new YamlText(pubFile.kind === 'ready' ? pubFile.text : PUBLISH_STUB);
    if (y.errors.length) return;
    y.assign(['public'], pubLines.length ? pubLines : []);
    if (await runPub({ owner: course.org, repo, path: PUBLISH_FILE }, y.text, pubFile.kind === 'ready' ? pubFile.sha : null, { message: 'materials: edit what the public website may publish, from the Instructor Console', statusRepo: [course.org, COURSE_REPO] })) setPub(null);
  };
  const setKind = (folder: string, kind: string) => {
    const next = { ...cur.kinds };
    delete next[folder.toLowerCase()];
    if (kind) next[folder.toLowerCase()] = kind;
    setHolds({ ...cur, kinds: next });
  };
  const saveMat = async () => {
    if (holds === null) return;
    const text = writeHolds(matFile.kind === 'ready' ? matFile.text : null, holds);
    if (text === null) return;
    const y = new YamlText(text);
    if (!validMaterials(y.toJS() ?? {})) return setMatSave({ kind: 'bad', text: invalidText(MATERIALS_FILE, validMaterials) });
    if (await runMat({ owner: course.org, repo, path: MATERIALS_FILE }, text, matFile.kind === 'ready' ? matFile.sha : null, { message: 'materials: edit the syllabus file and folder kinds, from the Instructor Console', statusRepo: [course.org, COURSE_REPO] })) setHolds(null);
  };
  const saveIgn = async () => {
    if (ign === null) return;
    const text = ign.endsWith('\n') || !ign ? ign : `${ign}\n`;
    if (await runIgn({ owner: course.org, repo, path: '.releaseignore' }, text, ignFile.kind === 'ready' ? ignFile.sha : null, { message: 'materials: edit what is withheld from students, from the Instructor Console', statusRepo: [course.org, COURSE_REPO] })) setIgn(null);
  };
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Materials', href: '#materials' }, { t: repo }]} />
      <div class="page-head">
        <div><h1>{repo}</h1><p class="lede">Materials repo settings. <span class="slug">{course.org}/{repo}</span></p></div>
        <div class="actions"><a class="btn quiet" href={`https://github.com/${course.org}/${repo}`} target="_blank" rel="noopener">Open on GitHub <Ext /></a></div>
      </div>
      <Help title="Public and withheld" doc="02-add-materials-to-course.md">
        <p>Materials live here privately until a scheduled release copies them to a semester. Files matching the withheld patterns never reach students. Files matching the public patterns are selected for the public website, so a deck opens in a browser there; until that site is rebuilt the selection is only recorded. Both use the same pattern syntax as .gitignore, written for the paths in this repo.</p>
      </Help>
      <div class="stack">
        <section class="panel section">
          <h2>Syllabus</h2>
          {m ? (m.state === 'ready' ? <div class="check-line ok"><Check /><span>Written.</span></div> : pubFile.kind === 'ready' ? <CheckLine cls="bad">Still the template text. Students would see the placeholder at the first release.</CheckLine> : <p class="footnote">Not ready yet.</p>) : <p class="footnote">Not checked yet.</p>}
          <div class="actions"><EditFile org={course.org} repo={repo} path={syllabus} /></div>
          {scope ? (
            <>
              <p class="footnote">Builds a paste-ready ‘Course sessions and readings’ block from the semester schedule and its readings entries. Write saves it as <code>SYLLABUS.sessions.md</code> in this repo; <code>{syllabus}</code> is yours and is never touched.</p>
              <div class="actions"><OpButtons def={generateSyllabus(scope, repo)} small previewLabel="Preview the session list" /></div>
            </>
          ) : null}
        </section>
        <section class="panel section">
          <h2>Syllabus file and folder kinds</h2>
          <div class="field">
            <label for="m-syl">Syllabus file <span class="default">default: {DEFAULT_SYLLABUS}</span></label>
            <input type="text" id="m-syl" list="m-syl-files" placeholder={DEFAULT_SYLLABUS} value={cur.syllabus} onInput={(e) => setHolds({ ...cur, syllabus: (e.target as HTMLInputElement).value })} />
            <datalist id="m-syl-files">{files.filter((f) => !f.includes('/')).map((f) => <option value={f} />)}</datalist>
            <p class="hint">The file the student site pins as the syllabus, at the repo’s top level.</p>
          </div>
          <p class="footnote">A release entry that names no kind takes the kind of the top folder it lands in.</p>
          {tree.kind === 'loading' ? <Loading /> : kinds.length ? (
            <ul class="kinds">
              {kinds.map((k) => (
                <li>
                  <code>{k.folder}/</code> <span class="chip">{KIND_LABEL[k.kind] ?? k.kind}</span> <span class="footnote">{FROM_WORD[k.from]}</span>
                  <label class="inline"> This folder is: <select aria-label={`Kind of ${k.folder}`} onChange={(e) => setKind(k.folder, (e.target as HTMLSelectElement).value)}>
                    <option value="" selected={k.from !== 'declared'}>{resetLabel(k.folder)}</option>
                    {CONTENT_KINDS.map((x) => <option value={x} selected={k.from === 'declared' && k.kind === x}>{KIND_LABEL[x] ?? x}</option>)}
                  </select></label>
                </li>
              ))}
            </ul>
          ) : <p class="footnote">No folders yet.</p>}
          {declared === null ? <CheckLine cls="bad">{MATERIALS_FILE} does not parse; fix it with Edit the file.</CheckLine> : null}
          <SaveBar state={matSave} onSave={() => void saveMat()} small disabled={holds === null || deepEqual(holds, baseHolds) || declared === null} file={{ org: course.org, repo, path: MATERIALS_FILE }} />
          <Lives org={course.org} repo={repo} path={MATERIALS_FILE} />
        </section>
        <section class="panel section">
          <h2>For the public website</h2>
          <div class="pattern-grid">
            <div class="field">
              <label for="pub-pat">Public patterns</label>
              <textarea class="code" id="pub-pat" placeholder="Nothing public" onInput={(e) => setPub((e.target as HTMLTextAreaElement).value)}>{pub ?? pubText}</textarea>
              <p class="hint">One pattern per line, for the paths in this repo. Empty means nothing is public.</p>
              {pubY?.errors.length ? <CheckLine cls="bad">publish.yml does not parse ({pubY.errors[0]}); fix it with Edit the file.</CheckLine> : null}
            </div>
            <div class="field"><span class="label">Rules</span><Unmatched rules={badged.unmatched.public} total={badged.rules.public} loading={tree.kind === 'loading'} partial={partial} /></div>
          </div>
          <SaveBar state={pubSave} onSave={() => void savePub()} small disabled={pub === null || pub === pubText || !!pubY?.errors.length} file={{ org: course.org, repo, path: PUBLISH_FILE }} />
          <Lives org={course.org} repo={repo} path={PUBLISH_FILE} />
        </section>
        <section class="panel section">
          <h2>Withheld from students</h2>
          <div class="pattern-grid">
            <div class="field">
              <label for="ign-pat">Withheld patterns</label>
              <textarea class="code" id="ign-pat" onInput={(e) => setIgn((e.target as HTMLTextAreaElement).value)}>{ign ?? ignText}</textarea>
              <p class="hint">A release that needs a withheld file is reported as a problem. This preview reads the repo’s top-level .releaseignore; one in a subfolder still applies there.</p>
            </div>
            <div class="field"><span class="label">Rules</span><Unmatched rules={badged.unmatched.withheld} total={badged.rules.withheld} loading={tree.kind === 'loading'} partial={partial} /></div>
          </div>
          <SaveBar state={ignSave} onSave={() => void saveIgn()} small disabled={ign === null || ign === ignText} file={{ org: course.org, repo, path: '.releaseignore' }} />
          <Lives org={course.org} repo={repo} path=".releaseignore" />
        </section>
        <section class="panel section">
          <h2>Files</h2>
          <p class="footnote">What happens to each file at a release, from the rules above as you type them, by the engine’s own rule. Withheld wins over public. A public deck brings its <code>_files/</code> folder and any {BUNDLE_WORDS} folder beside it; solutions, tests, grading_config.yml and .env files are never public.</p>
          {partial ? <CheckLine cls="bad">GitHub returned only part of this repo’s file list; badges may be incomplete.</CheckLine> : null}
          {tree.kind === 'loading' ? <Loading what="Reading the repo" /> : tree.kind === 'absent' ? <p class="footnote">Could not read the repo’s files.</p> : <FileTree files={files} badges={badged.badges} org={course.org} repo={repo} branch={branch} kinds={Object.fromEntries(kinds.map((k) => [k.folder, KIND_LABEL[k.kind] ?? k.kind]))} />}
        </section>
      </div>
    </>
  );
}
