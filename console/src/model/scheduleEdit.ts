// schedule.yml entries as the entry sheet edits them (design/inputs.md "Schedule editor",
// revision brief v3 section 4), and back. Writing merges into the entry as it is in the
// file, so keys the sheet does not show are kept, and a default is written only when the
// file already spelt it.

import { obj, type YamlText } from '../edit/yamlText';
import { kebab } from './format';
import { ARCHIVE_GRACE_DAYS, DEFAULT_DEST_REPO } from './policy';

export type Block = 'releases' | 'assignments' | 'events';

export interface DeployDraft {
  repo: string;
  folder: string;
  dest: string;
  path: string;
  diff: boolean;
  atDate: string;
  atTime: string;
}

export interface ReleaseDraft {
  kind: 'releases';
  id: string;
  type: string;
  title: string;
  date: string;
  time: string;
  details: string;
  show: boolean;
  tbc: boolean;
  deploys: DeployDraft[];
}

export interface AssignmentDraft {
  kind: 'assignments';
  id: string;
  template: string;
  title: string;
  manual: boolean;
  handoutDate: string;
  handoutTime: string;
  dueDate: string;
  dueTime: string;
  solutionOn: boolean;
  solutionDate: string;
  solutionTime: string;
  details: string;
  show: boolean;
  tbc: boolean;
}

export interface EventDraft {
  kind: 'events';
  id: string;
  type: string; // exam | special_event
  title: string;
  date: string;
  time: string;
  details: string;
  show: boolean;
  tbc: boolean;
}

export interface SemesterDraft {
  kind: 'semester';
  start: string;
  end: string;
  tz: string;
}

export interface ArchiveDraft {
  kind: 'archive';
  on: boolean;
  date: string;
  title: string;
  details: string;
  show: boolean;
  tbc: boolean;
  /** Days after the semester ends that the default archive date falls; '' while the box is empty. */
  graceDays: number | '';
}

export { ARCHIVE_GRACE_DAYS };

export type Draft = ReleaseDraft | AssignmentDraft | EventDraft | SemesterDraft | ArchiveDraft;

type Raw = Record<string, unknown>;

const s = (v: unknown): string => (v == null ? '' : v instanceof Date ? v.toISOString().slice(0, 16) : String(v));

/** "2026-10-08T10:00" -> ["2026-10-08", "10:00"]; a bare date has no time. */
export function splitWhen(v: unknown): [string, string] {
  const t = s(v);
  if (!t || t.toLowerCase() === 'tbc') return ['', ''];
  const m = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(t);
  return m ? [m[1], m[2] ?? ''] : [t, ''];
}

export function joinWhen(date: string, time: string): string | undefined {
  if (!date) return undefined;
  return time ? `${date}T${time}` : date;
}

/**
 * The moment to write for a date and time the sheet shows: the file's own value when it
 * still says that - so an offset (`+01:00`), seconds or a space the instructor wrote are
 * kept, not rewritten to the sheet's `T10:00` - else the sheet's.
 */
export function whenOf(raw: unknown, date: string, time: string): unknown {
  const t = s(raw);
  if (t && t.toLowerCase() !== 'tbc') {
    const [d, h] = splitWhen(raw);
    if (d === date && h === time) return raw;
  }
  return joinWhen(date, time);
}

/** Which block an entry id is in. */
export function blockOf(doc: Raw, id: string): Block | null {
  for (const b of ['releases', 'assignments', 'events'] as Block[]) if (id in obj(doc[b])) return b;
  return null;
}

