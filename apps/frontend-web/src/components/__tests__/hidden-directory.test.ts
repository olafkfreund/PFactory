import { describe, it, expect } from 'vitest';

import { isHiddenDirectory } from '../task-detail/TaskFiles';

describe('isHiddenDirectory (js/incomplete-sanitization)', () => {
  it('matches exact names and suffix rules', () => {
    expect(isHiddenDirectory('node_modules')).toBe(true);
    expect(isHiddenDirectory('.git')).toBe(true);
    expect(isHiddenDirectory('mypkg.egg-info')).toBe(true);
    expect(isHiddenDirectory('.egg-info')).toBe(true);
  });

  it('does not leak a literal star into the comparison', () => {
    // The old code did `hidden.replace('*', '')`, which strips only the first
    // occurrence, so a name still containing '*' could never match.
    expect(isHiddenDirectory('*.egg-info')).toBe(true);
    expect(isHiddenDirectory('src')).toBe(false);
    expect(isHiddenDirectory('egg-info')).toBe(false);
  });
});
