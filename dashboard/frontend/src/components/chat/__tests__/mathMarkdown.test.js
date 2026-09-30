import { describe, it, expect } from 'vitest';
import { hasMath, holdOpenMath, normalizeMath } from '../mathMarkdown';

describe('normalizeMath', () => {
  it('turns $x$ into inline double dollars', () => {
    expect(normalizeMath('so $x^2 + 1$ grows')).toBe('so $$x^2 + 1$$ grows');
  });

  it('leaves prices alone', () => {
    expect(normalizeMath('from $5 to $10')).toBe('from $5 to $10');
    expect(normalizeMath('about $5-$10 a month')).toBe('about $5-$10 a month');
    expect(normalizeMath('costs $5, total $10.')).toBe('costs $5, total $10.');
    expect(normalizeMath('an escaped \\$x\\$ stays')).toBe('an escaped \\$x\\$ stays');
  });

  it('turns \\(x\\) into inline and \\[x\\] into a block', () => {
    expect(normalizeMath('where \\(a_1\\) is')).toBe('where $$a_1$$ is');
    expect(normalizeMath('Then\n\\[\n  E = mc^2\n\\]\ndone')).toBe('Then\n\n$$\nE = mc^2\n$$\n\ndone');
  });

  it('makes a one-line $$x$$ on its own line a block, and keeps one inside a sentence inline', () => {
    expect(normalizeMath('$$\\int_0^1 x\\,dx$$')).toBe('\n$$\n\\int_0^1 x\\,dx\n$$\n');
    expect(normalizeMath('a $$x$$ and $$y$$ b')).toBe('a $$x$$ and $$y$$ b');
    expect(normalizeMath('$$\nx\n$$')).toBe('$$\nx\n$$');
  });

  it('does not touch code', () => {
    const src = 'run `echo $HOME$` then\n\n```sh\nprice=$a$b\n\\[x\\]\n```\n\nand $y$';
    expect(normalizeMath(src)).toBe('run `echo $HOME$` then\n\n```sh\nprice=$a$b\n\\[x\\]\n```\n\nand $$y$$');
  });
});

describe('holdOpenMath', () => {
  it('cuts a reply before a formula that has not closed', () => {
    expect(holdOpenMath('The sum is\n$$\n\\sum_{i=1}^{n')).toBe('The sum is');
    expect(holdOpenMath('so \\[ \\frac{a}{')).toBe('so');
    expect(holdOpenMath('where \\(x')).toBe('where');
  });

  it('keeps closed formulas, lone dollars and code', () => {
    expect(holdOpenMath('$$x$$ and $5')).toBe('$$x$$ and $5');
    expect(holdOpenMath('```\n$$\n')).toBe('```\n$$\n');
  });
});

describe('hasMath', () => {
  it('sees double dollars and math fences only', () => {
    expect(hasMath('a $$x$$')).toBe(true);
    expect(hasMath('```math\nx\n```')).toBe(true);
    expect(hasMath('from $5 to $10')).toBe(false);
  });
});
