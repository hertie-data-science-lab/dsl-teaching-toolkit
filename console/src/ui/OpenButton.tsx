// A repo's Open split button: `ui/OpenSplit.tsx` with `kind: 'repo'`, the name the screens use.

import type { RepoRef } from '../model/open';
import { OpenSplit } from './OpenSplit';

export const OpenButton = (p: RepoRef & { small?: boolean; quiet?: boolean }) => <OpenSplit kind="repo" {...p} />;
