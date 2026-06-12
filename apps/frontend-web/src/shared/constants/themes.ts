/**
 * Theme constants
 * Gruvbox is the default color theme for PFactory; additional palettes are opt-in.
 */

import type { ColorThemeDefinition } from '../types/settings';

export const COLOR_THEMES: ColorThemeDefinition[] = [
  {
    id: 'gruvbox',
    name: 'Gruvbox',
    description: 'Warm retro-groove — Gruvbox light & dark · PFactory purple accent',
    previewColors: { bg: '#fbf1c7', accent: '#d3869b', darkBg: '#282828' }
  },
];
