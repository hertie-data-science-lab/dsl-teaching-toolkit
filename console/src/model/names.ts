// The one place the console spells a repo or path name (decisions 0010 and 0012). The engine
// exports them as `console/schemas/names.json`; until that file exists the decided values
// below stand in, and a test fails if the two ever disagree.

export interface Names {
  /** The semester's config repo: what an instructor edits, and `.system/` for the engine. */
  config_repo: string;
  /** The semester's join form repo. */
  join_repo: string;
  /** Where the engine writes, in every repo it writes to. */
  system_dir: string;
  instructors_file: string;
  assignments_file: string;
  /** The course's list of its semesters, in its `.github`. */
  registry_file: string;
  /** Engine-written records, as paths inside the repo that holds them. */
  records: Record<string, string>;
}

export const DECIDED: Names = {
  config_repo: 'semester-config',
  join_repo: 'join',
  system_dir: '.system',
  instructors_file: 'instructors.yml',
  assignments_file: 'assignments.yml',
  registry_file: 'semesters.yml',
  records: {
    status: '.system/status.json',
    outcomes: '.system/outcomes',
    distributed: '.system/gradebook/distributed.csv',
  },
};

const exported = Object.values(import.meta.glob<{ default: Partial<Names> }>('../../schemas/names.json', { eager: true }))[0]?.default;

/** The names.json the engine exported, or undefined before it does. */
export const EXPORTED: Partial<Names> | undefined = exported;

export const NAMES: Names = { ...DECIDED, ...exported, records: { ...DECIDED.records, ...exported?.records } };

export const CONFIG_REPO = NAMES.config_repo;
export const JOIN_REPO = NAMES.join_repo;
export const INSTRUCTORS_FILE = NAMES.instructors_file;
export const REGISTRY_FILE = NAMES.registry_file;
export const STATUS_PATH = NAMES.records.status;
export const OUTCOMES_DIR = NAMES.records.outcomes;
export const LEDGER_PATH = NAMES.records.distributed;
/** The course's own repo: its settings, its status, the Console workflow. Its name is GitHub's. */
export const COURSE_REPO = '.github';
