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
  /** The course org's one hand-edited file and the semester's (decision 0039). */
  course_file: string;
  semester_file: string;
  /** The starters New assignment offers when nobody chose (`course.DEFAULT_FORMATS`). */
  default_formats: string[];
  /** The `defaults:` keys of every level, in the order the seeded skeleton writes them (decision 0039 rule 2). */
  default_keys: string[];
  /** The syllabus `semester.yml student_site.syllabus` names when it names none. */
  default_syllabus: string;
  /** The topic on a semester org's `.github` that marks it as one. */
  semester_topic: string;
  /** The course's list of its semesters, in its `.github`. */
  registry_file: string;
  /** Engine-written records, as paths inside the repo that holds them (`records.path` in the engine). */
  records: Record<string, string>;
  /** The hidden first line that routes a Join issue opened through the API (its form's label is dropped). */
  join_markers: { join_course: string; join_team: string };
  /** A semester site's `_config.yml`: the keys every sync rewrites, and the institution keys it leaves to the site (`site_repo` in the engine). */
  site_config: { synced: string[]; institution: string[] };
  /** The old names the engine still recognises (`legacy.py`): the console spells none of them. */
  legacy: { old: string; new: string; where: string; since: string; fault_code: string | null; cost: string }[];
}

export const NAMES: Names = names;

export const CONFIG_REPO = NAMES.config_repo;
export const JOIN_REPO = NAMES.join_repo;
export const SEMESTER_TOPIC = NAMES.semester_topic;
export const REGISTRY_FILE = NAMES.registry_file;
/** The course org's one hand-edited file (decision 0039), `.github/course.yml`. */
export const COURSE_FILE = NAMES.course_file;
/** The semester's one hand-edited settings file, `semester-config/semester.yml`. */
export const SEMESTER_FILE = NAMES.semester_file;
/** The `defaults:` keys, as the files spell them, in skeleton order. */
export const DEFAULT_KEYS = NAMES.default_keys;
/** The syllabus a semester pins when `student_site.syllabus` names none. */
export const DEFAULT_SYLLABUS = NAMES.default_syllabus;
/** The semester's roster: INSTRUCTOR-OWNED `students.csv` and the engine's `.system/roster.csv` (decision 0039 rule 4). */
export const STUDENTS_FILE = 'students.csv';
export const ROSTER_PATH = NAMES.records.roster;
export const STATUS_PATH = NAMES.records.status;
/** A semester's public facts for its students, in the semester org's `.github`. */
export const STUDENT_STATUS_PATH = NAMES.records.student_status;
export const JOIN_MARKERS = NAMES.join_markers;
export const SITE_CONFIG = NAMES.site_config;
export const OUTCOMES_DIR = NAMES.records.outcomes;
/** The semester's pointer to its course, in its config repo. */
export const POINTER_PATH = NAMES.records.pointer;
/** The course's own repo: its settings, its status, the Console workflow. Its name is GitHub's. */
export const COURSE_REPO = '.github';