/** The sheet's key for an entry: "semester", "archive", or the id (ids are unique across blocks in practice). */
export function readDraft(doc: Raw, key: string): Draft | null {
  if (key === 'semester') return { kind: 'semester', start: s(doc.semester_start), end: s(doc.semester_end), tz: s(doc.timezone) };
  if (key === 'archive') {
    const a = doc.archive === undefined ? null : obj(doc.archive);
    return { kind: 'archive', on: a !== null, date: splitWhen(a?.event_datetime)[0], title: s(a?.title), details: s(a?.details), show: a?.show_on_site !== false, tbc: a?.tbc === true, graceDays: typeof a?.grace_days === 'number' ? a.grace_days : ARCHIVE_GRACE_DAYS };
  }
  const b = blockOf(doc, key);
  if (!b) return null;
  const e = obj(obj(doc[b])[key]);
  const common = { id: key, title: s(e.title), details: s(e.details), show: e.show_on_site !== false, tbc: e.tbc === true };
  if (b === 'releases') {
    const [date, time] = splitWhen(e.event_datetime);
    const deploys = (Array.isArray(e.deploy) ? e.deploy : []).map((d: unknown) => {
      const x = obj(d);
      const [atDate, atTime] = splitWhen(x.deploy_datetime);
      return { repo: s(x.course_source_repo), folder: s(x.course_source_path), dest: s(x.semester_dest_repo), path: s(x.semester_dest_path), diff: !!x.deploy_datetime, atDate, atTime };
    });
    return { kind: 'releases', ...common, type: s(e.kind), date, time, deploys };
  }
  if (b === 'assignments') {
    const [handoutDate, handoutTime] = splitWhen(e.handout_datetime);
    const [dueDate, dueTime] = splitWhen(e.due_datetime);
    const [solutionDate, solutionTime] = splitWhen(e.solution_datetime);
    return {
      kind: 'assignments', ...common, template: s(e.course_source_repo), manual: !e.handout_datetime, handoutDate, handoutTime, dueDate, dueTime,
      solutionOn: !!e.solution_datetime, solutionDate, solutionTime,
    };
  }
  const [date, time] = splitWhen(e.event_datetime);
  return { kind: 'events', ...common, type: s(e.kind) || 'special_event', date, time, tbc: common.tbc || s(e.event_datetime).toLowerCase() === 'tbc' };
}

/** A value to write: `v`, unless it is the default and the file did not already spell it. */
function keep<T>(raw: unknown, v: T, dflt: T): T | undefined {
  if (v !== dflt) return v;
  return raw === dflt ? v : undefined;
}
const text = (v: string) => (v.trim() ? v : undefined);

function display(raw: Raw, d: { title: string; details: string; show: boolean; tbc: boolean }): Raw {
  return { title: text(d.title), details: text(d.details), show_on_site: keep(raw.show_on_site, d.show, true), tbc: keep(raw.tbc, d.tbc, false) };
}

/** The entry as it goes into the file, merged over what the file has. */
export function entryValue(d: ReleaseDraft | AssignmentDraft | EventDraft, rawEntry: unknown): Raw {
  const raw = obj(rawEntry);
  if (d.kind === 'releases') {
    const rawDeploys = Array.isArray(raw.deploy) ? raw.deploy : [];
    return {
      ...raw,
      event_datetime: whenOf(raw.event_datetime, d.date, d.time),
      kind: text(d.type),
      ...display(raw, d),
      deploy: d.deploys.length
        ? d.deploys.map((dp, i) => {
            const r = obj(rawDeploys[i]);
            return {
              ...r, course_source_repo: dp.repo, course_source_path: dp.folder,
              semester_dest_repo: dp.dest && (dp.dest !== DEFAULT_DEST_REPO || r.semester_dest_repo) ? dp.dest : undefined,
              semester_dest_path: text(dp.path), deploy_datetime: dp.diff ? whenOf(r.deploy_datetime, dp.atDate, dp.atTime) : undefined,
            };
          })
        : undefined,
    };
  }
  if (d.kind === 'assignments') {
    return {
      // Timings only (decision 0009): the title is the template's, the repo name and the
      // late cutoff are assignments.yml's. None is written here, and a retired key the
      // file still carries is left exactly as it is: the engine faults it and the
      // migration moves it.
      ...raw, ...display(raw, d), title: raw.title, course_source_repo: d.template,
      handout_datetime: d.manual ? undefined : whenOf(raw.handout_datetime, d.handoutDate, d.handoutTime),
      due_datetime: whenOf(raw.due_datetime, d.dueDate, d.dueTime),
      solution_datetime: d.solutionOn && !d.manual ? whenOf(raw.solution_datetime, d.solutionDate, d.solutionTime) : undefined,
    };
  }
  return {
    ...raw, kind: keep(raw.kind, d.type, 'special_event'), ...display(raw, d),
    event_datetime: whenOf(raw.event_datetime, d.date, d.time) ?? 'tbc', tbc: d.date ? keep(raw.tbc, d.tbc, false) : undefined,
  };
}

