import { describe, expect, it } from 'vitest';
import { md } from '../src/model/format';

describe('md', () => {
  it('joins lines inside a paragraph with a space', () => {
    expect(md('Trees, bagging\nand forests.')).toBe('<p>Trees, bagging and forests.</p>');
  });
  it('breaks a line that ends in two spaces', () => {
    expect(md('Room 2.61  \nclosed book')).toBe('<p>Room 2.61<br>closed book</p>');
  });
  it('starts a new paragraph at a blank line', () => {
    expect(md('One.\n\nTwo *more*.')).toBe('<p>One.</p><p>Two <i>more</i>.</p>');
  });
  it('keeps lists and escapes HTML', () => {
    expect(md('- a\n- <b>')).toBe('<ul><li>a</li><li>&lt;b&gt;</li></ul>');
  });
});
