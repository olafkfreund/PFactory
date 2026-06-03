import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Terminal, RefreshCw, Loader2, Check, FolderOpen } from 'lucide-react';
import { Label } from '../ui/label';
import { Input } from '../ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Button } from '../ui/button';
import { SettingsSection } from './SettingsSection';
import type { AppSettings, SupportedTerminal } from '../../shared/types';

interface DevToolsSettingsProps {
  settings: AppSettings;
  onSettingsChange: (settings: AppSettings) => void;
}

interface DetectedTool {
  id: string;
  name: string;
  path: string;
  installed: boolean;
}

interface DetectedTools {
  ides: DetectedTool[];
  terminals: DetectedTool[];
}

// Terminal display names - alphabetically sorted
const TERMINAL_NAMES: Partial<Record<SupportedTerminal, string>> = {
  alacritty: 'Alacritty',
  ghostty: 'Ghostty',
  gnometerminal: 'GNOME Terminal',
  hyper: 'Hyper',
  iterm2: 'iTerm2',
  kitty: 'Kitty',
  konsole: 'Konsole',
  powershell: 'PowerShell',
  system: 'System Terminal',
  tabby: 'Tabby',
  terminal: 'Terminal.app',
  terminator: 'Terminator',
  tilix: 'Tilix',
  tmux: 'tmux',
  warp: 'Warp',
  wezterm: 'WezTerm',
  windowsterminal: 'Windows Terminal',
  zellij: 'Zellij',
  custom: 'Custom...'  // Always last
};

/**
 * Developer Tools settings component for configuring preferred terminal
 */
export function DevToolsSettings({ settings, onSettingsChange }: DevToolsSettingsProps) {
  const { t } = useTranslation('settings');
  const [detectedTools, setDetectedTools] = useState<DetectedTools | null>(null);
  const [isDetecting, setIsDetecting] = useState(false);
  const [detectError, setDetectError] = useState<string | null>(null);

  // Detect installed tools on mount
  const detectTools = useCallback(async () => {
    setIsDetecting(true);
    setDetectError(null);
    try {
      // Check if the API is available (may not be in dev mode or if preload failed)
      if (!window.API?.worktreeDetectTools) {
        console.warn('[DevToolsSettings] Detection API not available');
        setIsDetecting(false);
        return;
      }

      const result = await window.API.worktreeDetectTools();
      if (result.success && result.data) {
        setDetectedTools(result.data as DetectedTools);
      } else {
        setDetectError(result.error || 'Failed to detect tools');
      }
    } catch (err) {
      setDetectError(err instanceof Error ? err.message : 'Failed to detect tools');
    } finally {
      setIsDetecting(false);
    }
  }, []);

  useEffect(() => {
    detectTools();
  }, [detectTools]);

  const handleTerminalChange = (terminal: SupportedTerminal) => {
    onSettingsChange({
      ...settings,
      preferredTerminal: terminal,
      // Clear custom path when switching away from custom
      customTerminalPath: terminal === 'custom' ? settings.customTerminalPath : undefined
    });
  };

  const handleCustomTerminalPathChange = (path: string) => {
    onSettingsChange({
      ...settings,
      customTerminalPath: path
    });
  };

  // Build Terminal options with detection status
  const terminalOptions: Array<{ value: SupportedTerminal; label: string; detected: boolean }> = [];

  // Always add system terminal first
  terminalOptions.push({
    value: 'system',
    label: TERMINAL_NAMES.system || 'System Terminal',
    detected: true
  });

  // Add detected terminals
  if (detectedTools) {
    for (const tool of detectedTools.terminals) {
      if (tool.id !== 'system') {
        terminalOptions.push({
          value: tool.id as SupportedTerminal,
          label: tool.name,
          detected: true
        });
      }
    }
  }

  // Add remaining terminals that weren't detected
  const detectedTerminalIds = new Set(detectedTools?.terminals.map(t => t.id) || []);
  detectedTerminalIds.add('system'); // Always consider system as detected
  for (const [id, name] of Object.entries(TERMINAL_NAMES)) {
    if (id !== 'custom' && !detectedTerminalIds.has(id)) {
      terminalOptions.push({
        value: id as SupportedTerminal,
        label: name,
        detected: false
      });
    }
  }

  // Add custom option last
  terminalOptions.push({ value: 'custom', label: 'Custom...', detected: false });

  return (
    <SettingsSection
      title={t('devtools.title', 'Developer Tools')}
      description={t('devtools.description', 'Configure your preferred terminal for working with worktrees')}
    >
      <div className="space-y-6">
        {/* Detect Tools Button */}
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={detectTools}
            disabled={isDetecting}
          >
            {isDetecting ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4 mr-2" />
            )}
            {t('devtools.detectAgain', 'Detect Again')}
          </Button>
        </div>

        {detectError && (
          <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md">
            {detectError}
          </div>
        )}

        {/* Terminal Selection */}
        <div className="space-y-2">
          <Label htmlFor="preferred-terminal" className="flex items-center gap-2">
            <Terminal className="h-4 w-4" />
            {t('devtools.terminal.label', 'Preferred Terminal')}
          </Label>
          <Select
            value={settings.preferredTerminal || 'system'}
            onValueChange={(value) => handleTerminalChange(value as SupportedTerminal)}
          >
            <SelectTrigger id="preferred-terminal">
              <SelectValue placeholder={t('devtools.terminal.placeholder', 'Select terminal...')} />
            </SelectTrigger>
            <SelectContent>
              {terminalOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  <div className="flex items-center gap-2">
                    <span>{option.label}</span>
                    {option.detected && (
                      <Check className="h-3 w-3 text-green-500" />
                    )}
                  </div>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {t('devtools.terminal.description', 'AI Factory will open terminal sessions here')}
          </p>

          {/* Custom Terminal Path */}
          {settings.preferredTerminal === 'custom' && (
            <div className="mt-3 space-y-2">
              <Label htmlFor="custom-terminal-path">
                {t('devtools.customPath', 'Custom path')}
              </Label>
              <div className="flex gap-2">
                <Input
                  id="custom-terminal-path"
                  value={settings.customTerminalPath || ''}
                  onChange={(e) => handleCustomTerminalPathChange(e.target.value)}
                  placeholder="/path/to/your/terminal"
                  className="flex-1"
                />
                <Button
                  variant="outline"
                  size="icon"
                  onClick={async () => {
                    const result = await window.API.selectDirectory();
                    if (result) {
                      handleCustomTerminalPathChange(result);
                    }
                  }}
                >
                  <FolderOpen className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* Detection Summary */}
        {detectedTools && !isDetecting && (
          <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded-md">
            <p className="font-medium mb-1">{t('devtools.detected', 'Detected on your system')}:</p>
            <ul className="list-disc list-inside space-y-0.5">
              {detectedTools.terminals.filter(t => t.id !== 'system').map((term) => (
                <li key={term.id}>{term.name}</li>
              ))}
              {detectedTools.terminals.filter(t => t.id !== 'system').length === 0 && (
                <li>{t('devtools.noToolsDetected', 'No additional tools detected')}</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