/** Write one draft into the file. */
export function writeDraft(y: YamlText, d: Draft, doc: Raw): void {
  if (d.kind === 'semester') {
    y.assign(['semester_start'], text(d.start));
    y.assign(['semester_end'], text(d.end));
    y.assign(['timezone'], text(d.tz));
    return;
  }
  if (d.kind === 'archive') {
    if (!d.on) {
      y.delete(['archive']);
      return;
    }
    const raw = obj(doc.archive);
    y.assign(['archive'], {
      ...raw,
      event_datetime: d.date ? whenOf(raw.event_datetime, d.date, splitWhen(raw.event_datetime)[1]) : 'event_datetime' in raw ? null : undefined,
      title: text(d.title),
      details: text(d.details),
      show_on_site: keep(raw.show_on_site, d.show, true),
      tbc: keep(raw.tbc, d.tbc, false),
      grace_days: keep(raw.grace_days, d.graceDays === '' ? ARCHIVE_GRACE_DAYS : d.graceDays, ARCHIVE_GRACE_DAYS),
    });
    return;
  }
  y.assign([d.kind, d.id], entryValue(d, obj(obj(doc[d.kind])[d.id])));
}

/** A fresh id in `block`: `lecture-6`, `lab-4`, `assignment-2`, `midterm-exam`. */
export function freshId(doc: Raw, block: Block, stem: string): string {
  const taken = new Set([...Object.keys(obj(doc.releases)), ...Object.keys(obj(doc.assignments)), ...Object.keys(obj(doc.events))]);
  const base = kebab(stem) || block.slice(0, -1);
  if (/-\d+$/.test(base) || block !== 'releases') {
    let id = base, n = 2;
    while (taken.has(id)) id = `${base}-${n++}`;
    return id;
  }
  let n = 1;
  while (taken.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}

/** The assignment key a template gives: `assignment-2-f2026` -> `assignment-2`. */
export function slugOfTemplate(template: string): string {
  return template.replace(/-[fswu]\d{4}$/, '');
}

export function blankDraft(type: string, defaults: { repo: string }): ReleaseDraft | AssignmentDraft | EventDraft {
  const common = { id: '', title: '', details: '', show: true, tbc: false };
  if (type === 'handout')
    return { kind: 'assignments', ...common, template: '', manual: false, handoutDate: '', handoutTime: '10:00', dueDate: '', dueTime: '23:59', solutionOn: false, solutionDate: '', solutionTime: '' };
  if (type === 'exam' || type === 'special_event') return { kind: 'events', ...common, type, date: '', time: '' };
  return { kind: 'releases', ...common, type, date: '', time: '10:00', deploys: [{ repo: defaults.repo, folder: '', dest: '', path: '', diff: false, atDate: '', atTime: '' }] };
}

/** What is wrong with a draft, by field; empty when it can be saved. */
export function draftErrors(d: Draft, _others?: { templateUsers: (template: string) => number }): Record<string, string> {
  const e: Record<string, string> = {};
  if (d.kind === 'releases') {
    if (!d.date) e.date = 'When is needed.';
    d.deploys.forEach((dp, i) => {
      if (!dp.repo) e[`deploy${i}.repo`] = 'Choose the repo.';
      if (!dp.folder) e[`deploy${i}.folder`] = 'Name the folder or file.';
      if (dp.diff && !dp.atDate) e[`deploy${i}.at`] = 'Give its own time, or untick.';
    });
  } else if (d.kind === 'assignments') {
    if (!d.template) e.template = 'Choose the template.';
    if (!d.dueDate) e.due = 'Due is needed.';
    if (!d.manual && !d.handoutDate) e.handout = 'Give the hand out date, or choose to hand out manually.';
    const h = joinWhen(d.handoutDate, d.handoutTime) ?? '';
    const due = joinWhen(d.dueDate, d.dueTime) ?? '';
    if (h && due && due < h) e.due = 'Due must come after the hand out.';
    const sol = joinWhen(d.solutionDate, d.solutionTime);
    if (d.solutionOn && !d.manual && !sol) e.solution = 'Give the date the solution is shown.';
    if (d.solutionOn && sol && h && sol <= h) e.solution = 'Must be after the hand out.';
  } else if (d.kind === 'events') {
    if (!d.title.trim()) e.title = 'A title is needed; the student site shows only this.';
  } else if (d.kind === 'semester') {
    if (d.start && d.end && d.end <= d.start) e.end = 'The semester must end after it starts.';
  } else if (d.kind === 'archive') {
    if (d.on && d.graceDays !== '' && !(Number.isInteger(d.graceDays) && d.graceDays >= 0)) e.graceDays = 'A whole number of days, 0 or more.';
  }
  return e;
}
