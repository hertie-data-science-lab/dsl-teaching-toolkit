// Course-side editors: S3 Course details (dsl-course.yml), S20 Public website, S19
// Materials repo settings (publish.yml, .releaseignore).

import type { ErrorObject } from 'ajv/dist/2020';
import { useState } from 'preact/hooks';
import courseSchema from '../../schemas/dsl_course.schema.json';
import { useEnv, type Env } from '../env';
import { badgeFiles } from '../edit/badges';
import { invalidText, useSave } from '../edit/save';
import { YamlText, deepEqual, obj } from '../edit/yamlText';
import { SchemaForm, fieldErrors } from '../forms/Form';
import { validator } from '../model/validate';
import { generateSyllabus, publishWebsite, type Scope } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { ABOUT, ASSIGNMENT_DEFAULTS } from '../tiers/course';
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

export function courseScope(p: Pick<CourseProps, 'course'>): Scope {
  return { courseOrg: p.course.org, where: p.course.name };
}

/** The course's newest semester, for the course-page ops the engine runs per semester. */
export function newestScope(p: Pick<CourseProps, 'course'>): Scope | null {
  const k = p.course.cohorts[0];
  return k ? { courseOrg: p.course.org, cohortOrg: k.org, where: k.termLabel } : null;
}

const clean = (v: Values): Values => Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x === '' || x === null ? undefined : x]));

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
    about: clean({ course_name: meta.course_name, course_code: meta.course_code, course_description: meta.course_description }),
    defaults: clean({ ...ad, formats: formatsList(ad.formats)[0] }),
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

/**
 * What the console's dsl_course schema refuses but the engine accepts (research 07 section 5),
 * so a save goes ahead with a note: `start`/`end` on a course instructor card
 * (`site_repo._people_from_meta` honours them) and a course-level `teaching_assistants`
 * list (the engine ignores it: no site shows course-level assistants).
 */
function tolerated(e: ErrorObject): string | null {
  if (e.keyword !== 'additionalProperties') return null;
  const key = (e.params as { additionalProperty: string }).additionalProperty;
  if (/^\/people\/instructors\/\d+$/.test(e.instancePath) && (key === 'start' || key === 'end'))
    return 'a course instructor card has start or end dates; the student site honours them';
  if (e.instancePath === '/people' && key === 'teaching_assistants') return 'it lists course-level teaching assistants; the engine ignores them, since assistants are set per semester under Instructors';
  return null;
}

