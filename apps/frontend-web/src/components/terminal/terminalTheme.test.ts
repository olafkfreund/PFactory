/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getTerminalTheme } from './terminalTheme';

const ansiKeys = [
  'black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white',
  'brightBlack', 'brightRed', 'brightGreen', 'brightYellow',
  'brightBlue', 'brightMagenta', 'brightCyan', 'brightWhite',
] as const;

function luminance(hex: string) {
  const [red, green, blue] = hex.slice(1).match(/../g)!.map((part) => {
    const channel = parseInt(part, 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

afterEach(() => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove('dark');
  document.documentElement.removeAttribute('style');
});

describe('getTerminalTheme', () => {
  it('uses a readable light palette and transparent selection for an oklch token', () => {
    const root = document.documentElement;
    root.style.setProperty('--color-primary', 'oklch(0.475 0.097 75)');
    const context = {
      fillStyle: '',
      fillRect: vi.fn(),
      getImageData: () => ({ data: new Uint8ClampedArray([124, 83, 8, 255]) }),
    };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);

    const theme = getTerminalTheme();

    expect(context.fillStyle).toBe('oklch(0.475 0.097 75)');
    expect(theme.selectionBackground).toBe('rgba(124, 83, 8, 0.25)');
    for (const key of ansiKeys) {
      expect((luminance('#f5f0e1') + 0.05) / (luminance(theme[key]) + 0.05)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('keeps the existing dark palette', () => {
    document.documentElement.classList.add('dark');
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      fillRect: vi.fn(),
      getImageData: () => ({ data: new Uint8ClampedArray([214, 216, 118, 255]) }),
    } as unknown as CanvasRenderingContext2D);

    const theme = getTerminalTheme();

    expect(theme.brightWhite).toBe('#FFFFFF');
    expect(theme.brightYellow).toBe('#E8E87A');
    expect(theme.black).toBe('#1A1A1F');
  });
});
