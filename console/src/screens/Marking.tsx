// The assignment hub's Marks tab (S12, the grading sheet as a grid) and Teams tab (S9, the
// assignment's `team:<n>` column of students.csv for a team assignment, decision 0039 rule 4).

import type { ComponentChildren } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { readTable, writeTable } from '../edit/csv';
import { CONFLICT, saveText, useSave, type SaveState } from '../edit/save';
import { YamlText, blank, isObj, type Path } from '../edit/yamlText';
import { Invalid } from '../forms/Form';
import { assignmentTitle, fmtDay, fmtWhen, plural, str } from '../model/format';
import {
  HOLD_WORDS, NOTES_KEY, SNIPPETS_KEY, cellValue, effectivePenalty, finalGrade, holdReasons, lateRule, markerLines, num, overrideText, overrideValue, penaltyFigure, questionFile,
  questionPoints, readSheet, readSnippets, round, rowsChanged, scoreTotal, spread, studentMarks, type HoldReason, type HoldUnit, type Penalty, type Person, type Unit,
} from '../model/marks';
import { SOURCE_WORD, scheduleFile, valueWord } from '../model/settings';
import type { Env } from '../env';
import type { ReadyProps } from './types';
import { sheetName, withRunSetting } from './RunSettings';
import { penaltyRate } from '../model/policy';
import { marksDraft, marksWidths, saveMarksDraft, saveMarksWidths, type MarksDraft } from '../model/prefs';
import { parseRoster, sameHandle, teamColumn, type RosterRow } from '../model/people';
import type { Assignment } from '../model/types';
import { returnMarks, teamsWindow, type AsgRef, type LateReturn, type PerQuestion } from '../ops/defs';
import { OpButtons, OpOpen, VerbHint } from '../ops/Panel';
import { assignmentHref, tabHref } from '../router';
import { CheckLine, Loading } from '../ui/bits';
import { Modal } from '../ui/Modal';
import { Hint } from '../ui/Hint';
import { SaveBar } from '../ui/edit';
import { Check } from '../ui/icons';
import type { TabProps } from './Assignments';
import { cohortScope, todayOf, tzOf, useGradingConfig, yearOf } from './common';
import { CONFIG_REPO, NAMES, ROSTER_PATH, STUDENTS_FILE } from '../model/names';
import { MarksDrawer, type DrawerBox, type DrawerLink } from './MarksDrawer';
import { formatsList } from '../tiers/grading';

/** The late cutoff as the Return marks dialog names it, while it has not passed; null after it. */
export function lateUntil(a: Assignment, now: number, tz: string, year: number): string | null {
  const at = a.grading_cutoff_datetime;
  return at && Date.parse(at) > now ? fmtWhen(at, tz, year) : null;
}

/** The late penalty the marks grid applies: the assignment's effective `late_penalty_per_day` as status states it (`status_json.run_settings`). */
function penaltyOf(a: Assignment): number | null {
  return penaltyRate(a.settings?.late_penalty_per_day?.value);
}

/** The assignment's late rule in the one wording the lede, the Penalty `?` and Return marks share. */
export const lateRuleOf = (a: Assignment) => lateRule(penaltyOf(a), a.settings?.late_window_days?.value);

/** What Return marks says while late work can still arrive: the deadline, the late cutoff and the late rule; null once the cutoff has passed. */
export function lateReturn(a: Assignment, now: number, tz: string, year: number): LateReturn | null {
  const until = lateUntil(a, now, tz, year);
  return until ? { due: fmtWhen(a.due, tz, year), until, rule: lateRuleOf(a) } : null;
}

/**
 * "Include marks per question" for Return marks: the assignment's effective `marks_per_question`
 * as status.json states it (on, from the institution, in a status written before the setting), and
 * how a change is saved: inline on the assignment's schedule entry, before the run goes.
 */
export function perQuestionOf(p: Pick<ReadyProps, 'files' | 'cohort'>, a: Assignment, env: Env | null): PerQuestion {
  const s = a.settings?.marks_per_question;
  return {
    value: s ? s.value !== false : true,
    source: s?.source ?? 'institution',
    save: async (on) => {
      if (!env) throw new Error('Sign in to save.');
      const f = scheduleFile(p.files, p.cohort.org);
      if (f === 'loading' || f === null) throw new Error('schedule.yml could not be read, so the setting was not saved and nothing was returned');
      const text = withRunSetting(f, a.slug, 'marks_per_question', on);
      if (text === null) return;
      let said = '';
      const ok = await saveText(env, { owner: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }, text, f.sha,
        { message: `schedule: ${a.slug} marks_per_question ${on}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] },
        (st) => { if (st.kind === 'bad') said = st.text; });
      if (!ok) throw new Error(`Include marks per question was not saved, so nothing was returned: ${said.replace(/^Not saved: /, '')}`);
    },
  };
}

function asgRef(a: Assignment, group: boolean): AsgRef {
  return { slug: a.slug, title: assignmentTitle(a), template: a.template, units: a.units, group, when: 'Marking' };
}

// --------------------------------------------------------------------------- marks

const key = (path: Path) => JSON.stringify(path);

/** " (out of 5)" for a question with a maximum, nothing for one without (a `{file}`-only entry). */
function outOf(m: unknown): string {
  const pts = questionPoints(m);
  return pts == null || pts === '' ? '' : ` (out of ${String(pts)})`;
}

type Filter = 'all' | 'unmarked' | 'late' | 'held';
const FILTER_WORD: Record<Filter, string> = { all: 'All', unmarked: 'Unmarked', late: 'Late', held: 'Held' };

/** The issue a Return marks preview writes in semester-config (grades.PREVIEW_TITLE). */
export const PREVIEW_TITLE = 'Distribute grades preview';

/**
 * Return marks asked for from another tab (the Overview): the Marks tab opens the panel once its
 * sheet is read, so the pop-up always carries the sheet's figures. One path, whichever page asks.
 */
let pendingReturn: { slug: string; start: 'preview' | 'run' } | null = null;
export function requestReturnMarks(slug: string, start: 'preview' | 'run'): void {
  pendingReturn = { slug, start };
}

/** A row of the grid the side panel can show: a student or team, or one member of a team. */
interface RowRef {
  unit: string;
  member: string | null;
}
/** The side panel: its row, the box to put the cursor in, and whether to take focus (not when it follows focus in the grid). */
type DrawerAt = RowRef & { focus: string | null; grab: boolean };

/** The units an edit set touches (its cells under `teams:` or `submissions:`), by key. */
function unitsEdited(edits: Record<string, unknown>, container: string): Set<string> {
  return new Set(Object.keys(edits).map((k) => JSON.parse(k) as Path).filter((pth) => pth[0] === container && pth.length > 1).map((pth) => String(pth[1])));
}

const on = (v: unknown) => v === true || v === 'true';
const off = (v: unknown) => v === false || v === 'false';

/**
 * What a template's `grading_config.yml` runs at the late cutoff (grades.GradingSpec): its tests
 * when `autograde:` is on, and the completion check when `completion_check:` says so, else
 * when its runnable format (the first of `formats:`) is a notebook. A shared drop box runs
 * neither (grades._cross_check).
 */
export function templateChecks(cfg: Record<string, unknown>): { tests: boolean; completion: boolean } {
  if (cfg.submit_via === 'shared_dropbox_repo') return { tests: false, completion: false };
  const completion = on(cfg.completion_check) ? true : off(cfg.completion_check) ? false : formatsList(cfg.formats)[0] === 'ipynb';
  return { tests: on(cfg.autograde), completion };
}

/** A grid column: its key, its default width (null: Feedback and Private notes share what is left), and its name. */
export interface MarksCol {
  key: string;
  def: number | null;
  label: string;
}
/** Below this window width the submission columns start folded. */
export const WIDE = 1400;
/** Where the instructor's fold choice is kept, beside the column widths. */
const FOLD_KEY = '__subFolded';
/** No column is dragged narrower than this. */
export const MIN_COL = 48;
/** Feedback and Private notes never get less than this each (the console beside its side nav at 1280px). */
export const MIN_TEXT_COL = 170;

/** "13 Oct 22:41" for a recorded `submitted` (`2026-10-13T22:41`, as the sheet holds it); anything else as written. */
export function submittedShort(v: unknown): string {
  const t = str(v).trim();
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(t);
  if (!m) return t;
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Number(m[2]) - 1];
  return `${Number(m[3])} ${mon}${m[4] ? ` ${m[4]}:${m[5]}` : ''}`;
}

/** A column header's `?`: small, so the header row does not grow. */
const ColHint = ({ col, children }: { col: string; children: ComponentChildren }) => <Hint small label={`About ${col}`}>{children}</Hint>;

/** A unit as the hold rules read it. */
const holdUnit = (u: Unit, score: unknown, latePenalty: unknown, adj: (pr: Person) => unknown): HoldUnit => ({
  score, daysLate: u.info?.days_late, latePenalty, people: u.people.map((pr) => ({ handle: pr.handle, adjustment: adj(pr) })),
});

/** Whether a score cell holds no mark at all (grades.is_blank: a map of blanks too). */
const noMark = (score: unknown) => (isObj(score) ? Object.values(score).every(blank) : blank(score));

/**
 * Who a preview would email, from the preview issue's body (grades._preview_body): each handle with
 * why, and this assignment's mark change where it has one ("ana-lopez (mark 36 -> 40)").
 */
export function previewEmails(body: string, slug: string): string[] | null {
  const section = (title: string) => {
    const at = body.indexOf(`### ${title}`);
    if (at < 0) return [];
    const rest = body.slice(at).split('\n').slice(1);
    const end = rest.findIndex((l) => l.startsWith('### '));
    return (end < 0 ? rest : rest.slice(0, end)).filter((l) => l.startsWith('- '));
  };
  const marks: Record<string, string> = {};
  for (const l of section('Grades that would change')) {
    const m = /^- \*\*(.+?)\*\* · `([^`]+)`.* · (\S+) \((?:was (.+)|new)\)$/.exec(l);
    if (m && m[1] === slug) marks[m[2]] = m[4] !== undefined ? `mark ${m[4]} -> ${m[3]}` : `new mark ${m[3]}`;
  }
  if (!body.includes('### Students who would be emailed')) return null;
  return section('Students who would be emailed').flatMap((l) => {
    const m = /^- `([^`]+)`.* - (their first grades email|a grade changed|feedback changed)\./.exec(l);
    if (!m) return [];
    const why = marks[m[1]] ?? (m[2] === 'feedback changed' ? 'feedback changed' : m[2] === 'a grade changed' ? 'mark changed' : 'first marks email');
    return [`${m[1]} (${why})`];
  });
}

/** Under a Return marks preview: who would be emailed, read from the private preview issue. */
function PreviewEmails({ org, slug }: { org: string; slug: string }) {
  const env = useEnv();
  const [said, setSaid] = useState<string | null>('Reading who would be emailed…');
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const issue = (await env!.client.listIssues(org, CONFIG_REPO, 'state=open')).find((i) => i.title === PREVIEW_TITLE);
        const who = issue?.body ? previewEmails(issue.body, slug) : null;
        if (live) setSaid(who === null ? null : `Email to: ${who.length ? who.join(', ') : 'nobody'}.`);
      } catch {
        if (live) setSaid('Could not read who would be emailed; the preview issue in semester-config lists them.');
      }
    })();
    return () => {
      live = false;
    };
  }, [org, slug]);
  return said ? <p class="footnote preview-emails">{said}</p> : null;
}

