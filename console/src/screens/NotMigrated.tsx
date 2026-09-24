// The one screen an org still carrying retired names gets (decision 0012: no dual reading).
// Nothing else about it loads: every other screen reads the new names only.

import { notMigratedText, type Leftover } from '../model/migration';
import { CheckLine } from '../ui/bits';

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
