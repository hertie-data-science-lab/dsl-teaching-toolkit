// How a released file is linked on the student screens (decision 0035 rule 10): the shared file
// row (`ui/FileRow.tsx`), its name opening the file inside the console when it lives in a
// materials repo (in All materials; in a kind tab's list, in that tab), else on GitHub, its
// "set up in Profile" pointing at the semester's Profile. Every file link the student screens
// draw renders through here, the Home syllabus and Updates box (`inline`: the name in brackets,
// no menu) and the assignment page's CONTRIBUTIONS.md included.

import type { ComponentChildren } from 'preact';
import { profileHref, type Setup } from '../model/open';
import type { FileLink } from '../model/student';
import { studentHref } from '../router';
import { FileRow, useYourSetup } from '../ui/FileRow';

/** The route that opens a file, `<repo>/<path>`: in All materials, or after a slash in the kind tab `tab` (`kind-<key>`). */
export const materialHref = (org: string, repo: string, path: string, tab?: string) => {
  const entry = encodeURIComponent(`${repo}/${path}`);
  return tab ? `${studentHref(org, tab)}/${entry}` : `${studentHref(org, 'materials')}-${entry}`;
};

/** Where a released file opens: inside the console when it is in a materials repo (in the kind tab `tab` when one is named), else on GitHub. */
export function fileHref(org: string, repos: string[], l: FileLink, tab?: string): { href: string; ext: boolean } {
  return l.repo && l.path && repos.includes(l.repo) ? { href: materialHref(org, l.repo, l.path, tab), ext: false } : { href: l.url, ext: true };
}

interface ItemProps {
  org: string;
  repos: string[];
  link: FileLink;
  setup: Setup | null;
  /** The name's class. */
  cls?: string;
  /** The file is open beside the list. */
  current?: boolean;
  /** The kind tab the file is listed in. */
  tab?: string;
  /** In a sentence (the Updates box): the name in brackets, no menu. */
  inline?: boolean;
  /** What the link says instead of the file's name (the Home syllabus button). */
  label?: ComponentChildren;
}

/** A file's name, linked, then its menu: the one file link of the student screens. */
export function FileLinkItem({ org, repos, link, setup, cls, current, tab, inline, label }: ItemProps) {
  const h = fileHref(org, repos, link, tab);
  return <FileRow org={org} link={link} here={h.ext ? undefined : h.href} setup={setup} profile={profileHref(org, true)} cls={cls} current={current} inline={inline} label={label} />;
}

const linkKey = (l: FileLink) => l.url || `${l.repo}/${l.path}`;

/** A row's files as chips, each with its menu (the schedule). */
export function FileChips({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  const setup = useYourSetup();
  return <>{links.map((l) => <FileLinkItem key={linkKey(l)} org={org} repos={repos} link={l} setup={setup} cls="st-chip" />)}</>;
}

/** A row's files as a list, the site's `session-files`, each with its menu; in a kind tab (`tab`) they open there, `current` (`<repo>/<path>`) marked. */
export function FileList({ org, repos, links, tab, current }: { org: string; repos: string[]; links: FileLink[]; tab?: string; current?: string }) {
  const setup = useYourSetup();
  return (
    <ul class="session-files">
      {links.map((l) => <li key={linkKey(l)}><FileLinkItem org={org} repos={repos} link={l} setup={setup} tab={tab} current={!!current && `${l.repo}/${l.path}` === current} /></li>)}
    </ul>
  );
}