export function MarksTab(p: TabProps) {
  const { a, status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const env = useEnv();
  const name = sheetName(p, a.slug);
  const path = `grading_sheets/${name}.yml`;
  const org = p.cohort.org;
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  // A draft kept from an earlier visit is offered, never replayed silently.
  const [pending, setPending] = useState<MarksDraft | null>(() => marksDraft(org, path));
  // Folds, by unit: `m:<unit>` shows or hides a team's members, `q:<unit>` its feedback per question.
  const [folds, setFolds] = useState<Record<string, boolean>>({});
  // The late-penalty override box as typed, by unit: the sheet gets `15%`, the box keeps `15.`.
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<Filter>('all');
  // The window's width, for the submission columns' default fold.
  const [winW, setWinW] = useState(() => (typeof window === 'undefined' ? WIDE : window.innerWidth));
  useEffect(() => {
    const on = () => setWinW(window.innerWidth);
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  // An individual assignment's Adjustment column: null opens it where the saved sheet has any adjustment.
  const [adjChoice, setAdjChoice] = useState<boolean | null>(null);
  const [full, setFull] = useState(false);
  // The Student view dialog: null closed, else the handle shown ('' before one is chosen).
  const [viewing, setViewing] = useState<string | null>(null);
  // The side panel for one row (feedback, per question, private notes, links, Student view); null closed.
  const [drawer, setDrawer] = useState<DrawerAt | null>(null);
  const drawerOpen = useRef(false);
  drawerOpen.current = drawer !== null;
  // In the panel's Student view on a team row: the member shown.
  const [svPick, setSvPick] = useState<string | null>(null);
  // The "Marked by" filter: null anyone, '' nobody recorded, else a login.
  const [byMarker, setByMarker] = useState<string | null>(null);
  const table = useRef<HTMLTableElement>(null);
  // Column widths the person dragged, kept per sheet in this browser.
  const [widths, setWidths] = useState<Record<string, number>>(() => marksWidths(org, path));
  useEffect(() => saveMarksWidths(org, path, widths), [widths, org, path]);
  // "From the submission" folds to one chip in a narrow window (full screen counts as wide), until the
  // instructor chooses; that choice is kept per sheet with the widths, and Reset widths forgets it.
  const subFolded = widths[FOLD_KEY] !== undefined ? widths[FOLD_KEY] === 1 : !full && winW < WIDE;
  const setSubFolded = (fold: boolean) => setWidths((cur) => ({ ...cur, [FOLD_KEY]: fold ? 1 : 0 }));
  const fullBtn = useRef<HTMLButtonElement>(null);
  const wasFull = useRef(false);
  // The Return marks def as this render built it, for a request from the Overview.
  const returnDef = useRef<ReturnType<typeof returnMarks> | null>(null);
  useEffect(() => {
    if (!pendingReturn || pendingReturn.slug !== a.slug || !returnDef.current || !env) return;
    const { start } = pendingReturn;
    pendingReturn = null;
    env.ops.open(returnDef.current, start);
  });
  const [save, , setSave] = useSave(env);
  const file = p.files.file(org, CONFIG_REPO, path);
  const sha = file.kind === 'ready' ? file.sha : null;
  // Unsaved marks survive a reload: kept in this browser per sheet, with the sha they were made on, until saved.
  useEffect(() => {
    if (!pending) saveMarksDraft(org, path, Object.keys(edits).length ? { sha, at: Date.now(), edits } : null);
  }, [edits, pending, org, path]);
  // Full screen is an overlay over the page: Escape or the icon closes it, and focus goes back to the icon.
  useEffect(() => {
    if (!full) {
      if (wasFull.current) fullBtn.current?.focus();
      wasFull.current = false;
      return;
    }
    wasFull.current = true;
    // With the side panel open, Escape closes the panel first.
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && (drawerOpen.current ? setDrawer(null) : setFull(false));
    document.addEventListener('keydown', esc);
    return () => document.removeEventListener('keydown', esc);
  }, [full]);
  const text = file.kind === 'ready' ? file.text : null;
  const parsed = useMemo(() => {
    if (text === null) return null;
    const y = new YamlText(text);
    if (y.errors.length) return { error: y.errors[0] };
    const doc = y.toJS();
    return { sheet: readSheet(text, doc), snippets: readSnippets(doc) };
  }, [text]);
  const roster = p.files.file(org, CONFIG_REPO, STUDENTS_FILE);
  const sysRoster = p.files.file(org, CONFIG_REPO, ROSTER_PATH);
  const rosterText = roster.kind === 'ready' ? roster.text : null;
  const sysText = sysRoster.kind === 'ready' ? sysRoster.text : null;
  const students = useMemo(() => (rosterText === null ? [] : parseRoster(rosterText, sysText).rows), [rosterText, sysText]);
  /** A person's real name from students.csv (by their handle in the engine's roster), or '' when it has none. */
  const realName = (h: string) => students.find((s) => sameHandle(s.handle, h))?.name ?? '';
  const cfg = useGradingConfig(p, a.template);
  const questions = cfg.questions && typeof cfg.questions === 'object' ? (cfg.questions as Record<string, unknown>) : null;
  const qs = questions ? Object.entries(questions) : [];
  const rate = penaltyOf(a);
  const rule = lateRuleOf(a);
  const max = qs.reduce((n, [, m]) => n + (Number(questionPoints(m)) || 0), 0);
  const group = a.teams !== null && a.teams !== undefined;
  const scope = cohortScope(p);
  const runs = templateChecks(cfg);
  const lede = (
    <p class="lede">{a.marks.filled} of {a.marks.total} marked. {rule ? `Totals and late penalties (${rule}) are worked out for you.` : 'Totals are worked out for you; no late penalty applies.'}</p>
  );
  const title = (
    <h2 class="h1">{assignmentTitle(a)} <Hint doc="10-grade-and-return-assignments.md">You enter {qs.length ? 'points per question' : 'one score'}, feedback students see, an adjustment and private notes that are never shared. {rate !== null ? 'A late unit’s penalty can be waived (switch it off) or overridden with the whole penalty as a percentage. ' : ''}{group ? 'Each member of a team receives the team’s final grade and feedback; a member’s own adjustment moves only their total.' : 'Nothing reaches a student until you return marks.'}</Hint></h2>
  );
  const late = lateReturn(a, now, tz, year);
  // Before the sheet is read, Return marks sits in the page head; with a sheet, in the grid's toolbar.
  const headOnly = (
    <>
      <div class="page-head">
        <div>{title}{lede}</div>
        <div class="actions"><OpButtons def={returnMarks(scope, asgRef(a, group), a.marks.filled, name, late, undefined, undefined, perQuestionOf(p, a, env))} /></div>
      </div>
      {p.tabs}
    </>
  );
  if (file.kind === 'loading') return <>{headOnly}<Loading what="Reading the mark sheet" /></>;
  if (file.kind !== 'ready')
    return <>{headOnly}<section class="panel section stub"><h2>No mark sheet yet</h2><p>{group && !a.teams ? 'No teams yet, so no mark sheet: it appears with the first team.' : 'The mark sheet appears at hand out, with a row for every student or team.'}</p></section></>;
  if (!parsed?.sheet)
    return <>{headOnly}<CheckLine cls="bad">The mark sheet does not parse ({parsed?.error}). Fix it with Edit the file directly.</CheckLine></>;
  const { sheet } = parsed;
  const container = sheet.group ? 'teams' : 'submissions';
  const v = (pth: Path, orig: unknown) => (key(pth) in edits ? edits[key(pth)] : orig);
  const set = (pth: Path, raw: string, typed = false) => {
    setEdits({ ...edits, [key(pth)]: typed ? cellValue(raw) : raw.trim() ? raw : null });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const changed = unitsEdited(edits, container).size;
  // The feedback snippets, as edited: the whole list is one edit, dropped when it is back to the saved one.
  const snipKey = key([SNIPPETS_KEY]);
  const savedSnippets = parsed.snippets ?? [];
  const snippets = snipKey in edits ? (edits[snipKey] as string[]) : savedSnippets;
  const snippetsEdited = snipKey in edits;
  const setSnippets = (list: string[]) => {
    const { [snipKey]: _drop, ...rest } = edits;
    setEdits(JSON.stringify(list) === JSON.stringify(savedSnippets) ? rest : { ...rest, [snipKey]: list });
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const dirty = changed > 0 || snippetsEdited;
  const scoreOf = (u: Unit) => {
    if (qs.length) {
      const base = u.score && typeof u.score === 'object' ? (u.score as Record<string, unknown>) : {};
      return Object.fromEntries(qs.map(([q]) => [q, v([...u.scorePath, q], base[q] ?? null)]));
    }
    return v(u.scorePath, u.score && typeof u.score === 'object' ? null : u.score);
  };
  // The whole score map as the engine reads it: questions the sheet has but `questions` lacks included.
  const fullScore = (u: Unit) => (qs.length && isObj(u.score) ? { ...u.score, ...(scoreOf(u) as Record<string, unknown>) } : scoreOf(u));
  const adjOf = (pr: Person) => v([...pr.base, 'adjustment_individual'], pr.adjustment);
  const fbOf = (pr: Person) => v([...pr.base, 'feedback_individual'], pr.feedback);
  // A late unit's penalty (`late_penalty`): switched on writes `apply`, off `waive`; an override
  // is the whole penalty and implies applied, and clearing it writes `apply` back - or, on a unit
  // whose sheet has no `late_penalty` key, drops the edit, so no key is written and nothing reads
  // as changed.
  const lateOf = (u: Unit) => v(u.latePenaltyPath, u.latePenalty);
  const setLate = (u: Unit, value: string) => {
    if (value !== 'apply' || (u.latePenalty !== null && u.latePenalty !== undefined)) return set(u.latePenaltyPath, value);
    const { [key(u.latePenaltyPath)]: _drop, ...rest } = edits;
    setEdits(rest);
  };
  // What the unit loses for lateness, worked out once for its late cell and its final grades.
  const lateFor = (u: Unit) => effectivePenalty(rate, u.info?.days_late, lateOf(u));
  // The engine's hold rules over the marks on screen: each held handle and why.
  const heldNow = holdReasons(sheet.units.map((u) => holdUnit(u, fullScore(u), lateOf(u), adjOf)), questions, rate);
  const heldOf = (u: Unit): HoldReason | null => u.people.map((pr) => heldNow[pr.handle]).find(Boolean) ?? null;
  const unmarked = (u: Unit) => noMark(scoreOf(u));
  const isLate = (u: Unit) => (num(u.info?.days_late) ?? 0) > 0;
  const counts: Record<Filter, number> = {
    all: sheet.units.length,
    unmarked: sheet.units.filter(unmarked).length,
    late: sheet.units.filter(isLate).length,
    held: sheet.units.filter((u) => heldOf(u) !== null).length,
  };
  // Moderation: who saved each unit's marks, as the saved sheet records it.
  const markerOf = (u: Unit) => (blank(u.markedBy) ? '' : str(u.markedBy).trim());
  const markers = markerLines(sheet.units.map((u) => ({ marker: u.markedBy, score: u.score })), questions);
  const anyMarker = sheet.units.some((u) => markerOf(u) !== '');
  const shown = sheet.units.filter((u) => (filter === 'all' || (filter === 'unmarked' ? unmarked(u) : filter === 'late' ? isLate(u) : heldOf(u) !== null)) && (byMarker === null || !anyMarker || markerOf(u) === byMarker));
  // Handed in outside GitHub: no `info:` anywhere, so nothing to show from the submission.
  const external = sheet.units.length > 0 && sheet.units.every((u) => u.info === null);
  // Tests and Completion: a column only when some unit's submission has the fact (the template runs tests / the completion check).
  const hasInfo = (k: string) => sheet.units.some((u) => u.info !== null && k in u.info);
  const showTests = hasInfo('autograde'), showCompletion = hasInfo('completion');
  const subCols = external ? 0 : subFolded ? 1 : 2 + (showTests ? 1 : 0) + (showCompletion ? 1 : 0);
  // A team's member rows: its ▸/▾ (or Open/Close all teams) decides; until then open where a member carries an adjustment or their own feedback.
  const membersOpen = (u: Unit) => folds[`m:${u.key}`] ?? u.people.some((pr) => !blank(pr.adjustment) || !blank(pr.feedback));
  // The Adjustment column: while any team is open; on an individual sheet behind the Adjustments chip, open where any is saved.
  const adjOpen = sheet.group ? sheet.units.some(membersOpen) : (adjChoice ?? sheet.units.some((u) => !blank(u.people[0]?.adjustment)));
  /** A person's cell: real name, handle under it (the handle alone when the roster has no name). */
  const who = (h: string, strong: boolean) => {
    const n = realName(h);
    const top = n || h;
    return <>{strong ? <b>{top}</b> : <span>{top}</span>}{n ? <><br /><span class="slug footnote">{h}</span></> : null}</>;
  };
  const input = (pth: Path, orig: unknown, cls: string, label: string, col: string, typed = true) => (
    <input class={cls} type="text" inputMode={typed ? 'decimal' : undefined} value={str(v(pth, orig))} aria-label={label} data-col={col} onInput={(e) => set(pth, (e.target as HTMLInputElement).value, typed)} />
  );
  const openDrawer = (row: RowRef, focus: string | null) => {
    setSvPick(null);
    setDrawer({ ...row, focus, grab: true });
  };
  const here = (r: RowRef) => drawer !== null && drawer.unit === r.unit && drawer.member === r.member;
  // Closing puts focus on the line it was showing, in the row it ended on.
  const closeDrawer = () => {
    const at = drawer;
    setDrawer(null);
    const tr = at && [...(table.current?.querySelectorAll<HTMLElement>('tr[data-unit]') ?? [])].find((r) => r.dataset.unit === at.unit && (r.dataset.member ?? null) === at.member);
    (tr?.querySelector<HTMLElement>(`[data-preview="${at?.focus === 'pn' ? 'pn' : 'fb'}"]`) ?? tr?.querySelector<HTMLElement>('[data-preview]'))?.focus();
  };
  // Feedback and Private notes in the grid: the first line of the text, which opens the row's side panel.
  const preview = (text: unknown, label: string, field: 'fb' | 'pn', row: RowRef, extra: ComponentChildren = null) => {
    const t = str(text).trim();
    return (
      <button class={`md-preview${t ? '' : ' empty'}`} type="button" aria-label={label} title={t || 'Open the side panel to write'} data-preview={field} onClick={() => openDrawer(row, field)}>
        <span class="md-line">{t ? t.split('\n')[0] : '+'}</span>{extra}
      </button>
    );
  };
  // The row's submission, where GitHub holds it: the student's or team's repo, or their folder in the drop box.
  // The stem and the shape are status.json's (`semester_name`, `submit_shape`); an older status falls back to the sheet name and the template.
  const stem = a.semester_name || name;
  const shape = a.submit_shape ?? ({ external: 'external', shared_dropbox_repo: 'shared-dropbox-repo' } as Record<string, string>)[str(cfg.submit_via)] ?? '';
  const submissionOf = (u: Unit): DrawerLink | null => {
    if (external || shape === 'external') return null;
    if (shape === 'shared-dropbox-repo') return { href: `https://github.com/${org}/${stem}-submissions/tree/HEAD/${encodeURIComponent(u.key)}`, text: 'Submission folder' };
    return { href: `https://github.com/${org}/${stem}-${u.key}`, text: 'Submission repo' };
  };
  // What the cutoff archived beside the sheet (collect: `.system/autograde/<slug>/<unit>.json` and `.ipynb` in semester-config).
  const archived = (u: Unit, ext: string) => `https://github.com/${org}/${CONFIG_REPO}/blob/HEAD/${NAMES.records.autograde}/${encodeURIComponent(name)}/${encodeURIComponent(u.key)}.${ext}`;
  const testsOf = (u: Unit): string | null => (runs.tests && u.info !== null && !blank(u.info.autograde) ? archived(u, 'json') : null);
  // The executed notebook exists only where the check ran it: clean, or with errors (collect `_check_completion`).
  const notebookOf = (u: Unit): string | null => {
    const c = str(u.info?.completion);
    return runs.completion && (c === 'ran-clean' || c.startsWith('errors:')) ? archived(u, 'ipynb') : null;
  };
  // "—" where the template does not run the check, whatever the sheet carries (a key can outlive the
  // setting that wrote it), and where this unit's sheet has no such fact; blank until the cutoff fills it.
  const fact = (u: Unit, k: 'autograde' | 'completion') => (!(k === 'autograde' ? runs.tests : runs.completion) || (u.info !== null && !(k in u.info)) ? '—' : str(u.info?.[k]));
  const info = (u: Unit) => {
    if (external) return [];
    if (subFolded) {
      const parts: string[] = [];
      if (u.info) {
        const days = num(u.info.days_late);
        if (!u.info.submitted) parts.push('Nothing');
        else if (days !== null) parts.push(days > 0 ? `${plural(days, 'day')} late` : 'on time');
        if (runs.tests && !blank(u.info.autograde)) parts.push(`${str(u.info.autograde)} tests`);
        if (runs.completion && !blank(u.info.completion)) parts.push(`completion ${str(u.info.completion)}`);
      }
      return [<td class="sys">{parts.length ? <span class="chip">{parts.join(' · ')}</span> : null}</td>];
    }
    return [
      <td class="sys">{submittedShort(u.info?.submitted) || (u.info ? 'Nothing' : '—')}</td>,
      <td class="sys num">{str(u.info?.days_late)}</td>,
      ...(showTests ? [<td class={`sys${runs.tests && !blank(u.info?.autograde) ? ' auto' : ''}`}>{testsOf(u) ? <a href={testsOf(u)!} target="_blank" rel="noopener" title="Which tests passed, in semester-config">{fact(u, 'autograde')}</a> : fact(u, 'autograde')}</td>] : []),
      ...(showCompletion ? [<td class="sys">{notebookOf(u) ? <a href={notebookOf(u)!} target="_blank" rel="noopener" title="The notebook as the check ran it, in semester-config">{fact(u, 'completion')}</a> : fact(u, 'completion')}</td>] : []),
    ];
  };
  // Layout A: the penalty, its switch, and the override box on one line.
  const lateCell = (u: Unit, pen: Penalty) => {
    const days = num(u.info?.days_late);
    // No late rule: nothing to waive or override, so no controls (the engine ignores the key).
    if (!pen.late || days === null || rate === null) return <td class="pen" />;
    const waived = pen.mode === 'waive';
    const figure = penaltyFigure(waived ? effectivePenalty(rate, days) : pen);
    const box = typed[u.key] ?? overrideText(lateOf(u));
    return (
      <td class="pen">
        <span class="pen-a">
          <span class={`val${waived ? ' off' : ''}`}>{figure}</span>
          <input class="late-switch" type="checkbox" role="switch" title="Apply the late penalty" aria-label={`Apply the late penalty for ${u.key}`} checked={!waived} onChange={(e) => { const on = (e.target as HTMLInputElement).checked; setTyped({ ...typed, [u.key]: '' }); setLate(u, on ? 'apply' : 'waive'); }} />
          <input class={`ovr${pen.mode === 'set' ? ' set' : ''}${pen.mode === 'bad' ? ' bad' : ''}`} type="text" inputMode="decimal" placeholder="%" title="Override: the whole penalty as a percentage" aria-label={`Late penalty override for ${u.key}, as a percentage`} aria-invalid={pen.mode === 'bad' ? 'true' : undefined} disabled={waived} data-col="ovr" value={box} onInput={(e) => { const raw = (e.target as HTMLInputElement).value; setTyped({ ...typed, [u.key]: raw }); setLate(u, overrideValue(raw)); }} />
        </span>
        {pen.mode === 'bad' ? <Invalid>Write a percentage, or clear it: held until then.</Invalid> : null}
      </td>
    );
  };
  const qfbCount = (u: Unit) => qs.filter(([q]) => !blank(v([...u.questionFeedbackPath, q], u.questionFeedback[q] ?? null))).length;
  const heldChip = (u: Unit) => {
    const why = heldOf(u);
    return why ? <><br /><span class="chip bad held-chip">held</span> <span class="held-why">{HOLD_WORDS[why]}</span></> : null;
  };
  // The unit's own row: its score per question, and (on a group sheet) the team's feedback.
  const unitRow = (u: Unit) => {
    const total = scoreTotal(scoreOf(u), questions);
    const pen = lateFor(u);
    const nosub = u.info !== null && !u.info?.submitted;
    const scoreCells = qs.length
      ? [...qs.map(([q, m]) => <td>{input([...u.scorePath, q], (u.score as Record<string, unknown> | null)?.[q] ?? null, 'q', `${q}${outOf(m)} for ${u.key}`, `q:${q}`)}</td>), <td class="calc">{round(total)}</td>]
      : [<td>{input(u.scorePath, typeof u.score === 'object' ? null : u.score, 'q', `Score for ${u.key}`, 'score')}</td>];
    const n = qfbCount(u);
    // Feedback per question lives in the side panel; the Feedback cell says how many questions have some.
    const qCount = n ? <span class="chip qfb-count" title={`Feedback on ${plural(n, 'question')}`}>✎ {n}</span> : null;
    const mark = unmarked(u) ? { 'data-unmarked': '' } : {};
    if (!sheet.group) {
      const pr = u.people[0];
      return (
        <tr class={`unit-row${nosub ? ' nosub' : ''}${here({ unit: u.key, member: null }) ? ' md-here' : ''}`} data-unit={u.key} {...mark}>
          <td>{who(u.key, true)}{heldChip(u)}</td>{info(u)}{scoreCells}
          {lateCell(u, pen)}
          {adjOpen ? <td class="adjcol">{input([...pr.base, 'adjustment_individual'], pr.adjustment, 'adj', `Adjustment for ${u.key}`, 'adj')}</td> : null}
          <td class="calc">{round(finalGrade(total, pen, adjOf(pr)))}</td>
          <td class="grow">{preview(fbOf(pr), `Open feedback for ${u.key}`, 'fb', { unit: u.key, member: null }, qCount)}</td>
          <td class="grow">{preview(v([...pr.base, NOTES_KEY], pr.notes), `Open private notes for ${u.key}`, 'pn', { unit: u.key, member: null })}</td>
        </tr>
      );
    }
    const open = membersOpen(u);
    const names = u.people.map((pr) => realName(pr.handle) || pr.handle);
    return (
      <tr class={`team-row${nosub ? ' nosub' : ''}${here({ unit: u.key, member: null }) ? ' md-here' : ''}`} data-unit={u.key} {...mark}>
        <td>
          <button class="fold-toggle" type="button" aria-expanded={open} aria-label={`${open ? 'Hide' : 'Show'} the members of ${u.key}`} onClick={() => setFolds({ ...folds, [`m:${u.key}`]: !open })}>{open ? '▾' : '▸'}</button>
          <b>{u.key}</b><br /><span class="footnote team-names">{names.length ? `${names.join(', ')} · ` : ''}{names.length}</span>{heldChip(u)}
        </td>{info(u)}{scoreCells}
        {lateCell(u, pen)}{adjOpen ? <td class="adjcol" /> : null}<td class="calc">{round(finalGrade(total, pen, 0))}</td>
        <td class="grow">{u.feedbackPath ? preview(v(u.feedbackPath, u.feedback), `Open team feedback for ${u.key}`, 'fb', { unit: u.key, member: null }, qCount) : null}</td>
        <td class="grow">{u.notesPath ? preview(v(u.notesPath, u.notes), `Open team private notes for ${u.key}`, 'pn', { unit: u.key, member: null }) : null}</td>
      </tr>
    );
  };
  // A member's row: the team's score, the member's own adjustment, final, feedback and private notes.
  const memberRows = (u: Unit) => {
    const total = scoreTotal(scoreOf(u), questions);
    const pen = lateFor(u);
    return u.people.map((pr) => (
      <tr class={`member-row${here({ unit: u.key, member: pr.handle }) ? ' md-here' : ''}`} data-unit={u.key} data-member={pr.handle}>
        <td style="padding-left:22px">{who(pr.handle, false)}</td>
        {subCols ? <td class="sys" colSpan={subCols} /> : null}{qs.length ? <td colSpan={qs.length} /> : null}
        <td class="calc team-score">{qs.length ? round(total) : str(scoreOf(u))}</td>
        <td class="pen" />
        <td class="adjcol">{input([...pr.base, 'adjustment_individual'], pr.adjustment, 'adj', `Adjustment for ${pr.handle}`, 'adj')}</td>
        <td class="calc">{round(finalGrade(total, pen, adjOf(pr)))}</td>
        <td class="grow">{preview(fbOf(pr), `Open feedback for ${pr.handle}`, 'fb', { unit: u.key, member: pr.handle })}</td>
        <td class="grow">{preview(v([...pr.base, NOTES_KEY], pr.notes), `Open private notes for ${pr.handle}`, 'pn', { unit: u.key, member: pr.handle })}</td>
      </tr>
    ));
  };
  const rows = shown.flatMap((u) => [
    unitRow(u),
    ...(sheet.group && membersOpen(u) ? memberRows(u) : []),
  ]);
  // The rows in the grid's order, for Ctrl/Cmd+Enter in the side panel.
  const rowOrder: RowRef[] = shown.flatMap((u) => [{ unit: u.key, member: null }, ...(sheet.group && membersOpen(u) ? u.people.map((pr) => ({ unit: u.key, member: pr.handle })) : [])]);
  // A save writes the edits cell by cell, blank snippets dropped, and `marked_by` on every unit whose
  // score this save changes: the signed-in login, for the Markers table and the Marked by filter.
  const applyTo = (base: string) => {
    const out = new YamlText(base);
    for (const [k, val] of Object.entries(edits)) out.assign(JSON.parse(k) as Path, k === snipKey ? (val as string[]).map((s) => s.trim()).filter(Boolean) : val);
    if (env) {
      const scored = new Set(Object.keys(edits).map((k) => JSON.parse(k) as Path).filter((pth) => pth[0] === container && pth[2] === sheet.units.find((u) => u.key === pth[1])?.scorePath[2]).map((pth) => String(pth[1])));
      for (const u of sheet.units) if (scored.has(u.key)) out.assign(u.markedByPath, env.user.login);
    }
    return out.text;
  };
  // A save over someone else's: read the sheet again, put the edits over it cell by cell, and save once more.
  const doSave = async () => {
    if (!env) return setSave({ kind: 'bad', text: 'Sign in to save.' });
    const target = { owner: org, repo: CONFIG_REPO, path };
    const what = [...(changed ? [`${changed} ${sheet.group ? 'team' : 'student'}${changed === 1 ? '' : 's'}`] : []), ...(snippetsEdited ? ['snippets'] : [])].join(', ');
    const opts = { message: `marks: ${a.slug}, ${what}, from the DSL Teaching Console`, statusRepo: [org, CONFIG_REPO] as [string, string] };
    const seen: { last: SaveState | null } = { last: null };
    const report = (s: SaveState) => {
      seen.last = s;
      setSave(s);
    };
    let ok = await saveText(env, target, applyTo(file.text), file.sha, opts, report);
    if (!ok && seen.last?.kind === 'bad' && seen.last.conflict) {
      report({ kind: 'busy', text: 'Someone else saved this sheet; reading it again…' });
      const fresh = await env.client.getContents(org, CONFIG_REPO, path).catch(() => null);
      if (!fresh) return report({ kind: 'bad', text: CONFLICT, conflict: true });
      const n = rowsChanged(file.text, fresh.text);
      env.files.put(org, CONFIG_REPO, path, undefined, fresh.text, fresh.sha);
      const told = `${n} ${n === 1 ? 'row' : 'rows'} changed by someone else; your edits kept.`;
      ok = await saveText(env, target, applyTo(fresh.text), fresh.sha, opts, (s) => report(s.kind === 'busy' || s.kind === 'idle' ? s : { ...s, text: `${told} ${s.text}` }));
    }
    if (ok) {
      setEdits({});
      setTyped({});
    }
  };
  // Enter moves a number box to the same cell one row down; in a text box Ctrl/Cmd+Enter does, and Enter is a new line.
  const onKey = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement;
    const col = el.dataset?.col;
    if (e.key !== 'Enter' || !col) return;
    if (el.tagName === 'TEXTAREA' ? !(e.ctrlKey || e.metaKey) : el.tagName !== 'INPUT' || e.shiftKey) return;
    const same = [...(table.current?.querySelectorAll<HTMLInputElement>('[data-col]') ?? [])].filter((x) => x.dataset.col === col && !x.disabled);
    const next = same[same.indexOf(el as HTMLInputElement) + 1];
    if (!next) return;
    e.preventDefault();
    next.focus();
    next.select?.();
  };
  // Return marks reads the sheet as saved: who gets marks, who is skipped or held, and the final grades.
  const savedHeld = holdReasons(sheet.units.map((u) => holdUnit(u, u.score, u.latePenalty, (pr) => pr.adjustment)), questions, rate);
  const heldUnits = sheet.units.filter((u) => u.people.some((pr) => savedHeld[pr.handle])).length;
  const skipped = sheet.units.filter((u) => !u.people.some((pr) => savedHeld[pr.handle]) && noMark(u.score)).length;
  const sending = sheet.units.length - heldUnits - skipped;
  const finals = sheet.units.flatMap((u) => {
    const pen = effectivePenalty(rate, u.info?.days_late, u.latePenalty);
    return u.people.filter((pr) => !savedHeld[pr.handle]).map((pr) => finalGrade(scoreTotal(u.score, questions), pen, pr.adjustment)).filter((f): f is number => f !== null);
  });
  const noun = sheet.group ? 'team' : 'student';
  const facts = [
    ...(changed ? [`${changed} unsaved ${changed === 1 ? 'row is' : 'rows are'} not counted until you save`] : []),
    `${sending} ${noun}${sending === 1 ? ' gets' : 's get'} marks · ${skipped} not marked yet (skipped) · ${heldUnits} held (fix first)`,
    spread(finals, Object.keys(savedHeld).length),
  ];
  const people = sheet.units.flatMap((u) => u.people.map((pr) => ({ u, pr })));
  /** What one person's marks page will show, from the marks on screen (the toolbar's Student view and the side panel's). */
  const studentViewOf = (u: Unit, pr: Person) => (
    <StudentView handle={pr.handle} title={assignmentTitle(a)} submitted={u.info?.submitted} m={studentMarks({
      group: sheet.group, team: u.key, score: fullScore(u), questions, max: max || null, rate, daysLate: u.info?.days_late, latePenalty: lateOf(u),
      adjustment: adjOf(pr), feedback: fbOf(pr),
      teamFeedback: u.feedbackPath ? v(u.feedbackPath, u.feedback) : null,
      questionFeedback: { ...u.questionFeedback, ...Object.fromEntries(qs.map(([q]) => [q, v([...u.questionFeedbackPath, q], u.questionFeedback[q] ?? null)])) },
    })} />
  );
  // With the side panel open, moving into another row of the grid moves the panel with it (focus stays in the grid).
  const followFocus = (e: FocusEvent) => {
    if (!drawer) return;
    const tr = (e.target as HTMLElement).closest?.('tr[data-unit]') as HTMLElement | null;
    if (!tr) return;
    const row = { unit: tr.dataset.unit!, member: tr.dataset.member ?? null };
    if (!here(row)) setDrawer({ ...row, focus: null, grab: false });
  };
  const drawerUnit = drawer ? sheet.units.find((u) => u.key === drawer.unit) ?? null : null;
  const drawerMember = drawer?.member && drawerUnit ? drawerUnit.people.find((pr) => pr.handle === drawer.member) ?? null : null;
  const drawerView = (() => {
    if (!drawer || !drawerUnit || (drawer.member && !drawerMember)) return null;
    const u = drawerUnit;
    const qBoxes = (label: (q: string) => string): DrawerBox[] =>
      qs.map(([q, m]) => ({ path: [...u.questionFeedbackPath, q], field: `qfb:${q}`, heading: `Feedback on ${q}${outOf(m)}`, label: label(q), value: str(v([...u.questionFeedbackPath, q], u.questionFeedback[q] ?? null)) }));
    const links = [
      submissionOf(u),
      ...(testsOf(u) ? [{ href: testsOf(u)!, text: `Test results (${str(u.info?.autograde)})` }] : []),
      ...(notebookOf(u) ? [{ href: notebookOf(u)!, text: 'Executed notebook' }] : []),
    ].filter((l): l is DrawerLink => l !== null);
    let eyebrow: string, title: string, sub: string | undefined, boxes: DrawerBox[], viewOf: Person | null;
    if (drawerMember) {
      const pr = drawerMember, nm = realName(pr.handle);
      eyebrow = `Member of ${u.key}`;
      title = nm || pr.handle;
      sub = nm ? pr.handle : undefined;
      boxes = [
        { path: [...pr.base, 'feedback_individual'], field: 'fb', heading: 'Their own feedback (only this member sees it)', label: `Feedback for ${pr.handle}`, value: str(fbOf(pr)) },
        { path: [...pr.base, NOTES_KEY], field: 'pn', heading: 'Private notes (never sent)', label: `Private notes for ${pr.handle}`, value: str(v([...pr.base, NOTES_KEY], pr.notes)), private: true },
      ];
      viewOf = pr;
    } else if (sheet.group) {
      eyebrow = 'Team';
      title = u.key;
      sub = u.people.map((pr) => realName(pr.handle) || pr.handle).join(', ') || undefined;
      boxes = [
        ...(u.feedbackPath ? [{ path: u.feedbackPath, field: 'fb', heading: 'Team feedback (every member sees it)', label: `Team feedback for ${u.key}`, value: str(v(u.feedbackPath, u.feedback)) }] : []),
        ...qBoxes((q) => `Feedback on ${q} for ${u.key}`),
        ...(u.notesPath ? [{ path: u.notesPath, field: 'pn', heading: 'Team private notes (never sent)', label: `Team private notes for ${u.key}`, value: str(v(u.notesPath, u.notes)), private: true }] : []),
      ];
      viewOf = u.people.find((pr) => pr.handle === svPick) ?? u.people[0] ?? null;
    } else {
      const pr = u.people[0], nm = realName(u.key);
      eyebrow = 'Student';
      title = nm || u.key;
      sub = nm ? u.key : undefined;
      boxes = [
        { path: [...pr.base, 'feedback_individual'], field: 'fb', heading: 'Feedback', label: `Feedback for ${u.key}`, value: str(fbOf(pr)) },
        ...qBoxes((q) => `Feedback on ${q} for ${u.key}`),
        { path: [...pr.base, NOTES_KEY], field: 'pn', heading: 'Private notes (never sent)', label: `Private notes for ${u.key}`, value: str(v([...pr.base, NOTES_KEY], pr.notes)), private: true },
      ];
      viewOf = pr;
    }
    const at = rowOrder.findIndex((r) => here(r));
    const view = viewOf ? (
      <details class="md-view">
        <summary>Student view</summary>
        {sheet.group && !drawerMember && u.people.length > 1 ? (
          <select class="student-pick" aria-label="Member shown" value={viewOf.handle} onChange={(e) => setSvPick((e.target as HTMLSelectElement).value)}>
            {u.people.map((pr) => <option value={pr.handle}>{realName(pr.handle) ? `${realName(pr.handle)} (${pr.handle})` : pr.handle}</option>)}
          </select>
        ) : null}
        {studentViewOf(u, viewOf)}
      </details>
    ) : null;
    return (
      <MarksDrawer rowKey={`${drawer.unit}/${drawer.member ?? ''}`} eyebrow={eyebrow} title={title} sub={sub} boxes={boxes} links={links}
        onText={(pth, text) => set(pth, text, false)} snippets={snippets} onSnippets={setSnippets} view={view}
        focus={drawer.grab ? drawer.focus : null} grab={drawer.grab} onClose={closeDrawer}
        onNext={(field) => { const next = rowOrder[at + 1]; if (next) setDrawer({ ...next, focus: field, grab: true }); }} />
    );
  })();
  // Moderation: each marker's count, mean and median score, from the saved sheet.
  const markersTable = anyMarker ? (
    <section class="panel section markers">
      <h2>Markers <Hint label="About Markers">Who saved each {noun}’s marks from the console, read from the saved mark sheet: how many each marked, and the mean and median score (the questions added up, before any late penalty or adjustment). Use Marked by above the grid for a second pass over one marker’s {noun}s.</Hint></h2>
      <div class="table-wrap">
        <table class="grid">
          <thead><tr><th>Marker</th><th class="num">Marked</th><th class="num">Mean{max ? ` / ${max}` : ''}</th><th class="num">Median{max ? ` / ${max}` : ''}</th></tr></thead>
          <tbody>{markers.map((m) => <tr><td>{m.marker || <span class="footnote">not recorded</span>}</td><td class="num">{m.count}</td><td class="num">{m.mean === null ? '—' : round(m.mean)}</td><td class="num">{m.median === null ? '—' : round(m.median)}</td></tr>)}</tbody>
        </table>
      </div>
    </section>
  ) : null;
  const view = viewing === null ? null : (people.find((x) => x.pr.handle === viewing) ?? people[0] ?? null);
  const restore = () => {
    if (!pending) return;
    setEdits({ ...pending.edits, ...edits });
    setPending(null);
  };
  const returnDef_ = (returnDef.current = returnMarks(scope, asgRef(a, group), sending, name, late, facts, <PreviewEmails org={org} slug={name} />, perQuestionOf(p, a, env)));
  // Returning is its own block above the grid: the panel it opens holds the figures and Preview.
  const returnBlock = (
    <div class="return-block">
      <span class="return-main"><OpOpen def={returnDef_} cls="btn big" label={returnDef_.verb} /><VerbHint def={returnDef_} /></span>
      <button class="btn small quiet" type="button" onClick={() => setViewing('')}>Student view</button>
    </div>
  );
  const tools = (
    <div class="marks-bar">
      <div class="marks-filters" role="group" aria-label="Show">
        <span class="footnote">Show:</span>
        {(['all', 'unmarked', 'late', 'held'] as const).map((f) => <button class={`chip filter${filter === f ? ' on' : ''}`} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{FILTER_WORD[f]} {counts[f]}</button>)}
        {anyMarker ? (
          <label class="marker-filter footnote">Marked by{' '}
            <select aria-label="Marked by" value={byMarker === null ? '*' : byMarker === '' ? '-' : byMarker} onChange={(e) => { const x = (e.target as HTMLSelectElement).value; setByMarker(x === '*' ? null : x === '-' ? '' : x); }}>
              <option value="*">anyone</option>
              {markers.filter((m) => m.marker).map((m) => <option value={m.marker}>{m.marker}</option>)}
              <option value="-">not recorded</option>
            </select>
          </label>
        ) : null}
        {sheet.group ? null : <button class="chip adj-chip" type="button" aria-expanded={adjOpen} onClick={() => setAdjChoice(!adjOpen)}>Adjustments {adjOpen ? '▾' : '▸'}</button>}
        {Object.keys(widths).length ? <button class="btn small quiet reset-widths" type="button" onClick={() => setWidths({})}>Reset widths</button> : null}
        <button class="icon-btn full-toggle" type="button" ref={fullBtn} aria-label={full ? 'Exit full screen' : 'Full screen'} title={full ? 'Exit full screen (Esc)' : 'Full screen'} onClick={() => setFull(!full)}>{full ? '⤡' : '⤢'}</button>
      </div>
      <div class="marks-tools">
        <SaveBar state={save} onSave={() => void doSave()} label="Save marks" disabled={!dirty} small file={{ org, repo: CONFIG_REPO, path }} note={dirty ? [...(changed ? [`${changed} ${noun}${changed > 1 ? 's' : ''} changed`] : []), ...(snippetsEdited ? ['snippets changed'] : [])].join(' · ') : 'No unsaved changes'} />
      </div>
    </div>
  );
  const subHint = <ColHint col="From the submission">Filled in by the toolkit from the submission; you never type these. Click the heading to fold these columns into one.</ColHint>;
  const subFold = <button class="th-fold" type="button" aria-expanded={!subFolded} onClick={() => setSubFolded(!subFolded)}>From the submission {subFolded ? '▸' : '▾'}</button>;
  // The name column fits its longest line (a name, a handle, or a team's members line), up to 220px; longer wraps.
  const longest = Math.max(
    8,
    ...sheet.units.flatMap((u) => [
      u.key.length + 4,
      ...(sheet.group ? [`${u.people.map((pr) => realName(pr.handle) || pr.handle).join(', ')} · ${u.people.length}`.length] : []),
      ...u.people.map((pr) => (realName(pr.handle) || pr.handle).length),
    ]),
  );
  const whoWidth = Math.min(220, Math.max(96, Math.round(longest * 6.4 + 28)));
  // The grid's columns in order, each content-wide by default; Feedback and Private notes share the rest.
  const cols: MarksCol[] = [
    { key: 'who', def: whoWidth, label: sheet.group ? 'Team' : 'Student' },
    ...(external ? [] : subFolded ? [{ key: 'sub', def: 170, label: 'From the submission' }] : [
      { key: 'submitted', def: 105, label: 'Submitted' },
      { key: 'days', def: 56, label: 'Days late' },
      ...(showTests ? [{ key: 'tests', def: 56, label: 'Tests' }] : []),
      ...(showCompletion ? [{ key: 'completion', def: 90, label: 'Completion' }] : []),
    ]),
    ...qs.map(([q]) => ({ key: `q:${q}`, def: 64, label: q })),
    { key: 'score', def: 56, label: 'Score' },
    { key: 'penalty', def: rate !== null ? 156 : 64, label: 'Penalty' },
    ...(adjOpen ? [{ key: 'adj', def: 86, label: 'Adjustment' }] : []),
    { key: 'final', def: 56, label: 'Final' },
    { key: 'fb', def: null, label: 'Feedback' },
    { key: 'pn', def: null, label: 'Private notes' },
  ];
  const widthOf = (c: MarksCol) => widths[c.key] ?? c.def;
  const setWidth = (k: string, w: number) => setWidths((cur) => ({ ...cur, [k]: Math.max(MIN_COL, Math.round(w)) }));
  const resetWidth = (k: string) => setWidths((cur) => Object.fromEntries(Object.entries(cur).filter(([x]) => x !== k)));
  // The table is never narrower than its fixed columns plus the two text columns' minimum.
  const minTable = cols.reduce((n, c) => n + (c.key === 'fb' || c.key === 'pn' ? Math.max(widths[c.key] ?? 0, MIN_TEXT_COL) : (widthOf(c) ?? 0)), 0);
  /** The handle on a column's right edge: drag (mouse, pen or touch), arrows by 8px, double-click for the default. */
  const grip = (key: string) => {
    const c = cols.find((x) => x.key === key);
    if (!c) return null;
    const start = (e: PointerEvent) => {
      const el = e.currentTarget as HTMLElement;
      const w0 = widthOf(c) ?? el.parentElement!.getBoundingClientRect().width;
      const x0 = e.clientX;
      e.preventDefault();
      el.setPointerCapture?.(e.pointerId);
      const move = (m: PointerEvent) => setWidth(key, w0 + m.clientX - x0);
      const end = () => {
        el.removeEventListener('pointermove', move);
        el.removeEventListener('pointerup', end);
        el.removeEventListener('pointercancel', end);
      };
      el.addEventListener('pointermove', move);
      el.addEventListener('pointerup', end);
      el.addEventListener('pointercancel', end);
    };
    const keys = (e: KeyboardEvent) => {
      const d = e.key === 'ArrowLeft' ? -8 : e.key === 'ArrowRight' ? 8 : 0;
      if (!d) return;
      e.preventDefault();
      const el = e.currentTarget as HTMLElement;
      setWidth(key, (widthOf(c) ?? el.parentElement!.getBoundingClientRect().width) + d);
    };
    return <span class="col-grip" role="separator" aria-orientation="vertical" tabindex={0} aria-label={`Resize ${c.label}: drag, or use the arrow keys; double-click resets it`} title="Drag to resize; double-click to reset" data-col-key={key} onPointerDown={start} onKeyDown={keys} onDblClick={() => resetWidth(key)} />;
  };
  const colgroup = <colgroup>{cols.map((c) => <col data-col-key={c.key} style={widthOf(c) !== null ? { width: `${widthOf(c)}px` } : undefined} />)}</colgroup>;
  const head = (
    <thead>
      <tr>
        <th rowSpan={2}>{sheet.group ? 'Team' : 'Student'}{grip('who')}</th>
        {external ? null : subFolded ? <th class="sys" rowSpan={2}>{subFold} {subHint}{grip('sub')}</th> : <th class="sys grp-h" colSpan={subCols}>{subFold} {subHint}</th>}
        {qs.length ? <th class="grp-h" colSpan={qs.length}>Points per question</th> : null}
        {qs.length
          ? <th rowSpan={2} class="num">Score{max ? ` / ${max}` : ''} <ColHint col="Score">The questions added up, before any late penalty.</ColHint>{grip('score')}</th>
          : <th rowSpan={2}>Score{max ? ` / ${max}` : ''} <ColHint col="Score">Your mark for the whole assignment: a number, or a word such as pass that students get as written.</ColHint>{grip('score')}</th>}
        <th rowSpan={2} class="sys">Penalty <ColHint col="Penalty">{rule ? `${rule.charAt(0).toUpperCase()}${rule.slice(1)}. Switch it off to waive it, or type your preferred penalty as a percentage to override it.` : 'No late penalty applies to this assignment.'}</ColHint>{grip('penalty')}</th>
        {adjOpen ? <th rowSpan={2} class="adjcol">Adjustment <ColHint col="Adjustment">Added to this person’s final grade (plus or minus points).</ColHint>{grip('adj')}</th> : null}
        <th rowSpan={2} class="sys">Final{max ? ` / ${max}` : ''} <ColHint col="Final">What the student gets: the score less any late penalty, plus their adjustment, never below 0.</ColHint>{grip('final')}</th>
        <th rowSpan={2} class="grow">Feedback <ColHint col="Feedback">Students see this with their marks.{sheet.group ? ' On a team row it reaches every member; on a member’s row, only that member.' : ''}</ColHint>{grip('fb')}</th>
        <th rowSpan={2} class="grow">Private notes <ColHint col="Private notes">For the teaching team only: never sent to students.{sheet.group ? ' A team’s notes are on its row; each member’s on theirs.' : ''}</ColHint></th>
      </tr>
      <tr>
        {external || subFolded ? null : (
          <>
            <th class="sys">Submitted <ColHint col="Submitted">When the work was handed in, as the latest collection found it. Nothing: no submission yet.</ColHint>{grip('submitted')}</th>
            <th class="sys">Days late <ColHint col="Days late">Days started after the deadline, counted by the toolkit. 0 is on time.</ColHint>{grip('days')}</th>
            {showTests ? <th class="sys">Tests <ColHint col="Tests">The template’s automatic tests passed, filled in at the late cutoff. Information for you, never a mark. — where the template has no tests.</ColHint>{grip('tests')}</th> : null}
            {showCompletion ? <th class="sys">Completion <ColHint col="Completion">At the late cutoff the toolkit runs the submitted notebook top to bottom in a clean sandbox and records whether it reached the end. “Completed” means every cell ran without an error; otherwise the first error is named. Information for you, never a mark: a notebook can run through and still be wrong, or stop on a missing package and still be right. “—” where the template has no check.</ColHint>{grip('completion')}</th> : null}
          </>
        )}
        {qs.map(([q, m]) => (
          <th>{q} <ColHint col={q}>Your points for {q}{outOf(m)}{questionFile(m) ? `, marked from ${questionFile(m)}` : ''}. Leave it blank until you have marked it.</ColHint><span class="grp">{questionPoints(m) != null && questionPoints(m) !== '' ? `/ ${String(questionPoints(m))}` : ''}{questionFile(m) ? <>{questionPoints(m) != null && questionPoints(m) !== '' ? ' · ' : ''}<code>{questionFile(m)}</code></> : null}</span>{grip(`q:${q}`)}</th>
        ))}
      </tr>
    </thead>
  );
  return (
    <>
      <div class="page-head"><div>{title}{lede}</div></div>
      {p.tabs}
      {sheet.frozen ? <p class="note" style="margin-bottom:12px"><b>Frozen at the late cutoff.</b> The submission details no longer change; your marks and feedback are still yours to edit.</p> : null}
      {pending ? (
        <p class="note draft-offer">
          Unsaved draft from {fmtWhen(new Date(pending.at).toISOString(), tz, year)} ({plural(unitsEdited(pending.edits, container).size, 'row')}{snipKey in pending.edits ? ', snippets' : ''}){pending.sha && pending.sha !== sha ? ', made before the sheet last changed: restoring puts your edits over the current marks cell by cell' : ''}:{' '}
          <button class="btn small" type="button" onClick={restore}>Restore</button> <button class="btn small quiet" type="button" onClick={() => setPending(null)}>Discard</button>
        </p>
      ) : null}
      {returnBlock}
      <div class={`marking${full ? ' full' : ''}`} role={full ? 'dialog' : undefined} aria-modal={full ? 'true' : undefined} aria-label={full ? `Marks: ${assignmentTitle(a)}` : undefined}>
        {tools}
        {external ? <p class="footnote">Submitted outside GitHub: no dates, no late penalty.</p> : null}
        <div class="table-wrap marks-wrap">
          <table class="grid marks" ref={table} onKeyDown={onKey} onFocusIn={followFocus} style={{ minWidth: `${minTable}px` }}>
            {colgroup}
            {head}
            <tbody>{rows}</tbody>
          </table>
        </div>
        {drawerView}
      </div>
      {markersTable}
      {viewing !== null ? (
        <Modal title="Student view" onClose={() => setViewing(null)}>
          <div class="field">
            <label for="student-pick">Student</label>
            <select id="student-pick" class="student-pick" value={view?.pr.handle ?? ''} onChange={(e) => setViewing((e.target as HTMLSelectElement).value)}>
              {people.map(({ pr }) => <option value={pr.handle}>{realName(pr.handle) ? `${realName(pr.handle)} (${pr.handle})` : pr.handle}</option>)}
            </select>
          </div>
          {view ? studentViewOf(view.u, view.pr) : <p>No students on this sheet yet.</p>}
        </Modal>
      ) : null}
    </>
  );
}

/** What one student's marks page will show, read-only, from the marks on screen: the whole breakdown, never the private notes. */
function StudentView({ handle, title, m, submitted }: { handle: string; title: string; m: ReturnType<typeof studentMarks>; submitted: unknown }) {
  const over = (x: string) => (x && m.max ? `${x} / ${m.max}` : x);
  const qs = Object.entries(m.score).filter(([q]) => q);
  const late = m.daysLate === null ? '' : m.daysLate > 0 ? `${plural(m.daysLate, 'day')} late` : 'on time';
  const perQ = m.questionFeedback.length ? <ul>{m.questionFeedback.map(([q, x]) => <li><b>{q}:</b> <span class="pre">{x}</span></li>)}</ul> : null;
  return (
    <section class="student-view" aria-label={`${handle}’s marks page`}>
      <h3>{handle} · {title}</h3>
      <p class="footnote">What {handle} will see once marks are returned, from the marks on this screen. Private notes are never shown.</p>
      <div class="table-wrap">
        <table class="grid">
          <thead><tr>{qs.map(([q]) => <th>{q}</th>)}<th>Score</th><th>Submitted</th><th>Late</th>{m.adjustment ? <th>Adjustment</th> : null}<th>Final grade</th>{m.team ? <th>Team</th> : null}</tr></thead>
          <tbody><tr>{qs.map(([, x]) => <td>{str(x)}</td>)}<td>{over(m.total || (m.score[''] !== undefined ? str(m.score['']) : '')) || '—'}</td><td>{str(submitted).replace('T', ' ') || 'not submitted'}</td><td>{late}{m.penalty ? ` · penalty ${m.penalty}` : ''}</td>{m.adjustment ? <td>{m.adjustment}</td> : null}<td><b>{over(m.final) || '—'}</b></td>{m.team ? <td>{m.team}</td> : null}</tr></tbody>
        </table>
      </div>
      {m.feedback ? <p class="pre">{m.feedback}</p> : null}
      {m.teamFeedback || (m.team && perQ) ? <blockquote class="sv-team"><b>Team feedback (shared with {m.team}):</b> <span class="pre">{m.teamFeedback}</span>{perQ}</blockquote> : null}
      {!m.team ? perQ : null}
    </section>
  );
}

// --------------------------------------------------------------------------- teams

const TEAM_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** A team and its members, each by email (casefolded): students.csv's key, joined or not. */
interface TeamsState {
  teams: { name: string; members: string[] }[];
}

/** The teams one `team:<n>` column of students.csv holds: the rows sharing a cell value, in file order. */
export function teamsIn(rows: RosterRow[], column: string): TeamsState {
  const out: TeamsState = { teams: [] };
  for (const r of rows) {
    const name = (r.teams[column] ?? '').trim();
    if (!name || !r.email) continue;
    let t = out.teams.find((x) => x.name === name);
    if (!t) out.teams.push((t = { name, members: [] }));
    t.members.push(r.email.toLowerCase());
  }
  return out;
}

/** students.csv with `column` holding each row's team from `state` (blank for none), the column added when the file has none; every other cell kept. */
export function withTeams(text: string, column: string, state: TeamsState): string {
  const t = readTable(text);
  const teamOf = new Map(state.teams.flatMap((x) => x.members.map((m) => [m, x.name] as const)));
  const header = t.header.includes(column) ? t.header : [...t.header, column];
  const rows = t.rows.map((r) => ({ ...r, [column]: teamOf.get((r.email ?? '').trim().toLowerCase()) ?? '' }));
  return writeTable({ header, rows });
}

export function TeamsTab(p: TabProps) {
  const { a, status, now } = p;
  const env = useEnv();
  const tz = tzOf(status), year = yearOf(now, tz), today = todayOf(now, tz);
  const [draft, setDraft] = useState<TeamsState | null>(null);
  const [newTeam, setNewTeam] = useState('');
  const [teamError, setTeamError] = useState('');
  const [save, runSave, setSave] = useSave(env);
  const roster = p.files.file(p.cohort.org, CONFIG_REPO, STUDENTS_FILE);
  const sysRoster = p.files.file(p.cohort.org, CONFIG_REPO, ROSTER_PATH);
  // The assignment's column: `team:<its number>`, else its key (`teams.columns`).
  const column = teamColumn(a);
  // The engine's answers (status.json); absent on an older status, and then no cap is applied.
  const size = a.settings?.max_team_size ?? null;
  const formation = a.settings?.team_formation ?? null;
  // While the settings are read, no cap is applied.
  const maxSize = typeof size?.value === 'number' ? size.value : Infinity;
  const students = roster.kind === 'ready' ? parseRoster(roster.text, sysRoster.kind === 'ready' ? sysRoster.text : null).rows : [];
  const base = teamsIn(students, column);
  const cur = draft ?? base;
  const joined = students.filter((s) => s.handle);
  const byEmail = (m: string) => students.find((s) => s.email.toLowerCase() === m);
  const nameOf = (m: string) => { const s = byEmail(m); return s?.name || s?.handle || m; };
  const inTeam = new Set(cur.teams.flatMap((t) => t.members));
  const free = joined.filter((s) => !inTeam.has(s.email.toLowerCase()));
  const notJoined = students.filter((s) => !s.handle).length;
  const change = (next: TeamsState) => {
    setDraft(next);
    if (save.kind !== 'busy') setSave({ kind: 'idle' });
  };
  const move = (m: string, to: string | null) =>
    change({ teams: cur.teams.map((t) => ({ ...t, members: t.name === to ? [...t.members.filter((x) => x !== m), m] : t.members.filter((x) => x !== m) })) });
  const create = () => {
    const nm = newTeam.trim();
    if (!TEAM_RE.test(nm)) return setTeamError('Use lowercase letters, numbers and dashes, like team-alpha.');
    if (cur.teams.some((t) => t.name === nm)) return setTeamError(`There is already a team called “${nm}”.`);
    setTeamError('');
    setNewTeam('');
    change({ teams: [...cur.teams, { name: nm, members: [] }] });
  };
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(base);
  const over = cur.teams.filter((t) => t.members.length > maxSize);
  const doSave = async () => {
    if (roster.kind !== 'ready') return;
    if (over.length) return setSave({ kind: 'bad', text: `${over.map((t) => t.name).join(', ')} ${over.length > 1 ? 'have' : 'has'} more than ${maxSize} members.` });
    const text = withTeams(roster.text, column, cur);
    const ok = await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: STUDENTS_FILE }, text, roster.sha, { message: `teams: ${a.slug}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] });
    if (ok) setDraft(null);
  };
  // As the engine's formation window: hand out to the late cutoff (due + the late window).
  const pin = a.grading_cutoff_datetime ?? a.due;
  const opens = a.handout ? a.handout.slice(0, 10) : null;
  const closes = pin ? pin.slice(0, 10) : null;
  // An assigned-teams assignment has no window: its Join-team form refuses every request.
  const assigned = formation?.value === 'assigned';
  const window = assigned ? 'assigned' : !opens ? 'never' : today < opens ? 'pending' : closes && today > closes ? 'closed' : 'open';
  const empty = cur.teams.filter((t) => !t.members.length);
  // A failed read is no empty table: nothing to place, and nothing to save over.
  const failed = roster.kind === 'error' ? roster : sysRoster.kind === 'error' ? sysRoster : null;
  if (failed)
    return (
      <>
        <div class="page-head"><div><h2 class="h1">{assignmentTitle(a)}</h2></div></div>
        {p.tabs}
        <CheckLine cls="bad">Could not read {failed === roster ? STUDENTS_FILE : ROSTER_PATH}: {failed.message}. It is tried again shortly.</CheckLine>
      </>
    );
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">{assignmentTitle(a)} <Hint doc="09-release-assignment-to-cohort.md">Students form their own teams on the assignment’s page until the window closes; you can assign the rest here. Students without a team get no repo at hand out.</Hint></h2>
          <p class="lede">{joined.length - free.length} of {joined.length} joined students in {cur.teams.length} teams; {free.length} without a team.{notJoined ? ` ${notJoined} students have not joined yet and cannot be placed.` : ''}</p>
        </div>
        <div class="actions"><a class="btn outline" href={assignmentHref(p.cohort.org, sheetName(p, a.slug))}>Assignment page, as students see it</a></div>
      </div>
      {p.tabs}
      <div class="stack">
        <div class="grid-2">
          <section class="panel section">
            <h2>Window</h2>
            <p>
              <span class={`chip ${window === 'open' ? 'ok' : ''}`}>{window === 'open' ? 'Open' : window === 'pending' ? 'Not open yet' : window === 'closed' ? 'Closed' : assigned ? 'You assign' : 'Never opens'}</span>{' '}
              {window === 'open' ? `Students can form and join teams on the assignment’s page until ${fmtDay(closes, tz, year)}.`
                : window === 'pending' ? `Opens at hand out, ${fmtDay(opens, tz, year)}.`
                : window === 'closed' ? 'Only you can change teams now.'
                : assigned ? 'You assign every team here; students cannot form their own.'
                : 'This assignment is handed out by hand, so students cannot form teams on the assignment’s page; assign them here.'}
            </p>
            {assigned ? null : <p class="footnote">The window runs from hand out until the late cutoff: the due date plus the late window.</p>}
            {window === 'open' && free.length ? <div class="actions"><OpButtons def={teamsWindow(cohortScope(p), asgRef(a, true), fmtDay(closes, tz, year))} small label={`Email ${free.length} without a team`} /></div> : null}
            <a class="textlink" href={`#schedule-${a.slug}`}>Change the dates in the schedule</a>
          </section>
          <section class="panel section">
            <h2>Team size</h2>
            <dl class="kv">
              <dt>Max team size</dt><dd>{size ? <>{String(size.value ?? '')} <span class="footnote">{SOURCE_WORD[size.source]}</span></> : '…'}</dd>
              <dt>How teams form</dt><dd>{formation ? <>{valueWord('team_formation', formation.value)} <span class="footnote">{SOURCE_WORD[formation.source]}</span></> : '…'}</dd>
            </dl>
            <a class="textlink" href={tabHref(a.slug, 'overview')}>Change for this assignment</a>
          </section>
        </div>
        <section class="panel section">
          <div class="section-head"><h2>Without a team</h2><span class="meta">Assign with the picker</span></div>
          <div class="free-list">
            {roster.kind === 'loading' ? <Loading what="Reading the roster" /> : free.length ? free.map((s) => (
              <div class="free-row">
                <span class="member free">{s.name || s.handle}</span>
                <select aria-label={`Team for ${s.name || s.handle}`} onChange={(e) => { const v = (e.target as HTMLSelectElement).value; if (v) move(s.email.toLowerCase(), v); }}>
                  <option value="" selected>Add to a team…</option>
                  {cur.teams.filter((t) => t.members.length < maxSize).map((t) => <option value={t.name}>{t.name} ({t.members.length}{Number.isFinite(maxSize) ? `/${maxSize}` : ''})</option>)}
                </select>
              </div>
            )) : <p class="valid-msg"><Check />Every joined student is in a team.</p>}
          </div>
        </section>
        <section class="panel section">
          <div class="section-head"><h2>Teams</h2><span class="meta">{cur.teams.length} teams</span></div>
          <div class="team-grid">
            {cur.teams.map((t) => {
              const full = t.members.length >= maxSize;
              return (
                <div class="team">
                  <div class="team-h">{t.name}<span class={full ? 'full' : undefined}>{t.members.length}{Number.isFinite(maxSize) ? ` of ${maxSize}` : ''}{full ? ', full' : ''}</span></div>
                  <div class="members">
                    {t.members.map((m) => <span class="member">{nameOf(m)} <button class="x" type="button" aria-label={`Take ${nameOf(m)} out of ${t.name}`} onClick={() => move(m, null)}>&times;</button></span>)}
                    {!t.members.length ? <span class="footnote">No members yet; an empty team is not saved.</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
          <div class="savebar">
            <div class="field">
              <label for="new-team">New team name</label>
              <input type="text" id="new-team" value={newTeam} placeholder="lowercase-with-dashes" onInput={(e) => setNewTeam((e.target as HTMLInputElement).value)} />
              <p class="why">Becomes the team repo’s name.</p>
            </div>
            <button class="btn small outline" type="button" style="align-self:end" onClick={create}>Create team</button>
          </div>
          {teamError ? <Invalid>{teamError}</Invalid> : null}
          {empty.length && dirty ? <p class="footnote">{empty.length} empty team{empty.length > 1 ? 's are' : ' is'} kept only until you leave this page.</p> : null}
        </section>
        <div class="panel" style="display:grid;gap:10px">
          <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty || roster.kind !== 'ready'} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: STUDENTS_FILE, exists: roster.kind !== 'absent' }} note={dirty ? 'Unsaved changes' : undefined} />
        </div>
      </div>
    </>
  );
}
