// The file-tree row heads both trees draw (decision 0026 rule 5, 0029 rule 8): the folder icon
// that opens with its folder, the file icon, the name. The indent guides are `.file-tree`'s CSS.
// The instructors' `PatternTree` adds its controls around them; the student Materials tree its
// links.

import type { ComponentChildren } from 'preact';
import { File, Folder } from './icons';

/** A folder's summary head: closed and open icons (CSS shows the one that fits), then `name/`. */
export function FolderHead({ name }: { name: string }) {
  return <><span class="ft-closed"><Folder /></span><span class="ft-opened"><Folder open /></span><span class="ft-name">{name}/</span></>;
}

/** A file's head: the icon, then the name, or `children` in its place (a link). */
export function FileHead({ name, children }: { name: string; children?: ComponentChildren }) {
  return <><File />{children ?? <span class="ft-name">{name}</span>}</>;
}
