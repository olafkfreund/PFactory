/**
 * Settings module barrel export
 * Provides clean import paths for settings components
 */

export {
  ProjectSettingsPage,
  PreferencesPage,
  type AppSection,
  type ProjectSettingsPageProps,
  type PreferencesPageProps,
} from './AppSettings';
export { ThemeSettings } from './ThemeSettings';
export { ThemeSelector } from './ThemeSelector';
export { GeneralSettings } from './GeneralSettings';
export { IntegrationSettings } from './IntegrationSettings';
export { AdvancedSettings } from './AdvancedSettings';
export { SettingsSection } from './SettingsSection';
export { useSettings } from './hooks/useSettings';
