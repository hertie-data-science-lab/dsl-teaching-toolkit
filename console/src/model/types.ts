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
  scope: 'course' | 'cohort';
  stage: string;
  text: string;
  stops: string;
  fix?: Fix;
}

export interface CourseStatus {
  org: string;
  name: string;
  code: string;
  stages: Record<string, StageState>;
  ready: boolean;
  materials: { repo: string; state: string }[];
  templates: { repo: string; slug: string; state: string }[];
  cohorts: string[];
}

export interface CohortStatus {
  org: string;
  term: string;
  term_label: string;
  timezone: string;
  week: number;
  weeks: number;
  live: boolean;
  stages: Record<string, StageState>;
  archive_date: string | null;
}

export interface WeekItem {
  when: string;
  type: string; // release | handout | due | event ...
  ref: string;
  title: string;
  state: string;
}

export interface Release {
  id: string;
  when: string;
  type: string | null; // lecture | lab | readings; null when the entry has no deploy block
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
  title: string;
  template: string;
  state: AssignmentState;
  handout: string | null;
  due: string | null;
  late_until: string | null;
  solution_shown: string | null;
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
  inputs: Record<string, string>;
  course?: CourseStatus;
  cohort?: CohortStatus;
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

/** A private outcome file, `classroom-config/.dsl/outcomes/<op>.json` (contracts section 2). */
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
  details?: string[]; // what the op produced or would produce, line by line (a file list, a generated block)
  people?: { handle: string; text: string }[]; // private file only
  started?: string;
  finished?: string;
}
