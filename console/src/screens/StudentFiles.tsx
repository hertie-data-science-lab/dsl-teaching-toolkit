// How a released file is linked on the student screens (decision 0035 rule 10): inside the
// console when it lives in a materials repo, else on GitHub. Every file link the student
// screens draw renders through here.

import type { FileLink } from '../model/student';
import { materialHref } from './StudentMaterials';

/** Where a released file opens: inside the console when it is in a materials repo, else on GitHub. */
export function fileHref(org: string, repos: string[], l: FileLink): { href: string; ext: boolean } {
  return l.repo && l.path && repos.includes(l.repo) ? { href: materialHref(org, l.repo, l.path), ext: false } : { href: l.url, ext: true };
}

export function FileChips({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  return (
    <>
      {links.map((l) => {
        const h = fileHref(org, repos, l);
        return <a class="st-chip" href={h.href} {...(h.ext ? { target: '_blank', rel: 'noopener' } : {})}>{l.name || 'file'}</a>;
      })}
    </>
  );
}

/** A row's files as a list, the site's `session-files`. */
export function FileList({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  return (
    <ul class="session-files">
      {links.map((l) => {
        const h = fileHref(org, repos, l);
        return <li><a href={h.href} {...(h.ext ? { target: '_blank', rel: 'noopener' } : {})}>{l.name || 'file'}</a></li>;
      })}
    </ul>
  );
}
