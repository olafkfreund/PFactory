/**
 * Unit tests for the ViewState zustand store.
 *
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useViewState } from '../ViewStateContext';

describe('useViewState', () => {
  beforeEach(() => {
    // Store is a module-level singleton; reset between tests.
    useViewState.setState({ showArchived: false });
  });

  it('starts with showArchived false', () => {
    const { result } = renderHook(() => useViewState());
    expect(result.current.showArchived).toBe(false);
  });

  it('setShowArchived sets the value', () => {
    const { result } = renderHook(() => useViewState());

    act(() => result.current.setShowArchived(true));
    expect(result.current.showArchived).toBe(true);

    act(() => result.current.setShowArchived(false));
    expect(result.current.showArchived).toBe(false);
  });

  it('toggleShowArchived flips the value', () => {
    const { result } = renderHook(() => useViewState());

    act(() => result.current.toggleShowArchived());
    expect(result.current.showArchived).toBe(true);

    act(() => result.current.toggleShowArchived());
    expect(result.current.showArchived).toBe(false);
  });

  it('exposes a boolean showArchived', () => {
    const { result } = renderHook(() => useViewState());
    expect(typeof result.current.showArchived).toBe('boolean');
  });
});
