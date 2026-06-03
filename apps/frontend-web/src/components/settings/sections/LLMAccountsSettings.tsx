import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Eye,
  EyeOff,
  Plus,
  Trash2,
  Star,
  Check,
  Pencil,
  X,
  Loader2,
  LogIn,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Activity,
  AlertCircle,
  Terminal
} from 'lucide-react';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { Switch } from '../../ui/switch';
import { cn } from '../../../lib/utils';
import { loadClaudeProfiles as loadGlobalClaudeProfiles } from '../../../stores/claude-profile-store';
import { CLIAccountCard } from './CLIAccountCard';
import type { ClaudeProfile, ClaudeAutoSwitchSettings, CLIAccountsDetectionResult } from '../../../shared/types';

interface LLMAccountsSettingsProps {
  isOpen: boolean;
}

export function LLMAccountsSettings({ isOpen }: LLMAccountsSettingsProps) {
  const { t } = useTranslation('settings');
  const { t: tCommon } = useTranslation('common');

  // Claude Accounts state
  const [claudeProfiles, setClaudeProfiles] = useState<ClaudeProfile[]>([]);
  const [activeProfileId, setActiveProfileId] = useState<string | null>(null);
  const [isLoadingProfiles, setIsLoadingProfiles] = useState(false);
  const [newProfileName, setNewProfileName] = useState('');
  const [isAddingProfile, setIsAddingProfile] = useState(false);
  const [deletingProfileId, setDeletingProfileId] = useState<string | null>(null);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [editingProfileName, setEditingProfileName] = useState('');
  const [authenticatingProfileId, setAuthenticatingProfileId] = useState<string | null>(null);
  const [expandedTokenProfileId, setExpandedTokenProfileId] = useState<string | null>(null);
  const [manualToken, setManualToken] = useState('');
  const [manualTokenEmail, setManualTokenEmail] = useState('');
  const [showManualToken, setShowManualToken] = useState(false);
  const [savingTokenProfileId, setSavingTokenProfileId] = useState<string | null>(null);

  // Auto-switch settings state
  const [autoSwitchSettings, setAutoSwitchSettings] = useState<ClaudeAutoSwitchSettings | null>(null);
  const [isLoadingAutoSwitch, setIsLoadingAutoSwitch] = useState(false);

  // CLI accounts state (Codex & Gemini)
  const [cliAccounts, setCLIAccounts] = useState<CLIAccountsDetectionResult | null>(null);
  const [isDetectingCLI, setIsDetectingCLI] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadClaudeProfiles();
      loadAutoSwitchSettings();
      detectCLIAccounts();
    }
  }, [isOpen]);

  useEffect(() => {
    const unsubscribe = window.API.onTerminalOAuthToken(async (info) => {
      if (info.success && info.profileId) {
        await loadClaudeProfiles();
        alert(`Profile authenticated successfully!\n\n${info.email ? `Account: ${info.email}` : 'Authentication complete.'}\n\nYou can now use this profile.`);
      }
    });
    return unsubscribe;
  }, []);

  useEffect(() => {
    const unsubscribe = window.API.onCLIAccountAuth(async (info) => {
      if (info.success) {
        await detectCLIAccounts();
      }
    });
    return unsubscribe;
  }, []);

  const loadClaudeProfiles = async () => {
    setIsLoadingProfiles(true);
    try {
      const result = await window.API.getClaudeProfiles();
      if (result.success && result.data) {
        setClaudeProfiles(result.data.profiles);
        setActiveProfileId(result.data.activeProfileId);
        await loadGlobalClaudeProfiles();
      }
    } catch (err) {
      console.error('Failed to load Claude profiles:', err);
    } finally {
      setIsLoadingProfiles(false);
    }
  };

  const handleAddProfile = async () => {
    if (!newProfileName.trim()) return;
    setIsAddingProfile(true);
    try {
      const profileName = newProfileName.trim();
      const profileSlug = profileName.toLowerCase().replace(/\s+/g, '-');
      const result = await window.API.saveClaudeProfile({
        id: `profile-${Date.now()}`,
        name: profileName,
        configDir: `~/.claude-profiles/${profileSlug}`,
        isDefault: false,
        createdAt: new Date()
      });
      if (result.success && result.data) {
        const initResult = await window.API.initializeClaudeProfile(result.data.id);
        if (initResult.success) {
          await loadClaudeProfiles();
          setNewProfileName('');
          alert(
            `Authenticating "${profileName}"...\n\n` +
            `A browser window will open for you to log in with your Claude account.\n\n` +
            `The authentication will be saved automatically once complete.`
          );
        } else {
          await loadClaudeProfiles();
          alert(`Failed to start authentication: ${initResult.error || 'Please try again.'}`);
        }
      }
    } catch (err) {
      console.error('Failed to add profile:', err);
      alert('Failed to add profile. Please try again.');
    } finally {
      setIsAddingProfile(false);
    }
  };

  const handleDeleteProfile = async (profileId: string) => {
    setDeletingProfileId(profileId);
    try {
      const result = await window.API.deleteClaudeProfile(profileId);
      if (result.success) {
        await loadClaudeProfiles();
      }
    } catch (err) {
      console.error('Failed to delete profile:', err);
    } finally {
      setDeletingProfileId(null);
    }
  };

  const startEditingProfile = (profile: ClaudeProfile) => {
    setEditingProfileId(profile.id);
    setEditingProfileName(profile.name);
  };

  const cancelEditingProfile = () => {
    setEditingProfileId(null);
    setEditingProfileName('');
  };

  const handleRenameProfile = async () => {
    if (!editingProfileId || !editingProfileName.trim()) return;
    try {
      const result = await window.API.renameClaudeProfile(editingProfileId, editingProfileName.trim());
      if (result.success) {
        await loadClaudeProfiles();
      }
    } catch (err) {
      console.error('Failed to rename profile:', err);
    } finally {
      setEditingProfileId(null);
      setEditingProfileName('');
    }
  };

  const handleSetActiveProfile = async (profileId: string) => {
    try {
      const result = await window.API.setActiveClaudeProfile(profileId);
      if (result.success) {
        setActiveProfileId(profileId);
        await loadGlobalClaudeProfiles();
      }
    } catch (err) {
      console.error('Failed to set active profile:', err);
    }
  };

  const handleAuthenticateProfile = async (profileId: string) => {
    setExpandedTokenProfileId(profileId);
    setManualToken('');
    setManualTokenEmail('');
    setShowManualToken(false);
    setAuthenticatingProfileId(null);
  };

  const toggleTokenEntry = (profileId: string) => {
    if (expandedTokenProfileId === profileId) {
      setExpandedTokenProfileId(null);
      setManualToken('');
      setManualTokenEmail('');
      setShowManualToken(false);
    } else {
      const profile = claudeProfiles.find(p => p.id === profileId);
      setExpandedTokenProfileId(profileId);
      setManualToken(profile?.oauthToken || '');
      setManualTokenEmail(profile?.email || '');
      setShowManualToken(false);
    }
  };

  const handleSaveManualToken = async (profileId: string) => {
    if (!manualToken.trim()) return;
    setSavingTokenProfileId(profileId);
    try {
      const result = await window.API.setClaudeProfileToken(
        profileId,
        manualToken.trim(),
        manualTokenEmail.trim() || undefined
      );
      if (result.success) {
        await loadClaudeProfiles();
        setExpandedTokenProfileId(null);
        setManualToken('');
        setManualTokenEmail('');
        setShowManualToken(false);
      } else {
        alert(`Failed to save token: ${result.error || 'Please try again.'}`);
      }
    } catch (err) {
      console.error('Failed to save token:', err);
      alert('Failed to save token. Please try again.');
    } finally {
      setSavingTokenProfileId(null);
    }
  };

  const loadAutoSwitchSettings = async () => {
    setIsLoadingAutoSwitch(true);
    try {
      const result = await window.API.getAutoSwitchSettings();
      if (result.success && result.data) {
        setAutoSwitchSettings(result.data);
      }
    } catch (err) {
      console.error('Failed to load auto-switch settings:', err);
    } finally {
      setIsLoadingAutoSwitch(false);
    }
  };

  const handleUpdateAutoSwitch = async (updates: Partial<ClaudeAutoSwitchSettings>) => {
    setIsLoadingAutoSwitch(true);
    try {
      const result = await window.API.updateAutoSwitchSettings(updates);
      if (result.success) {
        await loadAutoSwitchSettings();
      } else {
        alert(`Failed to update settings: ${result.error || 'Please try again.'}`);
      }
    } catch (err) {
      console.error('Failed to update auto-switch settings:', err);
      alert('Failed to update settings. Please try again.');
    } finally {
      setIsLoadingAutoSwitch(false);
    }
  };

  const detectCLIAccounts = async () => {
    setIsDetectingCLI(true);
    try {
      const result = await window.API.detectCLIAccounts();
      if (result.success && result.data) {
        setCLIAccounts(result.data);
      }
    } catch (err) {
      console.error('Failed to detect CLI accounts:', err);
    } finally {
      setIsDetectingCLI(false);
    }
  };

  const handleCLIImport = async (cli: 'codex' | 'gemini') => {
    try {
      const result = await window.API.importCLICredentials(cli);
      if (result.success) {
        await detectCLIAccounts();
      }
    } catch (err) {
      console.error(`Failed to import ${cli} credentials:`, err);
    }
  };

  const handleCLIStartLogin = async (cli: 'codex' | 'gemini') => {
    try {
      await window.API.startCLILoginTerminal(cli);
    } catch (err) {
      console.error(`Failed to start ${cli} login:`, err);
    }
  };

  const handleCLIRemove = async (cli: 'codex' | 'gemini') => {
    try {
      const result = await window.API.removeCLIAccount(cli);
      if (result.success) {
        await detectCLIAccounts();
      }
    } catch (err) {
      console.error(`Failed to remove ${cli} account:`, err);
    }
  };

  const handleCLIInstall = async (cli: 'codex' | 'gemini') => {
    try {
      const result = await window.API.installCLI(cli);
      if (result.success) {
        await detectCLIAccounts();
      }
    } catch (err) {
      console.error(`Failed to install/update ${cli}:`, err);
    }
  };

  return (
    <div className="space-y-6">
      {/* Claude Accounts Section */}
      <div className="space-y-4">
        <div className="rounded-lg bg-muted/30 border border-border p-4">
          <p className="text-sm text-muted-foreground mb-4">
            {t('integrations.claudeAccountsDescription')}
          </p>

          {isLoadingProfiles ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : claudeProfiles.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-4 text-center mb-4">
              <p className="text-sm text-muted-foreground">{t('integrations.noAccountsYet')}</p>
            </div>
          ) : (
            <div className="space-y-2 mb-4">
              {claudeProfiles.map((profile) => (
                <div
                  key={profile.id}
                  className={cn(
                    "rounded-lg border transition-colors",
                    profile.id === activeProfileId
                      ? "border-primary bg-primary/5"
                      : "border-border bg-background"
                  )}
                >
                  <div className={cn(
                    "flex items-center justify-between p-3",
                    expandedTokenProfileId !== profile.id && "hover:bg-muted/50"
                  )}>
                    <div className="flex items-center gap-3">
                      <div className={cn(
                        "h-7 w-7 rounded-full flex items-center justify-center text-xs font-medium shrink-0",
                        profile.id === activeProfileId
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-muted-foreground"
                      )}>
                        {(editingProfileId === profile.id ? editingProfileName : profile.name).charAt(0).toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        {editingProfileId === profile.id ? (
                          <div className="flex items-center gap-2">
                            <Input
                              value={editingProfileName}
                              onChange={(e) => setEditingProfileName(e.target.value)}
                              className="h-7 text-sm w-40"
                              autoFocus
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') handleRenameProfile();
                                if (e.key === 'Escape') cancelEditingProfile();
                              }}
                            />
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={handleRenameProfile}
                              className="h-7 w-7 text-success hover:text-success hover:bg-success/10"
                            >
                              <Check className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={cancelEditingProfile}
                              className="h-7 w-7 text-muted-foreground hover:text-foreground"
                            >
                              <X className="h-3 w-3" />
                            </Button>
                          </div>
                        ) : (
                          <>
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-sm font-medium text-foreground">{profile.name}</span>
                              {profile.isDefault && (
                                <span className="text-xs bg-muted px-1.5 py-0.5 rounded">{t('integrations.default')}</span>
                              )}
                              {profile.id === activeProfileId && (
                                <span className="text-xs bg-primary/20 text-primary px-1.5 py-0.5 rounded flex items-center gap-1">
                                  <Star className="h-3 w-3" />
                                  {t('integrations.active')}
                                </span>
                              )}
                              {(profile.oauthToken || (profile.isDefault && profile.configDir)) ? (
                                <span className="text-xs bg-success/20 text-success px-1.5 py-0.5 rounded flex items-center gap-1">
                                  <Check className="h-3 w-3" />
                                  {t('integrations.authenticated')}
                                </span>
                              ) : (
                                <span className="text-xs bg-warning/20 text-warning px-1.5 py-0.5 rounded">
                                  {t('integrations.needsAuth')}
                                </span>
                              )}
                            </div>
                            {profile.email && (
                              <span className="text-xs text-muted-foreground">{profile.email}</span>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                    {editingProfileId !== profile.id && (
                      <div className="flex items-center gap-1">
                        {!(profile.oauthToken || (profile.isDefault && profile.configDir)) ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleAuthenticateProfile(profile.id)}
                            disabled={authenticatingProfileId === profile.id}
                            className="gap-1 h-7 text-xs"
                          >
                            {authenticatingProfileId === profile.id ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <LogIn className="h-3 w-3" />
                            )}
                            {t('integrations.authenticate')}
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleAuthenticateProfile(profile.id)}
                            disabled={authenticatingProfileId === profile.id}
                            className="h-7 w-7 text-muted-foreground hover:text-foreground"
                            title="Re-authenticate profile"
                          >
                            {authenticatingProfileId === profile.id ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <RefreshCw className="h-3 w-3" />
                            )}
                          </Button>
                        )}
                        {profile.id !== activeProfileId && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleSetActiveProfile(profile.id)}
                            className="gap-1 h-7 text-xs"
                          >
                            <Check className="h-3 w-3" />
                            {t('integrations.setActive')}
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => toggleTokenEntry(profile.id)}
                          className="h-7 w-7 text-muted-foreground hover:text-foreground"
                          title={expandedTokenProfileId === profile.id ? "Hide token entry" : "Enter token manually"}
                        >
                          {expandedTokenProfileId === profile.id ? (
                            <ChevronDown className="h-3 w-3" />
                          ) : (
                            <ChevronRight className="h-3 w-3" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => startEditingProfile(profile)}
                          className="h-7 w-7 text-muted-foreground hover:text-foreground"
                          title="Rename profile"
                        >
                          <Pencil className="h-3 w-3" />
                        </Button>
                        {!profile.isDefault && (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDeleteProfile(profile.id)}
                            disabled={deletingProfileId === profile.id}
                            className="h-7 w-7 text-destructive hover:text-destructive hover:bg-destructive/10"
                            title="Delete profile"
                          >
                            {deletingProfileId === profile.id ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <Trash2 className="h-3 w-3" />
                            )}
                          </Button>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Expanded token entry section */}
                  {expandedTokenProfileId === profile.id && (
                    <div className="px-3 pb-3 pt-0 border-t border-border/50 mt-0">
                      <div className="bg-muted/30 rounded-lg p-3 mt-3 space-y-3">
                        <div className="flex items-center justify-between">
                          <Label className="text-xs font-medium text-muted-foreground">
                            {t('integrations.manualTokenEntry')}
                          </Label>
                          <span className="text-xs text-muted-foreground">
                            {t('integrations.runSetupToken')}
                          </span>
                        </div>

                        <div className="space-y-2">
                          <div className="relative">
                            <Input
                              type={showManualToken ? 'text' : 'password'}
                              placeholder={t('integrations.tokenPlaceholder')}
                              value={manualToken}
                              onChange={(e) => setManualToken(e.target.value)}
                              className="pr-10 font-mono text-xs h-8"
                            />
                            <button
                              type="button"
                              onClick={() => setShowManualToken(!showManualToken)}
                              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                            >
                              {showManualToken ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                            </button>
                          </div>

                          <Input
                            type="email"
                            placeholder={t('integrations.emailPlaceholder')}
                            value={manualTokenEmail}
                            onChange={(e) => setManualTokenEmail(e.target.value)}
                            className="text-xs h-8"
                          />
                        </div>

                        <div className="flex items-center justify-end gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => toggleTokenEntry(profile.id)}
                            className="h-7 text-xs"
                          >
                            {tCommon('buttons.cancel')}
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => handleSaveManualToken(profile.id)}
                            disabled={!manualToken.trim() || savingTokenProfileId === profile.id}
                            className="h-7 text-xs gap-1"
                          >
                            {savingTokenProfileId === profile.id ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <Check className="h-3 w-3" />
                            )}
                            {t('integrations.saveToken')}
                          </Button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Add new account */}
          <div className="flex items-center gap-2">
            <Input
              placeholder={t('integrations.accountNamePlaceholder')}
              value={newProfileName}
              onChange={(e) => setNewProfileName(e.target.value)}
              className="flex-1 h-8 text-sm"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newProfileName.trim()) {
                  handleAddProfile();
                }
              }}
            />
            <Button
              onClick={handleAddProfile}
              disabled={!newProfileName.trim() || isAddingProfile}
              size="sm"
              className="gap-1 shrink-0"
            >
              {isAddingProfile ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Plus className="h-3 w-3" />
              )}
              {tCommon('buttons.add')}
            </Button>
          </div>
        </div>
      </div>

      {/* Auto-Switch Settings Section */}
      {claudeProfiles.length > 1 && (
        <div className="space-y-4 pt-6 border-t border-border">
          <div className="flex items-center gap-2">
            <RefreshCw className="h-4 w-4 text-muted-foreground" />
            <h4 className="text-sm font-semibold text-foreground">{t('integrations.autoSwitching')}</h4>
          </div>

          <div className="rounded-lg bg-muted/30 border border-border p-4 space-y-4">
            <p className="text-sm text-muted-foreground">
              {t('integrations.autoSwitchingDescription')}
            </p>

            {/* Master toggle */}
            <div className="flex items-center justify-between">
              <div>
                <Label className="text-sm font-medium">{t('integrations.enableAutoSwitching')}</Label>
                <p className="text-xs text-muted-foreground mt-1">
                  {t('integrations.masterSwitch')}
                </p>
              </div>
              <Switch
                checked={autoSwitchSettings?.enabled ?? false}
                onCheckedChange={(enabled) => handleUpdateAutoSwitch({ enabled })}
                disabled={isLoadingAutoSwitch}
              />
            </div>

            {autoSwitchSettings?.enabled && (
              <>
                {/* Proactive Monitoring Section */}
                <div className="pl-6 space-y-4 pt-2 border-l-2 border-primary/20">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-sm font-medium flex items-center gap-2">
                        <Activity className="h-3.5 w-3.5" />
                        {t('integrations.proactiveMonitoring')}
                      </Label>
                      <p className="text-xs text-muted-foreground mt-1">
                        {t('integrations.proactiveDescription')}
                      </p>
                    </div>
                    <Switch
                      checked={autoSwitchSettings?.proactiveSwapEnabled ?? true}
                      onCheckedChange={(value) => handleUpdateAutoSwitch({ proactiveSwapEnabled: value })}
                      disabled={isLoadingAutoSwitch}
                    />
                  </div>

                  {autoSwitchSettings?.proactiveSwapEnabled && (
                    <>
                      <div className="space-y-2">
                        <Label className="text-sm">{t('integrations.checkUsageEvery')}</Label>
                        <select
                          className="w-full px-3 py-2 bg-background border border-input rounded-md text-sm"
                          value={autoSwitchSettings?.usageCheckInterval ?? 30000}
                          onChange={(e) => handleUpdateAutoSwitch({ usageCheckInterval: parseInt(e.target.value) })}
                          disabled={isLoadingAutoSwitch}
                        >
                          <option value={15000}>{t('integrations.seconds15')}</option>
                          <option value={30000}>{t('integrations.seconds30')}</option>
                          <option value={60000}>{t('integrations.minute1')}</option>
                          <option value={0}>{t('integrations.disabled')}</option>
                        </select>
                      </div>

                      <div className="space-y-2">
                        <div className="flex items-center justify-between">
                          <Label className="text-sm">{t('integrations.sessionThreshold')}</Label>
                          <span className="text-sm font-mono">{autoSwitchSettings?.sessionThreshold ?? 95}%</span>
                        </div>
                        <input
                          type="range"
                          min="70"
                          max="99"
                          step="1"
                          value={autoSwitchSettings?.sessionThreshold ?? 95}
                          onChange={(e) => handleUpdateAutoSwitch({ sessionThreshold: parseInt(e.target.value) })}
                          disabled={isLoadingAutoSwitch}
                          className="w-full"
                        />
                        <p className="text-xs text-muted-foreground">
                          {t('integrations.sessionThresholdDescription')}
                        </p>
                      </div>

                      <div className="space-y-2">
                        <div className="flex items-center justify-between">
                          <Label className="text-sm">{t('integrations.weeklyThreshold')}</Label>
                          <span className="text-sm font-mono">{autoSwitchSettings?.weeklyThreshold ?? 99}%</span>
                        </div>
                        <input
                          type="range"
                          min="70"
                          max="99"
                          step="1"
                          value={autoSwitchSettings?.weeklyThreshold ?? 99}
                          onChange={(e) => handleUpdateAutoSwitch({ weeklyThreshold: parseInt(e.target.value) })}
                          disabled={isLoadingAutoSwitch}
                          className="w-full"
                        />
                        <p className="text-xs text-muted-foreground">
                          {t('integrations.weeklyThresholdDescription')}
                        </p>
                      </div>
                    </>
                  )}
                </div>

                {/* Reactive Recovery Section */}
                <div className="pl-6 space-y-4 pt-2 border-l-2 border-orange-500/20">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-sm font-medium flex items-center gap-2">
                        <AlertCircle className="h-3.5 w-3.5" />
                        {t('integrations.reactiveRecovery')}
                      </Label>
                      <p className="text-xs text-muted-foreground mt-1">
                        {t('integrations.reactiveDescription')}
                      </p>
                    </div>
                    <Switch
                      checked={autoSwitchSettings?.autoSwitchOnRateLimit ?? false}
                      onCheckedChange={(value) => handleUpdateAutoSwitch({ autoSwitchOnRateLimit: value })}
                      disabled={isLoadingAutoSwitch}
                    />
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {/* Other CLI Accounts */}
      <div className="space-y-4 pt-6 border-t border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <h4 className="text-sm font-semibold text-foreground">{t('integrations.cliAccounts')}</h4>
          </div>
          <Button variant="outline" size="sm" onClick={detectCLIAccounts} disabled={isDetectingCLI}>
            {isDetectingCLI ? (
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3 mr-1" />
            )}
            {t('integrations.refreshStatus')}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">{t('integrations.cliAccountsDescription')}</p>
        <div className="space-y-2">
          <CLIAccountCard
            cli="codex"
            status={cliAccounts?.codex ?? null}
            isLoading={isDetectingCLI}
            onImport={() => handleCLIImport('codex')}
            onStartLogin={() => handleCLIStartLogin('codex')}
            onRemove={() => handleCLIRemove('codex')}
            onInstall={() => handleCLIInstall('codex')}
            onRefresh={detectCLIAccounts}
          />
          <CLIAccountCard
            cli="gemini"
            status={cliAccounts?.gemini ?? null}
            isLoading={isDetectingCLI}
            onImport={() => handleCLIImport('gemini')}
            onStartLogin={() => handleCLIStartLogin('gemini')}
            onRemove={() => handleCLIRemove('gemini')}
            onInstall={() => handleCLIInstall('gemini')}
            onRefresh={detectCLIAccounts}
          />
        </div>
      </div>
    </div>
  );
}
