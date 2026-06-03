import { Sun, Moon, Monitor, Check } from 'lucide-react';
import { cn } from '../../lib/utils';
import { Label } from '../ui/label';
import { useSettingsStore } from '../../stores/settings-store';
import { COLOR_THEMES } from '../../shared/constants/themes';
import type { AppSettings } from '../../shared/types';
import type { ColorTheme } from '../../shared/types/settings';

interface ThemeSelectorProps {
  settings: AppSettings;
  onSettingsChange: (settings: AppSettings) => void;
}

/**
 * Theme selector component with a 3-option mode toggle (Light/Dark/System).
 *
 * Theme changes are applied immediately for live preview, while other settings
 * require saving to take effect.
 */
export function ThemeSelector({ settings, onSettingsChange }: ThemeSelectorProps) {
  const updateStoreSettings = useSettingsStore((state) => state.updateSettings);

  const currentMode = settings.theme;
  const currentColorTheme: ColorTheme = settings.colorTheme ?? 'gruvbox';

  const handleModeChange = (mode: 'light' | 'dark' | 'system') => {
    // Update local draft state
    onSettingsChange({ ...settings, theme: mode });
    // Apply immediately to store for live preview (triggers App.tsx useEffect)
    updateStoreSettings({ theme: mode });
  };

  const handleColorThemeChange = (colorTheme: ColorTheme) => {
    // Update local draft state
    onSettingsChange({ ...settings, colorTheme });
    // Apply immediately to store for live preview (triggers App.tsx useEffect)
    updateStoreSettings({ colorTheme });
  };

  const getModeIcon = (mode: string) => {
    switch (mode) {
      case 'light':
        return <Sun className="h-4 w-4" />;
      case 'dark':
        return <Moon className="h-4 w-4" />;
      default:
        return <Monitor className="h-4 w-4" />;
    }
  };

  return (
    <div className="space-y-6">
      {/* Mode Toggle */}
      <div className="space-y-3">
        <Label className="text-sm font-medium text-foreground">Appearance Mode</Label>
        <p className="text-sm text-muted-foreground">Choose light, dark, or system preference</p>
        <div className="grid grid-cols-3 gap-3 max-w-md pt-1">
          {(['system', 'light', 'dark'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => handleModeChange(mode)}
              className={cn(
                'flex flex-col items-center gap-2 p-4 rounded-lg border-2 transition-all',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                currentMode === mode
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-primary/50 hover:bg-accent/50'
              )}
            >
              {getModeIcon(mode)}
              <span className="text-sm font-medium capitalize">{mode}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Color Theme */}
      <div className="space-y-3">
        <Label className="text-sm font-medium text-foreground">Color Theme</Label>
        <p className="text-sm text-muted-foreground">
          Switch the overall color palette. Gruvbox is the default; switch to another to match a
          customer's look and feel.
        </p>
        <div className="grid grid-cols-2 gap-3 max-w-md pt-1">
          {COLOR_THEMES.map((ct) => {
            const isActive = currentColorTheme === ct.id;
            return (
              <button
                key={ct.id}
                onClick={() => handleColorThemeChange(ct.id)}
                className={cn(
                  'flex items-center gap-3 p-3 rounded-lg border-2 text-left transition-all',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                  isActive
                    ? 'border-primary bg-primary/5'
                    : 'border-border hover:border-primary/50 hover:bg-accent/50'
                )}
              >
                <span className="flex -space-x-1 shrink-0">
                  <span className="h-5 w-5 rounded-full border border-border" style={{ backgroundColor: ct.previewColors.bg }} />
                  <span className="h-5 w-5 rounded-full border border-border" style={{ backgroundColor: ct.previewColors.accent }} />
                  <span className="h-5 w-5 rounded-full border border-border" style={{ backgroundColor: ct.previewColors.darkBg }} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 text-sm font-medium">
                    {ct.name}
                    {isActive && <Check className="h-3.5 w-3.5 text-primary" />}
                  </span>
                  <span className="block text-xs text-muted-foreground truncate">{ct.description}</span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

    </div>
  );
}
