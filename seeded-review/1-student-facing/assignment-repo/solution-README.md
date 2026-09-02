# Assignment 1 - model solution

Goes out to students after the deadline, two ways:

- **On a clock** - set `solution_datetime:` on this assignment in the cohort's `classroom-config/schedule.yml`, beside its `due_datetime`. The hourly cron pushes this folder into every student/team repo at that moment. Needs `handout_datetime:` set too - the schedule can only push a solution into repos it provisioned. There is no default: leave it out and the solution never ships automatically.
- **By hand** - run **Release assignment** with **include_solution** ticked.

Both do the same thing, idempotently, so a scheduled release you then re-run by hand changes nothing.
