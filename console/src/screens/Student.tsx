// The student shell's screens (decision 0011 rule 2): This week, Schedule, Assignments,
// Marks, Materials and Instructors of one semester. Placeholders until D3 fills them. In an
// instructor's Student view (rule 7) the same screens render with the instructor's own
// identity and never a student's repos or marks.

import { STUDENT_SCREENS, studentHref } from '../router';
import { semesterName, type Semester } from '../model/discovery';
import { Crumbs } from '../ui/bits';

export interface StudentProps {
  semester: Semester;
  /** The screen key from STUDENT_SCREENS. */
  screen: string;
  /** An instructor looking at the semester as a student would. */
  studentView: boolean;
}

/** The screen a hash names, This week for anything else. */
export const studentScreen = (key: string) => (STUDENT_SCREENS.some(([k]) => k === key) ? key : 'week');

export function StudentViewBanner({ semester }: { semester: Semester }) {
  return (
    <div class="ro-banner" role="status">
      <b>Student view.</b>
      <span>What a student of {semesterName(semester)} sees, shown with your own account: no student’s repos or marks.</span>
      <a href={`?cohort=${semester.org}#cohort`}>Back to the instructor screens</a>
    </div>
  );
}

export function StudentScreen({ semester, screen, studentView }: StudentProps) {
  const label = STUDENT_SCREENS.find(([k]) => k === screen)?.[1] ?? 'This week';
  return (
    <>
      <Crumbs items={[{ t: 'Your semesters', href: '#home' }, { t: semesterName(semester), href: studentHref(semester.org) }, { t: label }]} />
      {studentView ? <StudentViewBanner semester={semester} /> : null}
      <div class="page-head"><div><h1>{label}</h1><p class="lede">{semesterName(semester)}{semester.archived ? '; archived' : ''}</p></div></div>
      <section class="panel section stub">
        <p>Coming in D3.</p>
      </section>
    </>
  );
}
