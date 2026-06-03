/**
 * Monaco Editor page for file editing
 */

import { useState, useEffect, useCallback } from 'react';
import Editor from '@monaco-editor/react';
import { ChevronRight, ChevronDown, File, Folder, Save, X, Eye, Code, Columns, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import type { FileNode } from '../shared/types';

interface FileTab {
  path: string;
  name: string;
  content: string;
  isDirty: boolean;
  language: string;
}

interface EditorPageProps {
  projectPath?: string;
}

// Detect language from file extension
function detectLanguage(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const languageMap: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    py: 'python',
    rb: 'ruby',
    rs: 'rust',
    go: 'go',
    java: 'java',
    c: 'c',
    cpp: 'cpp',
    h: 'c',
    hpp: 'cpp',
    cs: 'csharp',
    php: 'php',
    swift: 'swift',
    kt: 'kotlin',
    scala: 'scala',
    r: 'r',
    sql: 'sql',
    html: 'html',
    css: 'css',
    scss: 'scss',
    less: 'less',
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    xml: 'xml',
    md: 'markdown',
    sh: 'shell',
    bash: 'shell',
    zsh: 'shell',
    dockerfile: 'dockerfile',
    makefile: 'makefile',
    toml: 'toml',
    ini: 'ini',
    env: 'dotenv',
  };
  return languageMap[ext] || 'plaintext';
}

