/**
 * Theme constants
 * Gruvbox is the default color theme for PFactory; additional palettes are opt-in.
 */

import type { ColorThemeDefinition } from '../types/settings';

export const COLOR_THEMES: ColorThemeDefinition[] = [
  {
    id: 'gruvbox',
    name: 'Gruvbox',
    description: 'Warm retro-groove — Gruvbox light & dark',
    previewColors: { bg: '#fbf1c7', accent: '#b8bb26', darkBg: '#282828' }
  },
  {
    id: 'shadcn',
    name: 'Mira (shadcn)',
    description: 'Clean neutral base with a yellow accent — shadcn/ui',
    previewColors: { bg: '#ffffff', accent: '#ffc800', darkBg: '#0a0a0a', darkAccent: '#f0b500' }
  },
];
