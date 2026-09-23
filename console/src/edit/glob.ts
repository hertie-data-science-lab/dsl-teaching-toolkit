// `.gitignore` pattern matching, for the match previews of `.releaseignore` and
// `publish.yml` (both use gitignore's syntax; the last matching pattern wins, `!` re-includes).
// The preview reads the repo's root file only; nested `.releaseignore` files still apply
// when the engine copies.

export interface Rule {
  re: RegExp;
  neg: boolean;
  dirOnly: boolean;
}

function toRegex(glob: string): string {
  let out = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*' && glob[i + 1] === '*') {
      const slashAfter = glob[i + 2] === '/';
      out += slashAfter ? '(?:.*/)?' : '.*';
      i += slashAfter ? 2 : 1;
    } else if (c === '*') out += '[^/]*';
    else if (c === '?') out += '[^/]';
    else out += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return out;
}

export function compile(line: string): Rule | null {
  let p = line.replace(/\s+$/, '');
  if (!p || p.startsWith('#')) return null;
  const neg = p.startsWith('!');
  if (neg) p = p.slice(1);
  const dirOnly = p.endsWith('/');
  p = p.replace(/\/+$/, '');
  const anchored = p.includes('/');
  p = p.replace(/^\//, '');
  const body = toRegex(p);
  return { re: new RegExp(anchored ? `^${body}$` : `^(?:.*/)?${body}$`), neg, dirOnly };
}

function hits(r: Rule, path: string, isDir: boolean): boolean {
  const parts = path.split('/');
  for (let i = 1; i <= parts.length; i++) {
    const sub = parts.slice(0, i).join('/');
    const subIsDir = i < parts.length || isDir;
    if (r.dirOnly && !subIsDir) continue;
    if (r.re.test(sub)) return true;
  }
  return false;
}

/** Whether `path` matches the pattern list, gitignore style: the last matching line decides. */
export function matches(lines: string[], path: string, isDir = false): boolean {
  let hit = false;
  for (const l of lines) {
    const r = compile(l);
    if (r && hits(r, path, isDir)) hit = !r.neg;
  }
  return hit;
}
