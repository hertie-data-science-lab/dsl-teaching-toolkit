// The one screen an org still carrying retired names gets (decision 0012: no dual reading).
// Nothing else about it loads: every other screen reads the new names only.

import { notMigratedText, type Leftover } from '../model/migration';
import { CheckLine } from '../ui/bits';

/** The check itself could not be made: nothing of the org loads, and the next page asks again. */
export function MigrationUnknownScreen({ what, org }: { what: 'semester' | 'course'; org: string }) {
  return (
    <>
      <div class="page-head">
        <div>
          <h1>Could not check whether this {what} is migrated</h1>
          <p class="lede">GitHub did not answer for {org}, so the console cannot tell whether it still uses retired names.</p>
        </div>
      </div>
      <section class="panel section"><p>Open the page again to check again.</p></section>
    </>
  );
}

export function NotMigratedScreen({ what, org, leftovers }: { what: 'semester' | 'course'; org: string; leftovers: Leftover[] }) {
  return (
    <>
      <div class="page-head">
        <div>
          <h1>This {what} has not been migrated yet</h1>
          <p class="lede">{org} still uses names the toolkit has retired, and the console reads only the new ones.</p>
        </div>
      </div>
      <section class="panel section">
        {leftovers.map((l) => <CheckLine cls="bad"><code>{notMigratedText(l)}</code></CheckLine>)}
        <p>The lab migrates each org in one pass. Ask the maintainer to run it for {org}; its screens open here once it is done.</p>
      </section>
    </>
  );
}
