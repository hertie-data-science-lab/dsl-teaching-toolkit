// The one place the console spells a repo or path name (decisions 0010 and 0012), read from
// `console/schemas/names.json`, which the engine exports beside the other schemas.

import names from '../../schemas/names.json';

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
  /** Engine-written records, as paths inside the repo that holds them (`records.path` in the engine). */
  records: Record<string, string>;
}

export const NAMES: Names = names;

export const CONFIG_REPO = NAMES.config_repo;
export const JOIN_REPO = NAMES.join_repo;
export const INSTRUCTORS_FILE = NAMES.instructors_file;
export const REGISTRY_FILE = NAMES.registry_file;
export const STATUS_PATH = NAMES.records.status;
export const OUTCOMES_DIR = NAMES.records.outcomes;
export const LEDGER_PATH = NAMES.records.distributed;
/** The semester's pointer to its course, in its config repo. */
export const POINTER_PATH = NAMES.records.pointer;
/** The course's own repo: its settings, its status, the Console workflow. Its name is GitHub's. */
export const COURSE_REPO = '.github';
