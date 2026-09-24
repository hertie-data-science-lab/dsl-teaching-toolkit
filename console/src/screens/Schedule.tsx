// S6 Schedule with its entry sheet (the schedule editor) and S11 Release detail.

import { useMemo, useState } from 'preact/hooks';
import scheduleSchema from '../../schemas/schedule.schema.json';
import { useEnv } from '../env';
import { compileAll, matchRules } from '../edit/glob';
import { invalidText, useSave } from '../edit/save';
import { YamlText, deepEqual } from '../edit/yamlText';
import { Field, Invalid } from '../forms/Form';
import { RELEASE_WORD, TYPE_CLASS, TYPE_LABEL, fmtDay, fmtTime, fmtWhen, releaseIdent, sortKey } from '../model/format';
import { parseSchedule, scheduleRows, type Block, type Row } from '../model/schedule';
import {
  ARCHIVE_GRACE_DAYS, blankDraft, blockOf, draftErrors, freshId, readDraft, slugOfTemplate, writeDraft,
  type AssignmentDraft, type ArchiveDraft, type DeployDraft, type Draft, type EventDraft, type ReleaseDraft, type TermDraft,
} from '../model/scheduleEdit';
import type { Release } from '../model/types';
import { validator } from '../model/validate';
import { keepFuture, releaseAdhoc, releaseAgain, releaseEarly, releaseNow, scheduledPreview } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import type { FieldTier } from '../tiers/types';
import { TIMEZONES } from '../tiers/course';
import { Crumbs, EditFile, Help, Lives, Md, ProblemCards, ghUrl } from '../ui/bits';
import { SaveLine, UnsavedBar, lineOf } from '../ui/edit';
import { Check } from '../ui/icons';
import { NOTHING_TO_RELEASE, releaseRef } from './Cohort';
import { NotFound } from './Assignments';
import { CheckNow, WithStatus, cohortCrumbs, cohortScope, gradingConfig, tzOf, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';

const LABELS: Record<Block, string> = { releases: 'Releases', assignments: 'Assignments', events: 'Events' };
const validSchedule = validator(scheduleSchema);

/** What the student site shows in the Details cell. */
function Details({ r }: { r: Row }) {
  if (r.entry === 'term') return <span class="det" />;
  if (!r.show) return <span class="det"><span class="gen">Hidden from the student site; still {r.block === 'releases' ? 'released' : 'runs'}.</span></span>;
  return (
    <span class="det">
      <Md src={r.details} />
      {r.block === 'releases' && r.state !== 'released' ? <p class="gen">Materials for {r.ident} are not yet released.</p> : null}
      {r.block === 'assignments' && r.type === 'due' && !r.details ? <span>Submit via <code>{r.entry}-&lt;your-handle&gt;</code></span> : null}
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

function useSchedFile(p: CohortProps): SchedFile | null | 'loading' {
  const f = p.files.file(p.cohort.org, 'classroom-config', 'schedule.yml');
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

// ------------------------------------------------------------------ the entry sheet

type Setter<T> = (patch: Partial<T>) => void;

function F<T>({ id, k, t, d, set, error }: { id: string; k: keyof T & string; t: FieldTier; d: T; set: Setter<T>; error?: string }) {
  return <Field id={id} k={k} t={t} value={(d as Record<string, unknown>)[k]} values={d as Record<string, unknown>} error={error} set={(key, v) => set({ [key]: v ?? (t.widget === 'checkbox' ? false : '') } as Partial<T>)} />;
}

function Common<T extends { show: boolean; tbc: boolean }>({ d, set }: { d: T; set: Setter<T> }) {
  return (
    <>
      <F id="e-show" k="show" d={d} set={set} t={{ tier: 'default', label: 'Show on the student site', widget: 'checkbox', defaultLabel: 'off hides the row; it still runs' }} />
      <F id="e-tbc" k="tbc" d={d} set={set} t={{ tier: 'default', label: 'To be confirmed', widget: 'checkbox', defaultLabel: 'adds “(TBC)” beside the time' }} />
    </>
  );
}

const MD = (label: string, reason?: string): FieldTier => ({ tier: 'default', label, widget: 'markdown', reason });

function FolderCheck({ p, dp, i, onSuggest }: { p: ReadyProps; dp: DeployDraft; i: number; onSuggest: (v: string) => void }) {
  if (!dp.repo || !dp.folder) return null;
  const tree = p.files.tree(p.course.org, dp.repo);
  if (tree.kind === 'loading') return <span class="footnote">Checking the folder…</span>;
  if (tree.kind === 'absent') return <Invalid>{dp.repo} was not found in the course, or you cannot read it.</Invalid>;
  const folder = dp.folder.replace(/\/+$/, '');
  const hit = tree.paths.find((x) => x.path === folder);
  if (!hit) {
    const near = tree.paths.find((x) => x.dir && x.path.startsWith(folder)) ?? tree.paths.find((x) => x.dir && x.path.split('/').pop() === folder.split('/').pop());
    return (
      <Invalid>
        Not found. The release will be skipped until the folder exists.
        {near ? <span class="fix-hint">Use the one that exists: <button class="textlink" type="button" style="min-height:0;padding:0" onClick={() => onSuggest(near.path)} data-i={i}>{near.path}</button></span> : null}
      </Invalid>
    );
  }
  const ign = p.files.file(p.course.org, dp.repo, '.releaseignore');
  const rules = compileAll(ign.kind === 'ready' ? ign.text.split('\n') : []);
  const files = hit.dir ? tree.paths.filter((x) => !x.dir && x.path.startsWith(`${folder}/`)) : [hit];
  const withheld = files.filter((x) => matchRules(rules, x.path)).length;
  return <span class="valid-msg"><Check />Ready; {files.length} file{files.length === 1 ? '' : 's'}{withheld ? `, ${withheld} withheld` : ''}</span>;
}

function ReleaseForm({ p, d, set, errors, repos }: { p: ReadyProps; d: ReleaseDraft; set: Setter<ReleaseDraft>; errors: Record<string, string>; repos: string[] }) {
  const tz = tzOf(p.status);
  const setDeploy = (i: number, patch: Partial<DeployDraft>) => set({ deploys: d.deploys.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  return (
    <>
      <div class="row-2">
        <F id="e-type" k="type" d={d} set={set} t={{ tier: 'default', label: 'Type', widget: 'select', defaultLabel: 'default: from where it lands', reason: 'Sets the row’s colour and its identifier.', options: [{ value: '', label: 'from the folder' }, { value: 'lecture', label: 'lecture' }, { value: 'lab', label: 'lab' }, { value: 'readings', label: 'readings' }] }} />
        <F id="e-title" k="title" d={d} set={set} t={{ tier: 'default', label: 'Title', defaultLabel: 'from the folder name', reason: 'The name, shown after the identifier. Plain text.' }} />
      </div>
      {d.type === 'readings' ? <p class="footnote">On the student site, readings appear inside the week’s lecture or lab row, not as their own row.</p> : null}
      <div class="row-2">
        <F id="e-date" k="date" d={d} set={set} error={errors.date} t={{ tier: 'ask', label: 'When', widget: 'date', reason: `Automation releases at this time, ${tz}.` }} />
        <F id="e-time" k="time" d={d} set={set} t={{ tier: 'ask', label: 'At', widget: 'time' }} />
      </div>
      <F id="e-det" k="details" d={d} set={set} t={MD('Details', 'Shown to students under the title.')} />
      <Common d={d} set={set} />
      <div class="field">
        <span class="label">What to release</span>
        {d.deploys.map((dp, i) => {
          const tree = dp.repo ? p.files.tree(p.course.org, dp.repo) : null;
          const adv = [dp.dest && dp.dest !== 'materials', !!dp.path, dp.diff].filter(Boolean).length;
          return (
            <div class="deploy">
              <div class="deploy-head">Deploy {i + 1}{d.deploys.length > 1 ? <button class="x" type="button" aria-label={`Remove deploy ${i + 1}`} onClick={() => set({ deploys: d.deploys.filter((_, j) => j !== i) })}>&times;</button> : null}</div>
              <div class="row-2">
                <div class="field">
                  <label for={`e-d${i}-repo`}>From repo</label>
                  <select id={`e-d${i}-repo`} onChange={(e) => setDeploy(i, { repo: (e.target as HTMLSelectElement).value })}>
                    {[...new Set([...repos, dp.repo].filter(Boolean))].map((r) => <option value={r} selected={r === dp.repo}>{r}</option>)}
                    {!dp.repo ? <option value="" selected>Choose a repo</option> : null}
                  </select>
                  {errors[`deploy${i}.repo`] ? <Invalid>{errors[`deploy${i}.repo`]}</Invalid> : null}
                </div>
                <div class="field">
                  <label for={`e-d${i}-folder`}>Folder</label>
                  <input type="text" id={`e-d${i}-folder`} list={`folders-${i}`} value={dp.folder} aria-invalid={errors[`deploy${i}.folder`] ? 'true' : undefined} onInput={(e) => setDeploy(i, { folder: (e.target as HTMLInputElement).value })} />
                  {tree?.kind === 'ready' ? <datalist id={`folders-${i}`}>{tree.paths.filter((x) => x.dir).map((x) => <option value={x.path} />)}</datalist> : null}
                  {errors[`deploy${i}.folder`] ? <Invalid>{errors[`deploy${i}.folder`]}</Invalid> : <FolderCheck p={p} dp={dp} i={i} onSuggest={(v) => setDeploy(i, { folder: v })} />}
                </div>
              </div>
              <details class="fold" open={adv > 0}>
                <summary>Advanced <span class={`cnt${adv ? ' changed' : ''}`}>({adv ? `${adv} changed` : 'none changed'})</span></summary>
                <div class="fold-body">
                  <div class="row-2">
                    <div class="field"><label for={`e-d${i}-dest`}>To repo <span class="default">default: materials</span></label><input type="text" id={`e-d${i}-dest`} placeholder="materials" value={dp.dest} onInput={(e) => setDeploy(i, { dest: (e.target as HTMLInputElement).value })} /><p class="why">In the cohort. Blank means materials.</p></div>
                    <div class="field"><label for={`e-d${i}-path`}>To path <span class="default">default: same as the folder</span></label><input type="text" id={`e-d${i}-path`} placeholder={dp.folder} value={dp.path} onInput={(e) => setDeploy(i, { path: (e.target as HTMLInputElement).value })} /></div>
                  </div>
                  <label class="check"><input type="checkbox" checked={dp.diff} onChange={(e) => setDeploy(i, { diff: (e.target as HTMLInputElement).checked })} /><span>Release at a different time than the session</span></label>
                  {dp.diff ? (
                    <div class="cond">
                      <div class="row-2">
                        <div class="field"><label for={`e-d${i}-at`}>Release this deploy at</label><input type="date" id={`e-d${i}-at`} value={dp.atDate || d.date} onInput={(e) => setDeploy(i, { atDate: (e.target as HTMLInputElement).value })} />{errors[`deploy${i}.at`] ? <Invalid>{errors[`deploy${i}.at`]}</Invalid> : null}</div>
                        <div class="field"><label for={`e-d${i}-att`}>At</label><input type="time" id={`e-d${i}-att`} value={dp.atTime} onInput={(e) => setDeploy(i, { atTime: (e.target as HTMLInputElement).value })} /></div>
                      </div>
                    </div>
                  ) : null}
                </div>
              </details>
            </div>
          );
        })}
        <div><button class="btn small quiet" type="button" onClick={() => set({ deploys: [...d.deploys, { repo: repos[0] ?? '', folder: '', dest: '', path: '', diff: false, atDate: '', atTime: '' }] })}>Add another deploy</button></div>
      </div>
      {d.id ? <a class="textlink" href={`#release-${d.id}`}>Open release details</a> : null}
    </>
  );
}

function templateVisibility(p: ReadyProps, template: string): string {
  return template ? String(gradingConfig(p, template).visibility ?? 'private') : 'private';
}

function AssignmentForm({ p, d, set, errors, templates, lateDays }: { p: ReadyProps; d: AssignmentDraft; set: Setter<AssignmentDraft>; errors: Record<string, string>; templates: { repo: string; slug: string; state: string }[]; lateDays: string }) {
  const tpl = templates.find((t) => t.repo === d.template);
  const vis = templateVisibility(p, d.template);
  const opts = [...templates.map((t) => ({ value: t.repo, label: `${t.slug.replace(/^assignment-(\d+).*/, 'Assignment $1')} (${t.repo})` }))];
  if (d.template && !tpl) opts.push({ value: d.template, label: d.template });
  return (
    <>
      <div class="field">
        <label for="e-tpl">Assignment template</label>
        <select id="e-tpl" onChange={(e) => set({ template: (e.target as HTMLSelectElement).value })}>
          {!d.template ? <option value="" selected>Choose an assignment template</option> : null}
          {opts.map((o) => <option value={o.value} selected={o.value === d.template}>{o.label}</option>)}
        </select>
        {errors.template ? <Invalid>{errors.template}</Invalid>
          : tpl && tpl.state !== 'ready' ? <Invalid>This assignment template has a problem. <a href={`#template-${tpl.slug}`}>Fix it on the assignment template</a></Invalid>
          : d.template ? <span class="valid-msg"><Check />Assignment template ready</span> : null}
        <p class="why">Must exist and be ready.</p>
      </div>
      <F id="e-title" k="title" d={d} set={set} t={{ tier: 'default', label: 'Title', defaultLabel: 'from the assignment template', reason: 'The name after the assignment’s number. Also feeds repo names.' }} />
      <div class="field">
        <span class="label">Hand out</span>
        <div class="choices">
          <label class="choice"><input type="radio" name="e-ho" checked={!d.manual} onChange={() => set({ manual: false })} /><b>At a time</b><span>Automation hands out then.</span></label>
          <label class="choice"><input type="radio" name="e-ho" checked={d.manual} onChange={() => set({ manual: true })} /><b>I will hand out manually</b><span>From the assignment page.</span></label>
        </div>
      </div>
      {!d.manual ? (
        <div class="cond"><div class="row-2">
          <F id="e-hod" k="handoutDate" d={d} set={set} error={errors.handout} t={{ tier: 'conditional', label: 'Hand out on', widget: 'date' }} />
          <F id="e-hot" k="handoutTime" d={d} set={set} t={{ tier: 'conditional', label: 'At', widget: 'time' }} />
        </div></div>
      ) : null}
      <div class="row-2">
        <F id="e-due" k="dueDate" d={d} set={set} error={errors.due} t={{ tier: 'ask', label: 'Due', widget: 'date' }} />
        <F id="e-duet" k="dueTime" d={d} set={set} t={{ tier: 'ask', label: 'At', widget: 'time' }} />
      </div>
      <div class="row-2">
        <F id="e-late" k="lateDate" d={d} set={set} error={errors.late} t={{ tier: 'default', label: 'Late work until', widget: 'date', defaultLabel: `default: due + ${lateDays} days`, reason: 'Late work is accepted with the penalty until then; marking starts after.' }} />
        <F id="e-latet" k="lateTime" d={d} set={set} t={{ tier: 'default', label: 'At', widget: 'time' }} />
      </div>
      {vis !== 'private' ? (
        <div class="field"><span class="label">Solution shown</span><div class="readonly">Not available: student repos are not private, so the solution cannot be pushed automatically.</div></div>
      ) : d.manual ? (
        <div class="field"><span class="label">Solution shown</span><div class="readonly">Needs a hand out time; the solution must follow it.</div></div>
      ) : (
        <>
          <F id="e-solon" k="solutionOn" d={d} set={set} t={{ tier: 'default', label: 'Show the solution', widget: 'checkbox', defaultLabel: 'default: off' }} />
          {d.solutionOn ? (
            <div class="cond"><div class="row-2">
              <F id="e-sol" k="solutionDate" d={d} set={set} error={errors.solution} t={{ tier: 'conditional', label: 'Solution shown on', widget: 'date', reason: 'Must be after the hand out.' }} />
              <F id="e-solt" k="solutionTime" d={d} set={set} t={{ tier: 'conditional', label: 'At', widget: 'time' }} />
            </div></div>
          ) : null}
        </>
      )}
      <F id="e-det" k="details" d={d} set={set} t={MD('Details', 'Shown to students under the title.')} />
      <Common d={d} set={set} />
      <details class="fold" open={!!errors.cohortRepo || !!d.cohortRepo}>
        <summary>Advanced <span class={`cnt${d.cohortRepo ? ' changed' : ''}`}>({d.cohortRepo ? '1 changed' : 'none changed'})</span></summary>
        <div class="fold-body">
          <F id="e-crepo" k="cohortRepo" d={d} set={set} error={errors.cohortRepo} t={{ tier: 'advanced', label: 'Repo name in the cohort', defaultLabel: 'derived', placeholder: slugOfTemplate(d.template), reason: 'Needed only when two entries hand out the same template; each must differ.' }} />
        </div>
      </details>
      {d.id ? <a class="textlink" href={`#assignment-${d.id}`}>Open the assignment</a> : null}
    </>
  );
}

function EventForm({ d, set, errors }: { d: EventDraft; set: Setter<EventDraft>; errors: Record<string, string> }) {
  return (
    <>
      <div class="row-2">
        <F id="e-etype" k="type" d={d} set={set} t={{ tier: 'ask', label: 'Type', widget: 'select', options: [{ value: 'exam', label: 'exam' }, { value: 'special_event', label: 'special event' }] }} />
        <F id="e-title" k="title" d={d} set={set} error={errors.title} t={{ tier: 'ask', label: 'Title', reason: 'Plain text; the student site shows only this, in bold.' }} />
      </div>
      <div class="row-2">
        <F id="e-date" k="date" d={d} set={set} t={{ tier: 'ask', label: 'When', widget: 'date', reason: 'Leave the date empty and the student site shows TBC at the end of term.' }} />
        <F id="e-time" k="time" d={d} set={set} t={{ tier: 'ask', label: 'At', widget: 'time', defaultLabel: 'optional' }} />
      </div>
      <F id="e-det" k="details" d={d} set={set} t={MD('Details')} />
      <Common d={d} set={set} />
    </>
  );
}

function TermForm({ d, set, errors }: { d: TermDraft; set: Setter<TermDraft>; errors: Record<string, string> }) {
  return (
    <>
      <div class="row-2">
        <F id="e-ts" k="start" d={d} set={set} t={{ tier: 'default', label: 'Term starts', widget: 'date', defaultLabel: 'inferred from the term', reason: 'Everything on the site’s calendar hangs off these.' }} />
        <F id="e-te" k="end" d={d} set={set} error={errors.end} t={{ tier: 'default', label: 'Term ends', widget: 'date', defaultLabel: 'default: +15 weeks' }} />
      </div>
      <details class="fold" open={!!d.tz && d.tz !== 'Europe/Berlin'}>
        <summary>Advanced <span class={`cnt${d.tz && d.tz !== 'Europe/Berlin' ? ' changed' : ''}`}>({d.tz && d.tz !== 'Europe/Berlin' ? '1 changed' : 'none changed'})</span></summary>
        <div class="fold-body">
          <F id="e-tz" k="tz" d={d} set={set} t={{ tier: 'advanced', label: 'Timezone', widget: 'select', defaultLabel: 'default: Europe/Berlin', reason: 'Every date in this schedule is in this timezone.', options: [{ value: '', label: 'Europe/Berlin (default)' }, ...[...new Set([...TIMEZONES, d.tz].filter(Boolean))].map((t) => ({ value: t, label: t }))] }} />
        </div>
      </details>
    </>
  );
}

function ArchiveForm({ d, set, errors }: { d: ArchiveDraft; set: Setter<ArchiveDraft>; errors: Record<string, string> }) {
  return (
    <>
      <F id="e-aon" k="on" d={d} set={set} t={{ tier: 'default', label: 'Archive automatically', widget: 'checkbox', defaultLabel: 'off means never' }} />
      {d.on ? (
        <div class="cond">
          <F id="e-ad" k="date" d={d} set={set} t={{ tier: 'default', label: 'Archive on', widget: 'date', defaultLabel: `default: ${d.graceDays === '' ? ARCHIVE_GRACE_DAYS : d.graceDays} days after the term ends`, reason: 'Every repo becomes read-only then. Students keep access; nothing is deleted.' }} />
          <F id="e-agrace" k="graceDays" d={d} set={set} error={errors.graceDays} t={{ tier: 'default', label: 'Days after term end', widget: 'number', defaultLabel: `default: ${ARCHIVE_GRACE_DAYS}`, reason: `Archiving happens this many days after the term ends; ${ARCHIVE_GRACE_DAYS} by default.` }} />
          <F id="e-at" k="title" d={d} set={set} t={{ tier: 'default', label: 'Title', defaultLabel: 'default: Cohort archived' }} />
          <F id="e-det" k="details" d={d} set={set} t={MD('Details', '{date} is filled in with the archive date.')} />
          <Common d={d} set={set} />
        </div>
      ) : null}
    </>
  );
}

const NEW_TYPES: [string, string, string][] = [['lecture', 'lecture', 'lec'], ['lab', 'lab', 'lab'], ['readings', 'readings', 'lec'], ['handout', 'hand out', 'asg'], ['exam', 'exam', 'exam'], ['special_event', 'event', 'evt']];

function identOf(d: Draft, row: Row | undefined, doc: Record<string, unknown>): string {
  if (row) return row.ident;
  if (d.kind === 'assignments') return (slugOfTemplate(d.template) || 'assignment').replace(/^assignment-(\d+).*/, 'Assignment $1');
  if (d.kind === 'events') return d.type === 'exam' ? 'Exam' : 'Event';
  if (d.kind === 'releases') {
    const n = Object.values((doc.releases ?? {}) as Record<string, { type?: string }>).filter((x) => (x.type || 'lecture') === (d.type || 'lecture')).length + 1;
    return d.type === 'readings' ? 'Readings' : `${d.type === 'lab' ? 'Lab' : 'Session'} ${n}`;
  }
  return '';
}

// ------------------------------------------------------------------ the page

function View(p: ReadyProps) {
  const { status, now } = p;
  const env = useEnv();
  const tz = tzOf(status), year = yearOf(now, tz);
  const scope = cohortScope(p);
  const [filters, setFilters] = useState<Record<Block, boolean>>({ releases: true, assignments: true, events: true });
  const [drafts, setDrafts] = useState<Record<string, Draft>>((): Record<string, Draft> =>
    p.prefill && p.entry === 'new' ? { new: { ...(blankDraft('handout', { repo: '' }) as AssignmentDraft), template: p.prefill } } : {},
  );
  const [removed, setRemoved] = useState<Record<string, Block>>({});
  const [save, runSave, setSave] = useSave(env);
  const file = useSchedFile(p);
  const sf = file && file !== 'loading' ? file : null;
  const doc = sf?.doc ?? {};
  const sched = useMemo(() => (sf && !sf.error ? parseSchedule(sf.text) : null), [sf?.text]);
  const rows = scheduleRows(status, sched, now, tz);
  const key = p.entry;
  const current = key && key !== 'new' && key !== 'term' && key !== 'archive' ? rows.find((r) => r.entry === key) : undefined;
  const repos = (status.course?.materials ?? []).map((m) => m.repo);
  const templates = status.course?.templates ?? [];
  const lateDays = String(((p.course.meta?.assignment_defaults ?? {}) as Record<string, unknown>).late_window_days ?? 10);

  const baseOf = (k: string): Draft | null => (k === 'new' ? null : readDraft(doc, k));
  const draftOf = (k: string): Draft | null => drafts[k] ?? baseOf(k);
  const dirtyKeys = Object.keys(drafts).filter((k) => k === 'new' || !deepEqual(drafts[k], baseOf(k)));
  const dirty = dirtyKeys.length + Object.keys(removed).length;
  const setDraft = (k: string, d: Draft) => {
    setDrafts({ ...drafts, [k]: d });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const templateUsers = (tpl: string) => {
    const all: Record<string, string> = {};
    for (const [id, e] of Object.entries((doc.assignments ?? {}) as Record<string, { course_source_repo?: string }>)) if (!removed[id]) all[id] = e.course_source_repo ?? '';
    for (const [k, d] of Object.entries(drafts)) if (d.kind === 'assignments') all[k === 'new' ? '#new' : k] = d.template;
    return Object.values(all).filter((t) => t === tpl).length;
  };
  const errorsOf = (d: Draft) => draftErrors(d, { templateUsers });
  const allErrors = dirtyKeys.some((k) => Object.keys(errorsOf(drafts[k])).length);

  const doSave = async () => {
    if (!sf || sf.error) return;
    if (allErrors) {
      setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
      return;
    }
    const y = new YamlText(sf.text);
    let newId: string | null = null;
    for (const k of dirtyKeys) {
      const d = drafts[k];
      if (k === 'new' && (d.kind === 'releases' || d.kind === 'assignments' || d.kind === 'events')) {
        const stem = d.kind === 'releases' ? d.type || 'lecture' : d.kind === 'assignments' ? slugOfTemplate(d.template) : d.title || 'event';
        newId = freshId(doc, d.kind, stem);
        writeDraft(y, { ...d, id: newId }, doc);
      } else writeDraft(y, d, doc);
    }
    for (const [id, b] of Object.entries(removed)) y.delete([b, id]);
    const out = y.toJS();
    if (!validSchedule(out)) {
      setSave({ kind: 'bad', text: invalidText('the schedule', validSchedule) });
      return;
    }
    const what = dirty === 1 && dirtyKeys.length === 1 ? (dirtyKeys[0] === 'new' ? `add ${newId}` : `edit ${dirtyKeys[0]}`) : dirtyKeys.length === 0 ? `remove ${Object.keys(removed).join(', ')}` : `${dirty} changes`;
    const ok = await runSave({ owner: p.cohort.org, repo: 'classroom-config', path: 'schedule.yml' }, y.text, sf.sha, { message: `schedule: ${what}, from the Instructor Console`, statusRepo: [p.cohort.org, 'classroom-config'] });
    if (ok) {
      setDrafts({});
      setRemoved({});
      if (newId && typeof location !== 'undefined') location.hash = `#schedule-${newId}`;
    }
  };

  const counts: Record<Block, number> = { releases: 0, assignments: 0, events: 0 };
  const seen = new Set<string>();
  for (const r of rows) if (r.entry !== 'term' && r.entry !== 'archive' && !seen.has(`${r.block}:${r.entry}`)) { seen.add(`${r.block}:${r.entry}`); counts[r.block]++; }
  const skipped = (status.releases ?? []).filter((r) => r.state === 'will_be_skipped').length;
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  let todayDone = false;
  const items = [];
  for (const r of rows) {
    if (!todayDone && (!r.when || sortKey(r.when, tz) >= nowKey)) {
      todayDone = true;
      items.push(<li class="today-line">Today, {fmtDay(new Date(now).toISOString(), tz, year)}</li>);
    }
    if (!filters[r.block]) continue;
    const rel = r.block === 'releases' ? (status.releases ?? []).find((x) => x.id === r.entry) : undefined;
    const gone = !!removed[r.entry];
    const ref = rel ? releaseRef(rel, status.releases ?? [], tz, year) : null;
    const st = gone ? (
      <><span>Removed.</span><button class="textlink" type="button" style="min-height:0;padding:0" onClick={() => { const n = { ...removed }; delete n[r.entry]; setRemoved(n); }}>Undo</button></>
    ) : r.fault && r.block === 'releases' ? <><span class="st-chip skip">will be skipped</span><span class="st-note">Fix the folder first</span></>
      : rel && !ref ? <><span class="st-note">{NOTHING_TO_RELEASE}</span><a class="textlink" href={`#schedule-${r.entry}`}>Edit</a></>
      : ref && rel?.state === 'planned' ? <><span class="st-chip">planned</span><OpOpen def={releaseEarly(scope, ref)} cls="btn small" label="Release early" /></>
      : <span class="st-chip">{r.state}</span>;
    items.push(
      <li class={`trow ${TYPE_CLASS[r.type] ?? 'evt'}${r.fault && r.block === 'releases' ? ' fault' : ''}${key === r.entry ? ' current' : ''}${gone ? ' removed' : ''}`} data-entry={r.entry}>
        <span class="k">{TYPE_LABEL[r.type] ?? r.type}</span>
        <span class="d">{r.when ? fmtDay(r.when, tz, year) : 'TBC'}{r.when && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}{r.tbc ? ' (TBC)' : ''}</span> : r.tbc && r.when ? <span>(TBC)</span> : null}</span>
        <span class="ttl"><a href={`#schedule-${r.entry}`}><b>{r.ident}</b>: {r.name}</a></span>
        <Details r={r} />
        <span class="st">{st}</span>
      </li>,
    );
  }

  let sheet = null;
  if (key && sf) {
    const d = key === 'new' ? drafts.new ?? null : draftOf(key);
    const close = <a class="x" href="#schedule" aria-label="Close entry" style="text-decoration:none;display:grid;place-items:center">&times;</a>;
    if (key === 'new' && !d) {
      sheet = (
        <div class="entry" role="dialog" aria-labelledby="entry-title">
          <div class="entry-head"><div><div class="eyebrow">New entry</div><h2 id="entry-title">What kind of entry?</h2></div>{close}</div>
          <div class="entry-body">
            <div class="type-pick">{NEW_TYPES.map(([t, label, cls]) => <button type="button" class={`trow ${cls}`} style="display:block;min-height:44px" onClick={() => setDraft('new', blankDraft(t, { repo: repos[0] ?? '' }))}>{label}</button>)}</div>
            <p class="footnote">A hand out is an assignment entry: assignment template, hand out, due and late work together.</p>
          </div>
        </div>
      );
    } else if (d) {
      const errors = key in drafts ? errorsOf(d) : {};
      const set = <T,>(patch: Partial<T>) => setDraft(key, { ...(d as object), ...patch } as unknown as Draft);
      const rel = d.kind === 'releases' && key !== 'new' ? (status.releases ?? []).find((x) => x.id === key) : undefined;
      const ref = rel ? releaseRef(rel, status.releases ?? [], tz, year) : null;
      const ident = identOf(d, current, doc);
      const title = d.kind === 'term' ? <>Term dates</> : d.kind === 'archive' ? <><b>Archive</b>: {d.title || 'Cohort archived'}</> : <><b>{ident}</b>: {d.title || (current?.name ?? 'Untitled')}</>;
      const eyebrow = d.kind === 'term' ? 'Term' : d.kind === 'archive' ? 'Archive' : d.kind === 'assignments' ? 'Assignment entry' : `${TYPE_LABEL[d.kind === 'releases' ? d.type || 'lecture' : d.type] ?? ''}${d.date ? `, ${fmtDay(d.date, tz, year)}${d.time ? ` ${d.time}` : ''}` : ''}`;
      const probs = (status.problems ?? []).filter((x) => x.fix?.entry === key && x.fix.path === 'schedule.yml');
      const two = d.kind === 'releases' || d.kind === 'assignments';
      const siteTitle = d.kind === 'term' ? '' : d.title || current?.name || '';
      sheet = (
        <div class="entry" role="dialog" aria-labelledby="entry-title">
          <div class="entry-head">
            <div>
              <div class="eyebrow">{eyebrow}</div>
              <h2 id="entry-title">{title}</h2>
              {rel ? <div style="margin-top:6px"><span class={`chip ${rel.state === 'will_be_skipped' ? 'bad' : rel.state === 'released' ? 'ok' : ''}`}>{RELEASE_WORD[rel.state]}</span></div> : null}
            </div>
            {close}
          </div>
          <div class="entry-body">
            {d.kind !== 'term' ? (
              <div class="site-note">
                On the student site this row shows {two ? <><b>{ident}</b> on one line and “{siteTitle}” below it, without the colon.</> : <>only the bold title, <b>{siteTitle || (d.kind === 'archive' ? 'Cohort archived' : '')}</b>.</>}
              </div>
            ) : null}
            {probs.length ? <ProblemCards list={probs} /> : null}
            <div class="form">
              {d.kind !== 'term' && d.kind !== 'archive' ? <div class="field"><span class="label">Identifier</span><div class="ident">{ident}<span>derived, as the student site does</span></div></div> : null}
              {d.kind === 'releases' ? <ReleaseForm p={p} d={d} set={set} errors={errors} repos={repos} />
                : d.kind === 'assignments' ? <AssignmentForm p={p} d={d} set={set} errors={errors} templates={templates} lateDays={lateDays} />
                : d.kind === 'events' ? <EventForm d={d} set={set} errors={errors} />
                : d.kind === 'term' ? <TermForm d={d} set={set} errors={errors} />
                : <ArchiveForm d={d} set={set} errors={errors} />}
            </div>
          </div>
          <div class="entry-foot">
            {rel ? (
              !ref ? <div class="savebar"><span class="st-note">{NOTHING_TO_RELEASE}. Add a deploy above.</span></div>
              : rel.state === 'planned' ? <div class="savebar"><span class="footnote">Goes out at its time without you.</span><OpOpen def={releaseEarly(scope, ref)} cls="btn small outline" label="Release early…" /></div>
              : rel.state === 'will_be_skipped' ? <div class="savebar"><span class="st-note">Fix the folder first; it cannot be released until it exists.</span></div>
              : rel.state === 'released' ? <div class="savebar"><span class="footnote">Released.</span><a class="btn small quiet" href={`#release-${rel.id}`}>Release again…</a></div>
              : null
            ) : null}
            <SaveLine state={save} />
            <div class="savebar">
              <button class="btn" type="button" disabled={save.kind === 'busy' || !dirty} onClick={() => void doSave()}>Save</button>
              <EditFile org={p.cohort.org} repo="classroom-config" path="schedule.yml" line={key !== 'new' && key !== 'term' ? lineOf(sf.text, key, key === 'archive' ? 0 : 2) : undefined} />
              {key !== 'new' && key !== 'term' && key !== 'archive' && blockOf(doc, key) && !removed[key] ? (
                <button class="btn small quiet" type="button" style="margin-left:auto" onClick={() => { setRemoved({ ...removed, [key]: blockOf(doc, key)! }); if (typeof location !== 'undefined') location.hash = '#schedule'; }}>Remove</button>
              ) : null}
            </div>
          </div>
        </div>
      );
    } else {
      sheet = <div class="entry"><div class="entry-head"><div><h2 id="entry-title">Not in the schedule</h2></div>{close}</div><div class="entry-body"><p>There is no entry called {key} in schedule.yml.</p></div></div>;
    }
  }

  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Schedule')} />
      <div class="page-head">
        <div>
          <h1>Schedule</h1>
          <p class="lede">{counts.releases + counts.assignments + counts.events} entries. {skipped ? `${skipped === 1 ? 'One release' : `${skipped} releases`} will be skipped as it stands.` : 'Every release has its folder.'}</p>
        </div>
        <div class="actions"><CheckNow p={p} label="Check" /><a class="btn outline" href="#schedule-new">Add entry</a></div>
      </div>
      <Help title="How the schedule works" doc="07-schedule-releases.md">
        <p>The schedule drives everything automatic: releases, hand outs, collection, the student site’s calendar. Dates are in the cohort’s timezone. The Details column shows exactly what students see.</p>
      </Help>
      <p class="site-note" style="margin-bottom:12px">
        Every row here reads <b>Identifier</b>: Name. The student site shows the same two parts on two lines, the bold identifier then the name, with no colon; exams and events show only the bold title. Whether the site should switch to one line is a theme decision.
      </p>
      <div class="term-meta">
        {sched?.start && sched?.end ? <span>Term <b>{fmtDay(sched.start, tz, year)} to {fmtDay(sched.end, tz)}</b></span> : null}
        <span>Timezone <b>{sched?.timezone ?? tz}</b></span>
        <span>Archive <b>{status.cohort?.archive_date ? fmtDay(status.cohort.archive_date, tz, year) : 'never'}</b></span>
        <a class="textlink" href="#schedule-term">Edit term dates</a>
        <a class="textlink" href="#schedule-archive">Archive settings</a>
      </div>
      {file === 'loading' ? <p class="footnote" style="margin-bottom:12px">Reading schedule.yml…</p> : null}
      {file === null ? <p class="footnote" style="margin-bottom:12px">There is no schedule.yml to edit.</p> : null}
      {sf?.error ? <p class="check-line bad" style="margin-bottom:12px"><span>schedule.yml does not parse ({sf.error}); fix it with Edit the file before editing here.</span></p> : null}
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
              <div class="savebar"><span class="footnote">Release a folder that is not in the schedule, for a one-off or a correction.</span><OpOpen def={releaseAdhoc(scope, repos)} cls="btn small outline" label="Release something unscheduled…" /></div>
              <div class="savebar"><span class="footnote">See what automation’s next scheduled release run would do.</span><OpOpen def={scheduledPreview(scope)} cls="btn small outline" label="Preview scheduled releases" /></div>
            </div>
          </details>
          <div style="margin-top:14px"><Lives org={p.cohort.org} repo="classroom-config" path="schedule.yml" /></div>
        </div>
        {sheet}
      </div>
      {!key ? <SaveLine state={save} /> : null}
      <UnsavedBar count={dirty} busy={save.kind === 'busy'} onDiscard={() => { setDrafts({}); setRemoved({}); setSave({ kind: 'idle' }); }} onSave={() => void doSave()} file={{ org: p.cohort.org, repo: 'classroom-config', path: 'schedule.yml' }} />
    </>
  );
}

export function ScheduleScreen(p: CohortProps) {
  return <WithStatus props={p} title="Schedule" crumbs={cohortCrumbs(p, 'Schedule')}>{(r) => <View {...r} />}</WithStatus>;
}

// --------------------------------------------------------------------------- S11

function ReleaseDetail(p: ReadyProps & { rel: Release }) {
  const { status, now, rel } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const all = status.releases ?? [];
  const ident = releaseIdent(rel, all);
  const st = rel.state;
  const scope = cohortScope(p);
  const ref = releaseRef(rel, all, tz, year);
  if (!ref) {
    return (
      <>
        <Crumbs items={cohortCrumbs(p, ident, [{ t: 'Schedule', href: '#schedule' }])} />
        <div class="page-head">
          <div>
            <h1><b>{ident}</b>: {rel.title}</h1>
            <p class="lede">{NOTHING_TO_RELEASE}.</p>
          </div>
          <div class="actions"><a class="btn" href={`#schedule-${rel.id}`}>Edit entry</a></div>
        </div>
      </>
    );
  }
  const problems = (status.problems ?? []).filter((x) => x.fix?.entry === rel.id);
  const last = (status.operations ?? []).find((o) => o.op.startsWith('release.') && o.summary.includes(`${ident}:`));
  const dest = rel.dest?.repo || 'materials', destPath = rel.dest?.path || ref.source.path;
  return (
    <>
      <Crumbs items={cohortCrumbs(p, ident, [{ t: 'Schedule', href: '#schedule' }])} />
      <div class="page-head">
        <div>
          <h1><b>{ident}</b>: {rel.title}</h1>
          <p class="lede"><span class={`chip ${st === 'will_be_skipped' ? 'bad' : st === 'released' ? 'ok' : ''}`}>{RELEASE_WORD[st]}</span>{fmtWhen(rel.when, tz, year)}</p>
        </div>
        <div class="actions"><a class="btn quiet" href={`#schedule-${rel.id}`}>Edit entry</a></div>
      </div>
      <Help title="Releases" doc="08-release-materials-to-cohort.md">
        <p>
          {st === 'released'
            ? 'Edits students should see: push to the cohort copy, or release again after fixing the course copy. Edits future terms should keep: keep cohort edits for future terms.'
            : st === 'will_be_skipped' ? 'Automation will skip this until the folder exists.'
            : st === 'late' ? 'Reason codes tell you whether the source, the schedule or the scheduler was at fault.'
            : 'Nothing to do; it goes out at the scheduled time. You can release it early.'}
        </p>
      </Help>
      <div class="grid-2">
        <section class="panel section">
          <h2>From and to</h2>
          <dl class="kv">
            <dt>From</dt><dd><a href={ghUrl(p.course.org, ref.source.repo, ref.source.path, 'main').replace('/blob/', '/tree/')} target="_blank" rel="noopener">{p.course.org}/{ref.source.repo}/{ref.source.path}</a></dd>
            <dt>To</dt><dd><a href={ghUrl(p.cohort.org, dest, destPath, 'main').replace('/blob/', '/tree/')} target="_blank" rel="noopener">{p.cohort.org}/{dest}/{destPath}</a></dd>
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
            : st === 'released' ? <><OpButtons def={releaseAgain(scope, ref)} label="Release again" /><OpOpen def={keepFuture(scope)} cls="btn quiet" label="Keep cohort edits for future terms" /></>
            : st === 'late' ? <OpButtons def={releaseNow(scope, ref)} label="Release now" />
            : <a class="btn" href={`#schedule-${rel.id}`}>Fix the folder</a>}
        </div>
        <div class="savebar"><span class="footnote">After changing the course copy, the student site may need an update.</span><a class="btn small quiet" href="#site">Update site on the Site page</a></div>
      </section>
    </>
  );
}

export function ReleaseScreen(p: CohortProps) {
  return (
    <WithStatus props={p} title="Release" crumbs={cohortCrumbs(p, 'Release', [{ t: 'Schedule', href: '#schedule' }])}>
      {(r) => {
        const rel = (r.status.releases ?? []).find((x) => x.id === p.entry);
        return rel ? <ReleaseDetail {...r} rel={rel} /> : <NotFound what={`No release called ${p.entry} in the schedule.`} back="#schedule" />;
      }}
    </WithStatus>
  );
}
