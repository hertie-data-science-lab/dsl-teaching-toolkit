import { describe, expect, it } from 'vitest';
import { editUrl, ghUrl } from '../src/ui/bits';

describe('GitHub links', () => {
  it('encode each path segment and keep the slashes, for a file or a folder', () => {
    expect(ghUrl('o', 'r', 'week 1/a#b.md')).toBe('https://github.com/o/r/blob/main/week%201/a%23b.md');
    expect(ghUrl('o', 'r', 'labs', 'HEAD', 'tree')).toBe('https://github.com/o/r/tree/HEAD/labs');
    expect(editUrl('o', 'r', 'a b.yml', 'main', 3)).toBe('https://github.com/o/r/edit/main/a%20b.yml#L3');
    expect(ghUrl('o')).toBe('https://github.com/o');
  });
});
