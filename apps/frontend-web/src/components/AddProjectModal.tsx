import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { FolderOpen, Search, Loader2, GitBranch, Package, FileCode, CheckCircle, FileText, FolderPlus } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { ScrollArea } from './ui/scroll-area';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from './ui/dialog';
import { cn } from '../lib/utils';
import { addProject, addProjectFromGitUrl } from '../stores/project-store';
import type { Project } from '../shared/types';

interface DiscoveredProject {
  name: string;
  path: string;
  has_git: boolean;
  has_package_json: boolean;
  has_requirements: boolean;
  has_pfactory: boolean;
  has_claude_md: boolean;
}

interface AddProjectModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onProjectAdded?: (project: Project, needsInit: boolean) => void;
}

// Default projects folder - can be customized
const DEFAULT_PROJECTS_FOLDER = '/home';

export function AddProjectModal({ open, onOpenChange, onProjectAdded }: AddProjectModalProps) {
  const { t } = useTranslation('dialogs');
  const [projectsFolder, setProjectsFolder] = useState(DEFAULT_PROJECTS_FOLDER);
  const [discoveredProjects, setDiscoveredProjects] = useState<DiscoveredProject[]>([]);
  const [selectedProject, setSelectedProject] = useState<DiscoveredProject | null>(null);
  const [customPath, setCustomPath] = useState('');
  const [isScanning, setIsScanning] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [useCustomPath, setUseCustomPath] = useState(false);
  const [showClaudeReadyOnly, setShowClaudeReadyOnly] = useState(false);
  const [createdDirPath, setCreatedDirPath] = useState<string | null>(null);
  // Epic #82 PR-B — clone-from-Git-URL mode. Mutually exclusive with the
  // local-folder mode above; toggled via the segmented button at the top
  // of the modal body.
  const [mode, setMode] = useState<'local' | 'clone'>('local');
  const [gitUrl, setGitUrl] = useState('');
  const [gitBranch, setGitBranch] = useState('');
  const [gitName, setGitName] = useState('');

  // Filter and sort projects - Claude-ready projects first
  const sortedProjects = [...discoveredProjects].sort((a, b) => {
    // Projects with CLAUDE.md come first
    if (a.has_claude_md && !b.has_claude_md) return -1;
    if (!a.has_claude_md && b.has_claude_md) return 1;
    // Then by name
    return a.name.localeCompare(b.name);
  });

  const filteredProjects = showClaudeReadyOnly
    ? sortedProjects.filter(p => p.has_claude_md)
    : sortedProjects;

  const claudeReadyCount = discoveredProjects.filter(p => p.has_claude_md).length;

  // Scan for projects when folder changes
  const scanProjects = useCallback(async () => {
    if (!projectsFolder.trim()) return;

    setIsScanning(true);
    setError(null);
    setDiscoveredProjects([]);
    setSelectedProject(null);

    try {
      const result = await window.API.discoverProjects(projectsFolder, 2);
      if (result.success && result.data) {
        setDiscoveredProjects(result.data);
        if (result.data.length === 0) {
          setError('No projects found in this folder. Try a different path or enter a custom path below.');
        }
      } else {
        setError(result.error || 'Failed to scan for projects');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to scan for projects');
    } finally {
      setIsScanning(false);
    }
  }, [projectsFolder]);

  // Scan on modal open
  useEffect(() => {
    if (open) {
      setCustomPath('');
      setSelectedProject(null);
      setUseCustomPath(false);
      setShowClaudeReadyOnly(false);
      setError(null);
      setCreatedDirPath(null);
      setMode('local');
      setGitUrl('');
      setGitBranch('');
      setGitName('');
      scanProjects();
    }
  }, [open, scanProjects]);

  const handleAddProject = async () => {
    setIsAdding(true);
    setError(null);

    try {
      let project: Project | null;
      let resolvedPath: string | null = null;

      if (mode === 'clone') {
        const url = gitUrl.trim();
        if (!url) {
          setError(t('addProject.gitUrlLabel', 'Git repository URL') + ' is required');
          setIsAdding(false);
          return;
        }
        project = await addProjectFromGitUrl(
          url,
          gitBranch.trim() || undefined,
          gitName.trim() || undefined,
        );
        if (project) {
          resolvedPath = project.path;
        }
      } else {
        const path = useCustomPath ? customPath.trim() : selectedProject?.path;
        if (!path) {
          setError('Please select a project or enter a custom path');
          setIsAdding(false);
          return;
        }
        project = await addProject(path);
        resolvedPath = path;
      }

      // Common post-add handling (main-branch detection + onProjectAdded
      // callback). Skipped on null project; the catch below logs.
      if (project && resolvedPath) {
        // Try to detect and save main branch
        try {
          const mainBranchResult = await window.API.detectMainBranch(resolvedPath);
          if (mainBranchResult.success && mainBranchResult.data) {
            await window.API.updateProjectSettings(project.id, {
              mainBranch: mainBranchResult.data,
            });
          }
        } catch {
          // Non-fatal - main branch can be set later
        }
        onProjectAdded?.(project, !project.autoBuildPath);
        if (project.createdDirectory) {
          // Show info message briefly before closing
          setCreatedDirPath(project.path);
          setTimeout(() => {
            setCreatedDirPath(null);
            onOpenChange(false);
          }, 3000);
        } else {
          onOpenChange(false);
        }
      } else {
        setError(
          mode === 'clone'
            ? 'Failed to clone the repository. Check the URL and try again.'
            : 'Failed to add project. Please check the path is valid.',
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add project');
    } finally {
      setIsAdding(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !isAdding && !isScanning) {
      if (e.target instanceof HTMLInputElement && e.target.id === 'projects-folder') {
        scanProjects();
      } else {
        handleAddProject();
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            <div className="flex items-center gap-2">
              <FolderOpen className="h-5 w-5" />
              {t('addProject.title', 'Add Project')}
            </div>
          </DialogTitle>
          <DialogDescription>
            Select a project from your projects folder or enter a custom path.
          </DialogDescription>
        </DialogHeader>

        <div className="py-4 space-y-4">
          {/* Mode toggle — local folder vs clone from Git URL (#82 PR-B) */}
          <div className="flex rounded-lg border border-border p-1 bg-muted/30">
            <button
              type="button"
              onClick={() => setMode('local')}
              className={cn(
                'flex-1 text-sm font-medium rounded-md px-3 py-1.5 transition-colors',
                mode === 'local'
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {t('addProject.modeLocal', 'Local folder')}
            </button>
            <button
              type="button"
              onClick={() => setMode('clone')}
              className={cn(
                'flex-1 text-sm font-medium rounded-md px-3 py-1.5 transition-colors',
                mode === 'clone'
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {t('addProject.modeClone', 'Clone from Git URL')}
            </button>
          </div>

          {mode === 'clone' && (
            <div className="space-y-3">
              <div className="space-y-2">
                <Label htmlFor="git-url">
                  {t('addProject.gitUrlLabel', 'Git repository URL')}
                </Label>
                <Input
                  id="git-url"
                  placeholder={t('addProject.gitUrlPlaceholder', 'https://github.com/owner/repo.git')}
                  value={gitUrl}
                  onChange={(e) => setGitUrl(e.target.value)}
                  onKeyDown={handleKeyDown}
                  autoFocus
                />
                <p className="text-xs text-muted-foreground">
                  {t(
                    'addProject.gitUrlHelp',
                    'Portal clones into the workspace root (~/.pfactory/workspaces/ on laptop, the PVC on K8s).',
                  )}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-2">
                  <Label htmlFor="git-branch">
                    {t('addProject.gitBranchLabel', 'Branch (optional)')}
                  </Label>
                  <Input
                    id="git-branch"
                    placeholder={t('addProject.gitBranchPlaceholder', 'main')}
                    value={gitBranch}
                    onChange={(e) => setGitBranch(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="git-name">
                    {t('addProject.gitNameLabel', 'Display name (optional)')}
                  </Label>
                  <Input
                    id="git-name"
                    placeholder={t('addProject.gitNamePlaceholder', 'Defaults to repo basename')}
                    value={gitName}
                    onChange={(e) => setGitName(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                </div>
              </div>
            </div>
          )}

          {mode === 'local' && (
          <>
          {/* Projects folder input */}
          <div className="space-y-2">
            <Label htmlFor="projects-folder">Projects Folder</Label>
            <div className="flex gap-2">
              <Input
                id="projects-folder"
                placeholder="/home/user/projects"
                value={projectsFolder}
                onChange={(e) => setProjectsFolder(e.target.value)}
                onKeyDown={handleKeyDown}
              />
              <Button
                variant="outline"
                onClick={scanProjects}
                disabled={isScanning || !projectsFolder.trim()}
              >
                {isScanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              </Button>
            </div>
          </div>

          {/* Discovered projects list */}
          {!useCustomPath && discoveredProjects.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>
                  Available Projects ({filteredProjects.length})
                  {claudeReadyCount > 0 && (
                    <span className="ml-2 text-xs text-blue-500">
                      ({claudeReadyCount} Claude-ready)
                    </span>
                  )}
                </Label>
                {claudeReadyCount > 0 && (
                  <Button
                    variant={showClaudeReadyOnly ? "default" : "outline"}
                    size="sm"
                    onClick={() => setShowClaudeReadyOnly(!showClaudeReadyOnly)}
                    className="h-6 text-xs"
                  >
                    <FileText className="h-3 w-3 mr-1" />
                    {showClaudeReadyOnly ? "Show All" : "CLAUDE.md Only"}
                  </Button>
                )}
              </div>
              <ScrollArea className="h-[200px] rounded-md border">
                <div className="p-2 space-y-1">
                  {filteredProjects.map((proj) => (
                    <button
                      key={proj.path}
                      onClick={() => setSelectedProject(proj)}
                      className={cn(
                        'w-full flex items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors',
                        'hover:bg-accent/50',
                        selectedProject?.path === proj.path && 'bg-accent border border-primary'
                      )}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm truncate">{proj.name}</span>
                          {proj.has_pfactory && (
                            <CheckCircle className="h-3 w-3 text-green-500 shrink-0" />
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground truncate">{proj.path}</p>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {proj.has_claude_md && <span title="Has CLAUDE.md"><FileText className="h-3 w-3 text-blue-500" /></span>}
                        {proj.has_git && <span title="Git repository"><GitBranch className="h-3 w-3 text-muted-foreground" /></span>}
                        {proj.has_package_json && <span title="Node.js project"><Package className="h-3 w-3 text-muted-foreground" /></span>}
                        {proj.has_requirements && <span title="Python project"><FileCode className="h-3 w-3 text-muted-foreground" /></span>}
                      </div>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}

          {/* Loading state */}
          {isScanning && (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin mr-2" />
              Scanning for projects...
            </div>
          )}

          {/* Toggle for custom path */}
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="use-custom"
              checked={useCustomPath}
              onChange={(e) => {
                setUseCustomPath(e.target.checked);
                if (e.target.checked) setSelectedProject(null);
              }}
              className="h-4 w-4 rounded"
            />
            <Label htmlFor="use-custom" className="text-sm font-normal cursor-pointer">
              Enter custom path instead
            </Label>
          </div>

          {/* Custom path input */}
          {useCustomPath && (
            <div className="space-y-2">
              <Label htmlFor="custom-path">Custom Project Path</Label>
              <Input
                id="custom-path"
                placeholder="/home/user/projects/my-project"
                value={customPath}
                onChange={(e) => setCustomPath(e.target.value)}
                onKeyDown={handleKeyDown}
                autoFocus
              />
            </div>
          )}
          </>
          )}

          {/* Directory created info */}
          {createdDirPath && (
            <div className="text-sm text-green-700 dark:text-green-400 bg-green-500/10 rounded-lg p-3 flex items-center gap-2">
              <FolderPlus className="h-4 w-4 shrink-0" />
              <span>Created new directory: <strong>{createdDirPath}</strong></span>
            </div>
          )}

          {/* Error message */}
          {error && !isScanning && (
            <div className="text-sm text-destructive bg-destructive/10 rounded-lg p-3">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isAdding}>
            Cancel
          </Button>
          <Button
            onClick={handleAddProject}
            disabled={
              isAdding ||
              isScanning ||
              (mode === 'clone'
                ? !gitUrl.trim()
                : (!selectedProject && !useCustomPath) ||
                  (useCustomPath && !customPath.trim()))
            }
          >
            {isAdding
              ? (mode === 'clone' ? t('addProject.cloning', 'Cloning…') : 'Adding...')
              : 'Add Project'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
