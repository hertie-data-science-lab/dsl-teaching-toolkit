import type { GhUser } from '../github/client';
import type { Course, CohortRef } from '../model/discovery';
import type { Files } from '../model/files';
import type { Heartbeat } from '../model/heartbeat';
import type { Loaded } from '../model/status';
import type { Status } from '../model/types';

/** What every cohort screen renders from. */
export interface CohortProps {
  course: Course;
  cohort: CohortRef;
  loaded: Loaded;
  files: Files;
  now: number;
  entry?: string;
  heartbeat?: Heartbeat | null;
  /** A template for the schedule editor's new entry (`?template=`, from New assignment). */
  prefill?: string;
}

/** A cohort screen whose status is ready. */
export interface ReadyProps extends CohortProps {
  status: Status;
}

export interface CourseProps {
  course: Course;
  loaded: Loaded; // the course's public status
  cohortStates: Record<string, Loaded>;
  files: Files;
  now: number;
  entry?: string;
}

export interface HomeProps {
  courses: Course[];
  cohortStates: Record<string, Loaded>;
  now: number;
  user: GhUser;
}
