// The `dsl.status/1` shape (build/contracts.md section 3). The JSON Schema in
// console/schemas/status.schema.json is the validator; these types are its reading.

export type StageState = 'done' | 'todo' | 'blocked' | 'problem';
export type AssignmentState = 'declared' | 'teams_forming' | 'blocked' | 'open' | 'late_window' | 'marking' | 'returned';
export type ReleaseState = 'planned' | 'will_be_skipped' | 'released' | 'late';
export type Conclusion = 'done' | 'nothing_to_do' | 'skipped' | 'previewed' | 'failed';

export interface Fix {
  repo: string;
  path: string;
  line?: number;
  screen?: string;
  entry?: string;
  ref?: string; // branch, when not the default
}

export interface Problem {
  id: string;
  scope: 'course' | 'semester';
  stage: string;
  text: string;
  stops: string;
  fix?: Fix;
  /** When the fault bites (ISO); absent for a fault no date pins. */
  when?: string;
}

export interface CourseStatus {
  org: string;
  name: string;
  code: string;
  stages: Record<string, StageState>;
  /** One sentence per stage that is not done, saying why. */
  stage_why?: Record<string, string>;
  /** Decision 0032, per stage: may it be set aside (C4-C6), and is it. Absent on an older status. */
  stage_optional?: Record<string, boolean>;
  stage_set_aside?: Record<string, boolean>;
  /** C1-C3 done and no course problem: a new semester can start (decision 0019). */
  ready: boolean;
  materials: MaterialsState[];
  /** `starter`: how main is written (decision 0028), the key or the engine's reading of the markers. */
  templates: { repo: string; slug: string; state: string; starter?: 'derived' | 'handwritten' }[];
  semesters: string[];
  /** Work started and not finished (decision 0022): never a problem. */
  todo?: Todo[];
}

/** One line of a materials repo's checklist; `blocks` lines are what `ready` needs. */
export interface MaterialsCheck {
  id: string;
  label: string;
  done: boolean;
  /** What is missing, one sentence; null once done. */
  why?: string | null;
  blocks: boolean;
  /** On `kind_folder`: every content kind, with the top folders of that kind. */
  detail?: { kind: string; folders: string[] }[];
}

export interface MaterialsState {
  repo: string;
  state: string;
  checks?: MaterialsCheck[];
}

export interface Todo {
  id: string;
  kind: 'materials' | 'template';
  repo: string;
  text: string;
  screen?: string;
  entry?: string;
  /** Decision 0032: it blocks nothing, so it may be set aside; and it is. Absent on an older status. */
  optional?: boolean;
  set_aside?: boolean;
}

export interface SemesterStatus {
  org: string;
  key: string; // f2026
  label: string; // Fall 2026
  timezone: string;
  /** `semester_start` / `semester_end` (yyyy-mm-dd); null while unset, absent on an older status. */
  start?: string | null;
  end?: string | null;
  /** Null while either date is unset, 0 before the start; absent on an older status. */
  week?: number | null;
  weeks?: number | null;
  live: boolean;
  /** Past its end and not archived yet; absent on an older status. */
  ended?: boolean;
  stages: Record<string, StageState>;
  stage_why?: Record<string, string>;
  archive_date: string | null;
}

export interface WeekItem {
  when: string;
  kind: string; // release | handout | due | event ...
  ref: string;
  title: string;
  state: string;
}

export interface Release {
  id: string;
  when: string;
  kind: string | null; // a policy kind (lecture, lab, readings, ...); the engine infers one when undeclared
  kind_inferred?: boolean;
  number?: number | null; // the site row's number; null when the site does not number it
  title: string;
  state: ReleaseState;
  source: { repo: string; path: string } | null;
  dest: { repo: string; path: string } | null;
  show_on_site: boolean;
  tbc: boolean;
  copies?: unknown;
}

export interface Assignment {
  slug: string;
  /** The entry's own number (decision 0020); null: it has none. Absent in an older status. */
  number?: number | null;
  title: string;
  template: string;
  state: AssignmentState;
  handout: string | null;
  due: string | null;
  grading_cutoff_datetime: string | null;
  solution_shown: string | null;
  /** Set before the late cutoff: the engine holds the solution until then (the cutoff). */
  solution_held_until?: string | null;
  units: number;
  submissions: number;
  teams: number | null;
  marks: { filled: number; total: number };
  returned: boolean;
  problem: boolean;
}

export interface Operation {
  run_id: number;
  op: string;
  conclusion: Conclusion;
  summary: string;
  finished: string;
}

export interface Status {
  schema: 'dsl.status/1';
  inputs: Record<string, string | null>;
  course?: CourseStatus;
  semester?: SemesterStatus;
  problems?: Problem[];
  this_week?: WeekItem[];
  releases?: Release[];
  assignments?: Assignment[];
  students?: { rows: number; codes_sent: number; joined: number };
  staff?: { instructors: number; tas: number; synced: boolean };
  site?: { url: string; last_update: string | null; stale: boolean };
  app_installed?: boolean | null;
  operations?: Operation[];
}

/** A private outcome file, `<config repo>/.system/outcomes/<op>.json` (contracts section 2). */
export interface Outcome {
  schema: 'dsl.outcome/1';
  op: string;
  run_id: number | null;
  actor: string;
  preview: boolean;
  conclusion: Conclusion;
  summary: string;
  counts?: Record<string, number>;
  reasons?: { code: string; text: string; fix?: Fix }[];
  details?: string[]; // what the op did or would do, one line each (e.g. the files derive wrote)
  block?: string; // a generated text to paste (e.g. the syllabus weekly plan)
  started?: string;
  finished?: string;
}
