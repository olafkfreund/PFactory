const lightPalette = {
  black: '#282828',
  red: '#9d0006',
  green: '#386b4d',
  yellow: '#63600b',
  blue: '#076678',
  magenta: '#8f3f71',
  cyan: '#2b6d6b',
  white: '#3c3836',
  brightBlack: '#504945',
  brightRed: '#af3a03',
  brightGreen: '#386b4d',
  brightYellow: '#63600b',
  brightBlue: '#076678',
  brightMagenta: '#8f3f71',
  brightCyan: '#2b6d6b',
  brightWhite: '#282828',
};

export function getTerminalTheme() {
  const root = document.documentElement;
  const rootStyle = getComputedStyle(root);
  const readColor = (token: string, fallback: string) =>
    rootStyle.getPropertyValue(token).trim() || fallback;
  const background = readColor('--color-background', '#0B0B0F');
  const foreground = readColor('--color-foreground', '#E8E6E3');
  const cursor = readColor('--color-primary', '#D6D876');
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const context = canvas.getContext('2d');
  // Resolve the oklch token to RGB so the selection can be translucent; grey if canvas is unavailable.
  let [red, green, blue] = [128, 128, 128];
  if (context) {
    context.fillStyle = cursor;
    context.fillRect(0, 0, 1, 1);
    [red, green, blue] = context.getImageData(0, 0, 1, 1).data;
  }

  return {
    background,
    foreground,
    cursor,
    cursorAccent: background,
    selectionBackground: `rgba(${String(red)}, ${String(green)}, ${String(blue)}, 0.25)`,
    selectionForeground: foreground,
    ...(root.classList.contains('dark') ? {
      black: '#1A1A1F',
      red: readColor('--color-destructive', '#FF6B6B'),
      green: readColor('--color-success', '#87D687'),
      yellow: readColor('--color-warning', '#D6D876'),
      blue: readColor('--color-info', '#6BB3FF'),
      magenta: '#C792EA',
      cyan: '#89DDFF',
      white: foreground,
      brightBlack: '#4A4A50',
      brightRed: '#FF8A8A',
      brightGreen: '#A5E6A5',
      brightYellow: '#E8E87A',
      brightBlue: '#8AC4FF',
      brightMagenta: '#DEB3FF',
      brightCyan: '#A6E8FF',
      brightWhite: '#FFFFFF',
    } : lightPalette),
  };
}
