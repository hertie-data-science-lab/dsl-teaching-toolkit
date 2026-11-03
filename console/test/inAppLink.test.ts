// @vitest-environment happy-dom
// The app follows a `?` link in place only for a plain click on a link with no target: `View
// here` (target _blank) and modifier clicks open a new tab, as the browser would.

import { expect, it } from 'vitest';
import { inAppHref } from '../src/app';

const click = (attrs: Record<string, string>, init: MouseEventInit = {}) => {
  const a = document.createElement('a');
  for (const [k, v] of Object.entries(attrs)) a.setAttribute(k, v);
  const span = document.createElement('span');
  a.appendChild(span);
  document.body.appendChild(a);
  let got: string | null | undefined;
  span.addEventListener('click', (e) => (got = inAppHref(e as MouseEvent)));
  span.dispatchEvent(new MouseEvent('click', { bubbles: true, button: 0, ...init }));
  a.remove();
  return got;
};

it('follows a plain ? link in place, but leaves target=_blank, modifier and external links to the browser', () => {
  expect(click({ href: '?course=x#materials' })).toBe('?course=x#materials');
  expect(click({ href: '?course=x#materials', target: '_self' })).toBe('?course=x#materials');
  expect(click({ href: '?course=x&file=r%2Fa.md#materials', target: '_blank', rel: 'noopener' })).toBeNull();
  expect(click({ href: '?course=x#materials' }, { metaKey: true })).toBeNull();
  expect(click({ href: '?course=x#materials' }, { ctrlKey: true })).toBeNull();
  expect(click({ href: '?course=x#materials' }, { shiftKey: true })).toBeNull();
  expect(click({ href: '?course=x#materials' }, { altKey: true })).toBeNull();
  expect(click({ href: '?course=x#materials' }, { button: 1 })).toBeNull();
  expect(click({ href: 'https://github.com/x' })).toBeNull();
});