// File tree component with lazy loading
function FileTree({
  nodes,
  onFileSelect,
  onLoadChildren,
  level = 0,
}: {
  nodes: FileNode[];
  onFileSelect: (path: string, name: string) => void;
  onLoadChildren: (path: string) => Promise<FileNode[]>;
  level?: number;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loadedChildren, setLoadedChildren] = useState<Record<string, FileNode[]>>({});
  const [loading, setLoading] = useState<Set<string>>(new Set());

  const toggleExpand = async (path: string) => {
    if (expanded.has(path)) {
      // Collapse
      const next = new Set(expanded);
      next.delete(path);
      setExpanded(next);
    } else {
      // Expand - load children if not already loaded
      if (!loadedChildren[path] && !loading.has(path)) {
        setLoading((prev) => new Set(prev).add(path));
        try {
          const children = await onLoadChildren(path);
          setLoadedChildren((prev) => ({ ...prev, [path]: children }));
        } catch (error) {
          console.error('Failed to load directory:', error);
        } finally {
          setLoading((prev) => {
            const next = new Set(prev);
            next.delete(path);
            return next;
          });
        }
      }
      const next = new Set(expanded);
      next.add(path);
      setExpanded(next);
    }
  };

  return (
    <div className="text-sm">
      {nodes.map((node) => (
        <div key={node.path}>
          <div
            className="flex items-center gap-1 px-2 py-1 hover:bg-accent cursor-pointer rounded"
            style={{ paddingLeft: `${level * 12 + 8}px` }}
            onClick={() => {
              if (node.isDirectory) {
                toggleExpand(node.path);
              } else {
                onFileSelect(node.path, node.name);
              }
            }}
          >
            {node.isDirectory ? (
              <>
                {loading.has(node.path) ? (
                  <span className="h-4 w-4 animate-spin text-muted-foreground">⟳</span>
                ) : expanded.has(node.path) ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
                <Folder className="h-4 w-4 text-blue-400" />
              </>
            ) : (
              <>
                <span className="w-4" />
                <File className="h-4 w-4 text-muted-foreground" />
              </>
            )}
            <span className="truncate">{node.name}</span>
          </div>
          {node.isDirectory && expanded.has(node.path) && loadedChildren[node.path] && (
            <FileTree
              nodes={loadedChildren[node.path]}
              onFileSelect={onFileSelect}
              onLoadChildren={onLoadChildren}
              level={level + 1}
            />
          )}
        </div>
      ))}
    </div>
  );
}

type ViewMode = 'editor' | 'preview' | 'split';

function PreviewPane({ content, language }: { content: string; language: string }) {
  if (language === 'html') {
    return (
      <iframe
        srcDoc={content}
        sandbox="allow-scripts"
        title="HTML Preview"
        className="w-full h-full border-0 bg-white"
      />
    );
  }
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function EditorPage({ projectPath }: EditorPageProps) {
  const { t } = useTranslation(['common']);
  const [files, setFiles] = useState<FileNode[]>([]);
  const [tabs, setTabs] = useState<FileTab[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('editor');
  const [isSaving, setIsSaving] = useState(false);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+S or Cmd+S to save
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        if (activeTab) {
          const tab = tabs.find((t) => t.path === activeTab);
          if (tab && tab.isDirty) {
            saveFile(activeTab);
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeTab, tabs]);

  // Load directory listing
  useEffect(() => {
    if (!projectPath) return;

    const loadFiles = async () => {
      try {
        const result = await window.API.listDirectory(projectPath);
        if (result.success && result.data) {
          // API returns {path, entries, parent} - we need the entries array
          const entries = Array.isArray(result.data) ? result.data : result.data.entries || [];
          // Convert to FileNode format if needed
          const fileNodes = entries.map((entry: FileNode) => ({
            name: entry.name,
            path: entry.path,
            isDirectory: entry.type === 'directory' || entry.isDirectory,
            children: (entry.type === 'directory' || entry.isDirectory) ? [] : undefined,
          }));
          setFiles(fileNodes);
        }
      } catch (error) {
        console.error('Failed to load files:', error);
      }
    };

    loadFiles();
  }, [projectPath]);

  // Open file in new tab
  const openFile = useCallback(async (path: string, name: string) => {
    // Check if already open
    const existing = tabs.find((t) => t.path === path);
    if (existing) {
      setActiveTab(path);
      return;
    }

    setIsLoading(true);
    try {
      const result = await window.API.readFile(path);
      if (result.success && result.data) {
        const newTab: FileTab = {
          path,
          name,
          content: result.data.content || '',
          isDirty: false,
          language: result.data.language || detectLanguage(name),
        };
        setTabs((prev) => [...prev, newTab]);
        setActiveTab(path);
      }
    } catch (error) {
      console.error('Failed to read file:', error);
    } finally {
      setIsLoading(false);
    }
  }, [tabs]);

  // Load directory children (for lazy loading in file tree)
  const loadChildren = useCallback(async (dirPath: string): Promise<FileNode[]> => {
    try {
      const result = await window.API.listDirectory(dirPath);
      if (result.success && result.data) {
        const entries = Array.isArray(result.data) ? result.data : result.data.entries || [];
        return entries.map((entry: FileNode) => ({
          name: entry.name,
          path: entry.path,
          isDirectory: entry.type === 'directory' || entry.isDirectory,
          children: (entry.type === 'directory' || entry.isDirectory) ? [] : undefined,
        }));
      }
    } catch (error) {
      console.error('Failed to load directory:', error);
    }
    return [];
  }, []);

  // Close tab
  const closeTab = (path: string) => {
    setTabs((prev) => prev.filter((t) => t.path !== path));
    if (activeTab === path) {
      const remaining = tabs.filter((t) => t.path !== path);
      setActiveTab(remaining.length > 0 ? remaining[remaining.length - 1].path : null);
    }
  };

  // Update content
  const handleEditorChange = (value: string | undefined) => {
    if (!activeTab || value === undefined) return;
    setTabs((prev) =>
      prev.map((t) =>
        t.path === activeTab ? { ...t, content: value, isDirty: true } : t
      )
    );
  };

  // Save file
  const saveFile = async (path: string) => {
    const tab = tabs.find((t) => t.path === path);
    if (!tab || isSaving) return;

    setIsSaving(true);
    try {
      const result = await window.API.writeFile(path, tab.content);
      if (result.success) {
        // Mark as not dirty
        setTabs((prev) =>
          prev.map((t) => (t.path === path ? { ...t, isDirty: false } : t))
        );
        console.log('File saved successfully:', path);
      } else {
        console.error('Failed to save file:', result.error);
        alert(`Failed to save file: ${result.error}`);
      }
    } catch (error) {
      console.error('Error saving file:', error);
      alert(`Error saving file: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsSaving(false);
    }
  };

  const activeTabData = tabs.find((t) => t.path === activeTab);
  const isMarkdownFile = activeTabData?.language === 'markdown';
  const isHtmlFile = activeTabData?.language === 'html';
  const isPreviewable = isMarkdownFile || isHtmlFile;

  // Reset view mode to editor when switching to non-previewable files
  useEffect(() => {
    if (activeTabData && !isPreviewable && viewMode !== 'editor') {
      setViewMode('editor');
    }
  }, [activeTabData, isPreviewable, viewMode]);

  if (!projectPath) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground">
        Select a project to edit files
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* File tree sidebar */}
      <div className="w-64 border-r border-border overflow-auto bg-card/50">
        <div className="p-2 border-b border-border">
          <h3 className="font-medium text-sm">Explorer</h3>
        </div>
        <FileTree nodes={files} onFileSelect={openFile} onLoadChildren={loadChildren} />
      </div>

      {/* Editor area */}
      <div className="flex-1 flex flex-col">
        {/* Tab bar */}
        {tabs.length > 0 && (
          <div className="flex border-b border-border bg-card/50 overflow-x-auto">
            {tabs.map((tab) => (
              <div
                key={tab.path}
                className={`flex items-center gap-2 px-3 py-2 border-r border-border cursor-pointer text-sm ${
                  activeTab === tab.path
                    ? 'bg-background text-foreground'
                    : 'text-muted-foreground hover:bg-accent'
                }`}
                onClick={() => setActiveTab(tab.path)}
              >
                <span className="truncate max-w-[150px]">
                  {tab.isDirty && <span className="text-primary mr-1">*</span>}
                  {tab.name}
                </span>
                <button
                  className="hover:bg-accent rounded p-0.5"
                  onClick={(e) => {
                    e.stopPropagation();
                    closeTab(tab.path);
                  }}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* View Mode Toolbar (for previewable files: markdown, html) */}
        {activeTabData && isPreviewable && (
          <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-card/50">
            <span className="text-xs text-muted-foreground mr-2">View:</span>
            <button
              className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${
                viewMode === 'editor'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-accent'
              }`}
              onClick={() => setViewMode('editor')}
            >
              <Code className="h-3 w-3" />
              Edit
            </button>
            <button
              className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${
                viewMode === 'preview'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-accent'
              }`}
              onClick={() => setViewMode('preview')}
            >
              <Eye className="h-3 w-3" />
              Preview
            </button>
            <button
              className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${
                viewMode === 'split'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-accent'
              }`}
              onClick={() => setViewMode('split')}
            >
              <Columns className="h-3 w-3" />
              Split
            </button>
            {isHtmlFile && activeTabData && projectPath && (
              <button
                className="flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors bg-muted text-muted-foreground hover:bg-accent ml-2"
                onClick={() => {
                  const params = new URLSearchParams({ path: activeTabData.path, root: projectPath });
                  window.open(`/api/files/serve?${params.toString()}`, '_blank');
                }}
                title={t('common:buttons.openInBrowser')}
              >
                <ExternalLink className="h-3 w-3" />
                {t('common:buttons.openInBrowser')}
              </button>
            )}
          </div>
        )}

        {/* Editor */}
        <div className="flex-1 overflow-hidden">
          {activeTabData ? (
            viewMode === 'editor' || !isPreviewable ? (
              // Editor only mode
              <Editor
                height="100%"
                language={activeTabData.language}
                value={activeTabData.content}
                onChange={handleEditorChange}
                theme="vs-dark"
                options={{
                  minimap: { enabled: true },
                  fontSize: 14,
                  lineNumbers: 'on',
                  wordWrap: 'on',
                  automaticLayout: true,
                  scrollBeyondLastLine: false,
                }}
                loading={
                  <div className="flex items-center justify-center h-full text-muted-foreground">
                    Loading editor...
                  </div>
                }
              />
            ) : viewMode === 'preview' ? (
              // Preview only mode (markdown / html)
              <div className="h-full overflow-auto p-6">
                <PreviewPane content={activeTabData.content} language={activeTabData.language} />
              </div>
            ) : (
              // Split view mode (markdown / html)
              <div className="flex h-full">
                <div className="flex-1 border-r border-border">
                  <Editor
                    height="100%"
                    language={activeTabData.language}
                    value={activeTabData.content}
                    onChange={handleEditorChange}
                    theme="vs-dark"
                    options={{
                      minimap: { enabled: false },
                      fontSize: 14,
                      lineNumbers: 'on',
                      wordWrap: 'on',
                      automaticLayout: true,
                      scrollBeyondLastLine: false,
                    }}
                    loading={
                      <div className="flex items-center justify-center h-full text-muted-foreground">
                        Loading editor...
                      </div>
                    }
                  />
                </div>
                <div className="flex-1 overflow-auto p-6">
                  <PreviewPane content={activeTabData.content} language={activeTabData.language} />
                </div>
              </div>
            )
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground">
              {isLoading ? 'Loading...' : 'Select a file to edit'}
            </div>
          )}
        </div>

        {/* Status bar */}
        {activeTabData && (
          <div className="flex items-center justify-between px-3 py-1 border-t border-border bg-card/50 text-xs text-muted-foreground">
            <span>{activeTabData.path}</span>
            <div className="flex items-center gap-2">
              <span>{activeTabData.language}</span>
              {activeTabData.isDirty && (
                <button
                  className="flex items-center gap-1 hover:text-foreground"
                  onClick={() => saveFile(activeTabData.path)}
                >
                  <Save className="h-3 w-3" />
                  Save
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