/** dsl-course.yml after `after`, or why it would not be valid; `warning` names what the engine reads but the check does not know. */
export function courseFileAfter(text: string, before: Details, after: Details, meta: Record<string, unknown>): { text: string; warning?: string } | { error: string } {
  const out = new YamlText(text);
  writeDetails(out, before, after, meta);
  if (validCourse(out.toJS())) return { text: out.text };
  const errors = validCourse.errors ?? [];
  const known = errors.map(tolerated);
  if (known.some((k) => k === null)) return { error: invalidText('dsl-course.yml', { errors: errors.filter((_, i) => known[i] === null) }) };
  return { text: out.text, warning: `Saved without the console’s check, which does not know these yet: ${[...new Set(known)].sort().join('. Also, ')}.` };
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
  const [warning, setWarning] = useState('');
  const y = file.kind === 'ready' ? new YamlText(file.text) : null;
  const meta = y && !y.errors.length ? obj(y.toJS()) : {};
  const before = detailsOf(meta);
  const d = draft ?? before;
  const ready = courseView(p).course?.ready ?? false;
  const set = (patch: Partial<Details>) => {
    setDraft({ ...d, ...patch });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const errs = { ...fieldErrors(null, ABOUT, d.about), ...fieldErrors(null, ASSIGNMENT_DEFAULTS, d.defaults) };
  const doSave = async () => {
    setWarning('');
    if (!y || file.kind !== 'ready') return;
    if (p.migrated === false) return setSave({ kind: 'bad', text: 'Not saved: the console has not yet confirmed this course uses the current names.' });
    if (Object.keys(errs).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const after = d;
    const out = courseFileAfter(file.text, before, after, meta);
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    const missing = await missingAdmin(env, before.admins, after.admins);
    if (missing) return setSave({ kind: 'bad', text: `There is no GitHub account called ${missing}.` });
    // The note describes a file that was written, so it shows only once the save went through.
    if (await runSave({ owner: course.org, repo: COURSE_REPO, path: 'dsl-course.yml' }, out.text, file.sha, { message: 'course: edit the course details, from the Instructor Console', statusRepo: [course.org, COURSE_REPO] })) {
      setDraft(null);
      setWarning(out.warning ?? '');
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
        <p>These are the course’s defaults. Every one can be overridden per assignment or per semester.</p>
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
              <h3>Assignment defaults</h3>
              <SchemaForm id="cdx" schema={null} tiers={ASSIGNMENT_DEFAULTS} values={d.defaults} onChange={(v) => set({ defaults: v })} />
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
              {warning ? <CheckLine cls="warn">{warning}</CheckLine> : null}
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
  const pub = src ? p.files.file(course.org, src, 'publish.yml') : null;
  let patterns: string[] = [];
  if (pub?.kind === 'ready') {
    const y = new YamlText(pub.text);
    const list = y.errors.length ? null : obj(y.toJS()).public;
    patterns = Array.isArray(list) ? list.map(String) : [];
  }
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
        <p>Only files matching each materials repo’s public patterns appear. Withheld files never do. The first publish saves these settings; a daily update keeps the site current.</p>
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
            <dt>Public patterns</dt><dd>{patterns.length ? patterns.join(', ') : 'Nothing public'}</dd>
          </dl>
          {src ? <a class="textlink" href={`#materials-${src}`}>Change what is public</a> : null}
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

const PUBLISH_STUB = '# INSTRUCTOR-OWNED - yours. What the semester site hosts PUBLICLY; same syntax as .gitignore.\npublic:\n';

export function MaterialsScreen(p: CourseProps) {
  const { course, entry } = p;
  const repo = entry ?? '';
  const env = useEnv();
  const v = courseView(p);
  const m = v.course?.materials?.find((x) => x.repo === repo);
  const tree = p.files.tree(course.org, repo);
  const files = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir).map((x) => x.path) : [];
  const pubFile = p.files.file(course.org, repo, 'publish.yml');
  const ignFile = p.files.file(course.org, repo, '.releaseignore');
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
    if (await runPub({ owner: course.org, repo, path: 'publish.yml' }, y.text, pubFile.kind === 'ready' ? pubFile.sha : null, { message: 'materials: edit what is published openly, from the Instructor Console', statusRepo: [course.org, COURSE_REPO] })) setPub(null);
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
        <p>Materials live here privately until a scheduled release copies them to a semester. Files matching the withheld patterns never reach students. Files matching the public patterns also appear on the public website. Both use the same pattern syntax as .gitignore.</p>
      </Help>
      <div class="stack">
        <section class="panel section">
          <h2>Syllabus</h2>
          {m ? (m.state === 'ready' ? <div class="check-line ok"><Check /><span>Written.</span></div> : pubFile.kind === 'ready' ? <CheckLine cls="bad">Still the template text. Students would see the placeholder at the first release.</CheckLine> : <p class="footnote">Not ready yet.</p>) : <p class="footnote">Not checked yet.</p>}
          <div class="actions"><EditFile org={course.org} repo={repo} path="SYLLABUS.md" /></div>
          {scope ? (
            <>
              <p class="footnote">Builds a paste-ready ‘Course sessions and readings’ block from the semester schedule and the <code>readings/NN_*</code> folders. Write saves it as <code>SYLLABUS.sessions.md</code> in this repo; <code>SYLLABUS.md</code> is yours and is never touched.</p>
              <div class="actions"><OpButtons def={generateSyllabus(scope, repo)} small previewLabel="Preview the session list" /></div>
            </>
          ) : null}
        </section>
        <section class="panel section">
          <h2>Published openly</h2>
          <div class="pattern-grid">
            <div class="field">
              <label for="pub-pat">Public patterns</label>
              <textarea class="code" id="pub-pat" placeholder="Nothing public" onInput={(e) => setPub((e.target as HTMLTextAreaElement).value)}>{pub ?? pubText}</textarea>
              <p class="hint">One pattern per line. Empty means nothing is public.</p>
              {pubY?.errors.length ? <CheckLine cls="bad">publish.yml does not parse ({pubY.errors[0]}); fix it with Edit the file.</CheckLine> : null}
            </div>
            <div class="field"><span class="label">Rules</span><Unmatched rules={badged.unmatched.public} total={badged.rules.public} loading={tree.kind === 'loading'} partial={partial} /></div>
          </div>
          <SaveBar state={pubSave} onSave={() => void savePub()} small disabled={pub === null || pub === pubText || !!pubY?.errors.length} file={{ org: course.org, repo, path: 'publish.yml' }} />
          <Lives org={course.org} repo={repo} path="publish.yml" />
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
          <p class="footnote">What happens to each file at a release, from the rules above as you type them. Withheld wins over published openly. A published deck’s <code>_files/</code> folder is published with it; solutions, tests, grading_config.yml and .env files are never published.</p>
          {partial ? <CheckLine cls="bad">GitHub returned only part of this repo’s file list; badges may be incomplete.</CheckLine> : null}
          {tree.kind === 'loading' ? <Loading what="Reading the repo" /> : tree.kind === 'absent' ? <p class="footnote">Could not read the repo’s files.</p> : <FileTree files={files} badges={badged.badges} org={course.org} repo={repo} branch={branch} />}
        </section>
      </div>
    </>
  );
}
