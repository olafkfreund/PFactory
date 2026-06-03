import { useState, useEffect, useCallback, useRef, useMemo, type ClipboardEvent, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { v4 as uuid } from 'uuid';
import { Loader2, ChevronDown, ChevronUp, Image as ImageIcon, X, RotateCcw, FolderTree, GitBranch, Cloud } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { Label } from './ui/label';
import { Checkbox } from './ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from './ui/select';
import {
  generateImageId,
  blobToBase64,
  createThumbnail,
  isValidImageMimeType,
  resolveFilename
} from './ImageUpload';
import { TaskFileExplorerDrawer } from './TaskFileExplorerDrawer';
import { AgentProfileSelector } from './AgentProfileSelector';
import { FileAutocomplete } from './FileAutocomplete';
import { SkillsBrowser } from './SkillsBrowser';
import { Badge } from './ui/badge';
import { TaskClarificationWizard } from './TaskClarificationWizard';
import { createTask, saveDraft, loadDraft, clearDraft, isDraftEmpty } from '../stores/task-store';
import { useProjectStore } from '../stores/project-store';
import { cn } from '../lib/utils';
import type { Task, TaskCategory, TaskPriority, TaskComplexity, TaskImpact, TaskMetadata, ImageAttachment, TaskDraft, ModelType, ThinkingLevel, ReferencedFile, SelectedSkill } from '../shared/types';
import type { PhaseModelConfig, PhaseThinkingConfig } from '../shared/types/settings';
import {
  TASK_CATEGORY_LABELS,
  TASK_PRIORITY_LABELS,
  TASK_COMPLEXITY_LABELS,
  TASK_IMPACT_LABELS,
  MAX_IMAGES_PER_TASK,
  ALLOWED_IMAGE_TYPES_DISPLAY,
  DEFAULT_AGENT_PROFILES,
  DEFAULT_PHASE_MODELS,
  DEFAULT_PHASE_THINKING
} from '../shared/constants';
import { useSettingsStore } from '../stores/settings-store';

interface TaskCreationWizardProps {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Switch to the Cloud Infrastructure check flow (closes this wizard). */
  onCloudInfra?: () => void;
}

export function TaskCreationWizard({
  projectId,
  open,
  onOpenChange,
  onCloudInfra
}: TaskCreationWizardProps) {
  const { t } = useTranslation('tasks');

  // Get selected agent profile from settings
  const { settings } = useSettingsStore();
  const selectedProfile = DEFAULT_AGENT_PROFILES.find(
    p => p.id === settings.selectedAgentProfile
  ) || DEFAULT_AGENT_PROFILES.find(p => p.id === 'auto')!;

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showFileExplorer, setShowFileExplorer] = useState(false);
  const [showGitOptions, setShowGitOptions] = useState(false);

  // Clarification wizard state
  const [clarificationOpen, setClarificationOpen] = useState(false);
  const [clarificationTask, setClarificationTask] = useState<Task | null>(null);

  // Git options state
  // Use a special value to represent "use project default" since Radix UI Select doesn't allow empty string values
  const PROJECT_DEFAULT_BRANCH = '__project_default__';
  const [branches, setBranches] = useState<string[]>([]);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [baseBranch, setBaseBranch] = useState<string>(PROJECT_DEFAULT_BRANCH);
  const [projectDefaultBranch, setProjectDefaultBranch] = useState<string>('');

  // Get project path from project store
  // Note: projectPath must be non-null for the Browse Files button to render.
  // This depends on receiving the correct projectId prop from App.tsx,
  // which should use (activeProjectId || selectedProjectId) for multi-tab support.
  const projects = useProjectStore((state) => state.projects);
  const projectPath = useMemo(() => {
    const project = projects.find((p) => p.id === projectId);
    return project?.path ?? null;
  }, [projects, projectId]);
  // Used by the Copilot delegation checkbox — delegation only works on
  // GitHub repos in V1; the toggle is disabled with a tooltip otherwise.
  const projectGitProvider = useMemo(() => {
    const project = projects.find((p) => p.id === projectId);
    return (project?.settings?.gitProvider ?? 'github').toLowerCase();
  }, [projects, projectId]);

  // Metadata fields
  const [category, setCategory] = useState<TaskCategory | ''>('');
  const [priority, setPriority] = useState<TaskPriority | ''>('');
  const [complexity, setComplexity] = useState<TaskComplexity | ''>('');
  const [impact, setImpact] = useState<TaskImpact | ''>('');

  // Model configuration (initialized from selected agent profile)
  const [profileId, setProfileId] = useState<string>(settings.selectedAgentProfile || 'auto');
  const [model, setModel] = useState<ModelType | ''>(selectedProfile.model);
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel | ''>(selectedProfile.thinkingLevel);
  // Auto profile - per-phase configuration
  // Use custom settings from app settings if available, otherwise fall back to defaults
  const [phaseModels, setPhaseModels] = useState<PhaseModelConfig | undefined>(
    settings.customPhaseModels || selectedProfile.phaseModels || DEFAULT_PHASE_MODELS
  );
  const [phaseThinking, setPhaseThinking] = useState<PhaseThinkingConfig | undefined>(
    settings.customPhaseThinking || selectedProfile.phaseThinking || DEFAULT_PHASE_THINKING
  );

  // Image attachments
  const [images, setImages] = useState<ImageAttachment[]>([]);

  // Referenced files from file explorer
  const [referencedFiles, setReferencedFiles] = useState<ReferencedFile[]>([]);

  // Execution mode: 'quick' uses simplified prompts (~70% fewer tokens)
  const [mode, setMode] = useState<'quick' | 'full'>('full');

  // Review setting
  const [requireReviewBeforeCoding, setRequireReviewBeforeCoding] = useState(false);
  const [enableRemoteControl, setEnableRemoteControl] = useState(false);
  const [enableDelegation, setEnableDelegation] = useState(false);

  // Skills state
  const [selectedSkills, setSelectedSkills] = useState<SelectedSkill[]>([]);
  const [showSkillsBrowser, setShowSkillsBrowser] = useState(false);

  // Draft state
  const [isDraftRestored, setIsDraftRestored] = useState(false);
  const [pasteSuccess, setPasteSuccess] = useState(false);

  // Ref for the textarea to handle paste events
  const descriptionRef = useRef<HTMLTextAreaElement>(null);

  // Ref for the form scroll container (for drag auto-scroll)
  const formContainerRef = useRef<HTMLDivElement>(null);

  // Drag-and-drop state for images over textarea
  const [isDragOverTextarea, setIsDragOverTextarea] = useState(false);

  // @ autocomplete state
  const [autocomplete, setAutocomplete] = useState<{
    show: boolean;
    query: string;
    startPos: number;
    position: { top: number; left: number };
  } | null>(null);

  // Load draft when dialog opens, or initialize from selected profile
  useEffect(() => {
    if (open && projectId) {
      const draft = loadDraft(projectId);
      if (draft && !isDraftEmpty(draft)) {
        setTitle(draft.title);
        setDescription(draft.description);
        setCategory(draft.category);
        setPriority(draft.priority);
        setComplexity(draft.complexity);
        setImpact(draft.impact);
        // Load model/thinkingLevel/profileId from draft if present, otherwise use profile defaults
        setProfileId(draft.profileId || settings.selectedAgentProfile || 'auto');
        setModel(draft.model || selectedProfile.model);
        setThinkingLevel(draft.thinkingLevel || selectedProfile.thinkingLevel);
        setPhaseModels(draft.phaseModels || settings.customPhaseModels || selectedProfile.phaseModels || DEFAULT_PHASE_MODELS);
        setPhaseThinking(draft.phaseThinking || settings.customPhaseThinking || selectedProfile.phaseThinking || DEFAULT_PHASE_THINKING);
        setImages(draft.images);
        setReferencedFiles(draft.referencedFiles ?? []);
        setRequireReviewBeforeCoding(draft.requireReviewBeforeCoding ?? false);
        setEnableRemoteControl(draft.enableRemoteControl ?? false);
        setEnableDelegation(draft.enableDelegation ?? false);
        setSelectedSkills(draft.selectedSkills ?? []);
        setMode(draft.mode || 'full');
        setIsDraftRestored(true);

        // Expand sections if they have content
        if (draft.category || draft.priority || draft.complexity || draft.impact) {
          setShowAdvanced(true);
        }
      } else {
        // No draft - initialize from selected profile and custom settings
        setProfileId(settings.selectedAgentProfile || 'auto');
        setModel(selectedProfile.model);
        setThinkingLevel(selectedProfile.thinkingLevel);
        setPhaseModels(settings.customPhaseModels || selectedProfile.phaseModels || DEFAULT_PHASE_MODELS);
        setPhaseThinking(settings.customPhaseThinking || selectedProfile.phaseThinking || DEFAULT_PHASE_THINKING);
      }
    }
  }, [open, projectId, settings.selectedAgentProfile, settings.customPhaseModels, settings.customPhaseThinking, selectedProfile.model, selectedProfile.thinkingLevel]);

  // Fetch branches and project default branch when dialog opens
  useEffect(() => {
    if (open && projectPath) {
      fetchBranches();
      fetchProjectDefaultBranch();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectPath]);

  const fetchBranches = async () => {
    if (!projectPath) return;

    setIsLoadingBranches(true);
    try {
      const result = await window.API.getGitBranches(projectPath);
      if (result.success && result.data) {
        setBranches(result.data);
      }
    } catch (err) {
      console.error('Failed to fetch branches:', err);
    } finally {
      setIsLoadingBranches(false);
    }
  };

  const fetchProjectDefaultBranch = async () => {
    if (!projectId) return;

    try {
      // Get env config to check if there's a configured default branch
      const result = await window.API.getProjectEnv(projectId);
      if (result.success && result.data?.defaultBranch) {
        setProjectDefaultBranch(result.data.defaultBranch);
      } else if (projectPath) {
        // Fall back to auto-detect
        const detectResult = await window.API.detectMainBranch(projectPath);
        if (detectResult.success && detectResult.data) {
          setProjectDefaultBranch(detectResult.data);
        }
      }
    } catch (err) {
      console.error('Failed to fetch project default branch:', err);
    }
  };

  /**
   * Get current form state as a draft
   */
  const getCurrentDraft = useCallback((): TaskDraft => ({
    projectId,
    title,
    description,
    category,
    priority,
    complexity,
    impact,
    profileId,
    mode,
    model,
    thinkingLevel,
    phaseModels,
    phaseThinking,
    images,
    referencedFiles,
    requireReviewBeforeCoding,
    enableRemoteControl,
    enableDelegation,
    selectedSkills,
    savedAt: new Date()
  }), [projectId, title, description, category, priority, complexity, impact, profileId, mode, model, thinkingLevel, phaseModels, phaseThinking, images, referencedFiles, requireReviewBeforeCoding, enableRemoteControl, enableDelegation, selectedSkills]);
  /**
   * Handle paste event for screenshot support
   * Strategy: Let browser handle text paste naturally, we only process images separately
   */
  const handlePaste = useCallback(async (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const clipboardItems = e.clipboardData?.items;
    if (!clipboardItems) return;

    // Find image items in clipboard
    const imageItems: DataTransferItem[] = [];
    for (let i = 0; i < clipboardItems.length; i++) {
      const item = clipboardItems[i];
      if (item.type.startsWith('image/')) {
        imageItems.push(item);
      }
    }

    // If no images, allow normal paste behavior
    if (imageItems.length === 0) return;

    // DON'T prevent default - let the browser paste the text normally
    // We only handle images separately

    // Check if we can add more images
    const remainingSlots = MAX_IMAGES_PER_TASK - images.length;
    if (remainingSlots <= 0) {
      setError(`Maximum of ${MAX_IMAGES_PER_TASK} images allowed`);
      return;
    }

    setError(null);

    // Process image items asynchronously (doesn't block text paste)
    const newImages: ImageAttachment[] = [];
    const existingFilenames = images.map(img => img.filename);

    for (const item of imageItems.slice(0, remainingSlots)) {
      const file = item.getAsFile();
      if (!file) continue;

      // Validate image type
      if (!isValidImageMimeType(file.type)) {
        setError(`Invalid image type. Allowed: ${ALLOWED_IMAGE_TYPES_DISPLAY}`);
        continue;
      }

      try {
        const dataUrl = await blobToBase64(file);
        const thumbnail = await createThumbnail(dataUrl);

        // Generate filename for pasted images (screenshot-timestamp.ext)
        const extension = file.type.split('/')[1] || 'png';
        const baseFilename = `screenshot-${Date.now()}.${extension}`;
        const resolvedFilename = resolveFilename(baseFilename, [
          ...existingFilenames,
          ...newImages.map(img => img.filename)
        ]);

        newImages.push({
          id: generateImageId(),
          filename: resolvedFilename,
          mimeType: file.type,
          size: file.size,
          data: dataUrl.split(',')[1], // Store base64 without data URL prefix
          thumbnail
        });
      } catch {
        setError('Failed to process pasted image');
      }
    }

    if (newImages.length > 0) {
      setImages(prev => [...prev, ...newImages]);
      // Show success feedback
      setPasteSuccess(true);
      setTimeout(() => setPasteSuccess(false), 2000);
    }
  }, [images]);

  /**
   * Detect @ mention being typed and show autocomplete
   */
  const detectAtMention = useCallback((text: string, cursorPos: number) => {
    const beforeCursor = text.slice(0, cursorPos);
    // Match @ followed by optional path characters (letters, numbers, dots, dashes, slashes)
    const match = beforeCursor.match(/@([\w\-./\\]*)$/);

    if (match) {
      return {
        query: match[1],
        startPos: cursorPos - match[0].length
      };
    }
    return null;
  }, []);

  /**
   * Handle description change and check for @ mentions
   */
  const handleDescriptionChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    const cursorPos = e.target.selectionStart || 0;

    setDescription(newValue);

    // Check for @ mention at cursor
    const mention = detectAtMention(newValue, cursorPos);

    if (mention) {
      // Calculate popup position based on cursor
      const textarea = descriptionRef.current;
      if (textarea) {
        const rect = textarea.getBoundingClientRect();
        const textareaStyle = window.getComputedStyle(textarea);
        const lineHeight = parseFloat(textareaStyle.lineHeight) || 20;
        const paddingTop = parseFloat(textareaStyle.paddingTop) || 8;
        const paddingLeft = parseFloat(textareaStyle.paddingLeft) || 12;

        // Estimate cursor position (simplified - assumes fixed-width font)
        const textBeforeCursor = newValue.slice(0, cursorPos);
        const lines = textBeforeCursor.split('\n');
        const currentLineIndex = lines.length - 1;
        const currentLineLength = lines[currentLineIndex].length;

        // Calculate position relative to textarea
        const charWidth = 8; // Approximate character width
        const top = paddingTop + (currentLineIndex + 1) * lineHeight + 4;
        const left = paddingLeft + Math.min(currentLineLength * charWidth, rect.width - 300);

        setAutocomplete({
          show: true,
          query: mention.query,
          startPos: mention.startPos,
          position: { top, left: Math.max(0, left) }
        });
      }
    } else {
      // No @ mention at cursor, close autocomplete
      if (autocomplete?.show) {
        setAutocomplete(null);
      }
    }
  }, [detectAtMention, autocomplete?.show]);

  /**
   * Handle autocomplete selection
   */
  const handleAutocompleteSelect = useCallback((filename: string) => {
    if (!autocomplete) return;

    const textarea = descriptionRef.current;
    if (!textarea) return;

    // Replace the @query with @filename
    const beforeMention = description.slice(0, autocomplete.startPos);
    const afterMention = description.slice(autocomplete.startPos + 1 + autocomplete.query.length);
    const newDescription = beforeMention + '@' + filename + afterMention;

    setDescription(newDescription);
    setAutocomplete(null);

    // Set cursor after the inserted mention
    setTimeout(() => {
      const newCursorPos = autocomplete.startPos + 1 + filename.length;
      textarea.focus();
      textarea.setSelectionRange(newCursorPos, newCursorPos);
    }, 0);
  }, [autocomplete, description]);

  /**
   * Close autocomplete
   */
  const handleAutocompleteClose = useCallback(() => {
    setAutocomplete(null);
  }, []);

  /**
   * Handle drag over the form container to auto-scroll when dragging near edges
   */
  const handleContainerDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    const container = formContainerRef.current;
    if (!container) return;

    const rect = container.getBoundingClientRect();
    const edgeThreshold = 60; // px from edge to trigger scroll
    const scrollSpeed = 8;

    // Auto-scroll when dragging near top or bottom edges
    if (e.clientY < rect.top + edgeThreshold) {
      container.scrollTop -= scrollSpeed;
    } else if (e.clientY > rect.bottom - edgeThreshold) {
      container.scrollTop += scrollSpeed;
    }
  }, []);

  /**
   * Handle drag over textarea for image drops
   */
  const handleTextareaDragOver = useCallback((e: DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOverTextarea(true);
  }, []);

  /**
   * Handle drag leave from textarea
   */
  const handleTextareaDragLeave = useCallback((e: DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOverTextarea(false);
  }, []);

  /**
   * Handle drop on textarea for file references and images
   */
  const handleTextareaDrop = useCallback(
    async (e: DragEvent<HTMLTextAreaElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOverTextarea(false);

      if (isCreating) return;

      // First, check for file reference drops (from the file explorer)
      const jsonData = e.dataTransfer?.getData('application/json');
      if (jsonData) {
        try {
          const data = JSON.parse(jsonData);
          if (data.type === 'file-reference' && data.name) {
            // Insert @mention at cursor position in the textarea
            const textarea = descriptionRef.current;
            if (textarea) {
              const cursorPos = textarea.selectionStart || 0;
              const textBefore = description.substring(0, cursorPos);
              const textAfter = description.substring(cursorPos);

              // Insert @mention at cursor position
              const mention = `@${data.name}`;
              const newDescription = textBefore + mention + textAfter;
              setDescription(newDescription);

              // Set cursor after the inserted mention
              setTimeout(() => {
                textarea.focus();
                const newCursorPos = cursorPos + mention.length;
                textarea.setSelectionRange(newCursorPos, newCursorPos);
              }, 0);

              return; // Don't process as image
            }
          }
        } catch {
          // Not valid JSON, continue to image handling
        }
      }

      // Fall back to image file handling
      const files = e.dataTransfer?.files;
      if (!files || files.length === 0) return;

      // Filter for image files
      const imageFiles: File[] = [];
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        if (file.type.startsWith('image/')) {
          imageFiles.push(file);
        }
      }

      if (imageFiles.length === 0) return;

      // Check if we can add more images
      const remainingSlots = MAX_IMAGES_PER_TASK - images.length;
      if (remainingSlots <= 0) {
        setError(`Maximum of ${MAX_IMAGES_PER_TASK} images allowed`);
        return;
      }

      setError(null);

      // Process image files
      const newImages: ImageAttachment[] = [];
      const existingFilenames = images.map(img => img.filename);

      for (const file of imageFiles.slice(0, remainingSlots)) {
        // Validate image type
        if (!isValidImageMimeType(file.type)) {
          setError(`Invalid image type. Allowed: ${ALLOWED_IMAGE_TYPES_DISPLAY}`);
          continue;
        }

        try {
          const dataUrl = await blobToBase64(file);
          const thumbnail = await createThumbnail(dataUrl);

          // Use original filename or generate one
          const baseFilename = file.name || `dropped-image-${Date.now()}.${file.type.split('/')[1] || 'png'}`;
          const resolvedFilename = resolveFilename(baseFilename, [
            ...existingFilenames,
            ...newImages.map(img => img.filename)
          ]);

          newImages.push({
            id: generateImageId(),
            filename: resolvedFilename,
            mimeType: file.type,
            size: file.size,
            data: dataUrl.split(',')[1], // Store base64 without data URL prefix
            thumbnail
          });
        } catch {
          setError('Failed to process dropped image');
        }
      }

      if (newImages.length > 0) {
        setImages(prev => [...prev, ...newImages]);
        // Show success feedback
        setPasteSuccess(true);
        setTimeout(() => setPasteSuccess(false), 2000);
      }
    },
    [images, isCreating, description]
  );

  /**
   * Parse @mentions from description and create ReferencedFile entries
   * Merges with existing referencedFiles, avoiding duplicates
   */
  const parseFileMentions = useCallback((text: string, existingFiles: ReferencedFile[]): ReferencedFile[] => {
    // Match @filename patterns (supports filenames with dots, hyphens, underscores, and path separators)
    const mentionRegex = /@([\w\-./\\]+\.\w+)/g;
    const matches = Array.from(text.matchAll(mentionRegex));

    if (matches.length === 0) return existingFiles;

    // Create a set of existing file names for quick lookup
    const existingNames = new Set(existingFiles.map(f => f.name));

    // Parse mentioned files that aren't already in the list
    const newFiles: ReferencedFile[] = [];
    matches.forEach(match => {
      const fileName = match[1];
      if (!existingNames.has(fileName)) {
        newFiles.push({
          id: uuid(),
          path: fileName, // Store relative path from @mention
          name: fileName,
          isDirectory: false,
          addedAt: new Date()
        });
        existingNames.add(fileName); // Prevent duplicates within mentions
      }
    });

    return [...existingFiles, ...newFiles];
  }, []);

  const handleCreate = async () => {
    if (!description.trim()) {
      setError('Please provide a description');
      return;
    }

    setIsCreating(true);
    setError(null);

    try {
      // Parse @mentions from description and merge with referenced files
      const allReferencedFiles = parseFileMentions(description, referencedFiles);

      // Build metadata from selected values
      const metadata: TaskMetadata = {
        sourceType: 'manual'
      };

      if (category) metadata.category = category;
      if (priority) metadata.priority = priority;
      if (complexity) metadata.complexity = complexity;
      if (impact) metadata.impact = impact;
      if (model) metadata.model = model;
      if (thinkingLevel) metadata.thinkingLevel = thinkingLevel;
      // Auto profile - per-phase configuration
      if (profileId === 'auto') {
        metadata.isAutoProfile = true;
        if (phaseModels) metadata.phaseModels = phaseModels;
        if (phaseThinking) metadata.phaseThinking = phaseThinking;
      }
      if (images.length > 0) metadata.attachedImages = images;
      if (allReferencedFiles.length > 0) metadata.referencedFiles = allReferencedFiles;
      if (requireReviewBeforeCoding) metadata.requireReviewBeforeCoding = true;
      if (enableRemoteControl) metadata.enableRemoteControl = true;
      if (enableDelegation) metadata.enableDelegation = true;
      // Only include baseBranch if it's not the project default placeholder
      if (baseBranch && baseBranch !== PROJECT_DEFAULT_BRANCH) metadata.baseBranch = baseBranch;
      // Execution mode: 'quick' uses simplified prompts (~70% fewer tokens)
      if (mode) metadata.mode = mode;
      // Skills: inject selected skills for AI context during execution
      if (selectedSkills.length > 0) metadata.selectedSkills = selectedSkills;

      // Title is optional - if empty, it will be auto-generated by the backend
      const task = await createTask(projectId, title.trim(), description.trim(), metadata);
      if (task) {
        // Clear draft on successful creation
        clearDraft(projectId);
        resetForm();
        onOpenChange(false);
        // Open clarification wizard
        setClarificationTask(task);
        setClarificationOpen(true);
      } else {
        setError('Failed to create task. Please try again.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setIsCreating(false);
    }
  };

  const resetForm = () => {
    setTitle('');
    setDescription('');
    setCategory('');
    setPriority('');
    setComplexity('');
    setImpact('');
    // Reset to selected profile defaults and custom settings
    setProfileId(settings.selectedAgentProfile || 'auto');
    setModel(selectedProfile.model);
    setThinkingLevel(selectedProfile.thinkingLevel);
    setPhaseModels(settings.customPhaseModels || selectedProfile.phaseModels || DEFAULT_PHASE_MODELS);
    setPhaseThinking(settings.customPhaseThinking || selectedProfile.phaseThinking || DEFAULT_PHASE_THINKING);
    setImages([]);
    setReferencedFiles([]);
    setMode('full');
    setRequireReviewBeforeCoding(false);
    setSelectedSkills([]);
    setShowSkillsBrowser(false);
    setBaseBranch(PROJECT_DEFAULT_BRANCH);
    setError(null);
    setShowAdvanced(false);
    setShowFileExplorer(false);
    setShowGitOptions(false);
    setIsDraftRestored(false);
    setPasteSuccess(false);
  };

  /**
   * Handle dialog close - save draft if content exists
   */
  const handleClose = () => {
    if (isCreating) return;

    const draft = getCurrentDraft();

    // Save draft if there's any content
    if (!isDraftEmpty(draft)) {
      saveDraft(draft);
    } else {
      // Clear any existing draft if form is empty
      clearDraft(projectId);
    }

    resetForm();
    onOpenChange(false);
  };

  /**
   * Discard draft and start fresh
   */
  const handleDiscardDraft = () => {
    clearDraft(projectId);
    resetForm();
    setError(null);
  };

  return (
    <>
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className={cn(
          "max-h-[90vh] p-0 overflow-hidden transition-all duration-300 ease-out bg-card",
          showFileExplorer ? "sm:max-w-[60vw]" : "sm:max-w-[50vw] sm:min-w-[550px]"
        )}
        hideCloseButton={showFileExplorer}
      >
        <div className="flex h-full min-h-0 overflow-hidden">
          {/* Form content */}
          <div
            ref={formContainerRef}
            onDragOver={handleContainerDragOver}
            className="flex-1 flex flex-col p-6 min-w-0 min-h-0 overflow-y-auto relative"
          >
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle className="text-foreground">Create New Task</DialogTitle>
            {isDraftRestored && (
              <div className="flex items-center gap-2">
                <span className="text-xs bg-info/10 text-info px-2 py-1 rounded-md">
                  Draft restored
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
                  onClick={handleDiscardDraft}
                >
                  <RotateCcw className="h-3 w-3 mr-1" />
                  Start Fresh
                </Button>
              </div>
            )}
          </div>
          <DialogDescription>
            Describe what you want to build. The AI will analyze your request and
            create a detailed specification.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 py-4">
          {/* Task template chooser — Code change (this wizard) vs Cloud Infrastructure */}
          {onCloudInfra && (
            <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 px-3 py-2">
              <div className="flex items-center gap-2 text-sm">
                <Cloud className="h-4 w-4 text-muted-foreground" />
                <span className="text-muted-foreground">
                  Assessing a cloud account instead of changing code?
                </span>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  onOpenChange(false);
                  onCloudInfra();
                }}
              >
                Cloud Infrastructure
              </Button>
            </div>
          )}
          {/* Description (Primary - Required) */}
          <div className="space-y-2">
            <Label htmlFor="description" className="text-sm font-medium text-foreground">
              Description <span className="text-destructive">*</span>
            </Label>
            {/* Wrap textarea for file @mentions */}
            <div className="relative">
              {/* Syntax highlight overlay for @mentions */}
              <div
                className="absolute inset-0 pointer-events-none overflow-hidden rounded-md border border-transparent"
                style={{
                  padding: '0.5rem 0.75rem',
                  font: 'inherit',
                  lineHeight: '1.5',
                  wordWrap: 'break-word',
                  whiteSpace: 'pre-wrap',
                  color: 'transparent'
                }}
              >
                {description.split(/(@[\w\-./\\]+\.\w+)/g).map((part, i) => {
                  // Check if this part is an @mention
                  if (part.match(/^@[\w\-./\\]+\.\w+$/)) {
                    return (
                      <span
                        key={i}
                        className="underline decoration-info/60 underline-offset-2"
                      >
                        {part}
                      </span>
                    );
                  }
                  return <span key={i}>{part}</span>;
                })}
              </div>
              <Textarea
                ref={descriptionRef}
                id="description"
                placeholder="Describe the feature, bug fix, or improvement you want to implement. Be as specific as possible about requirements, constraints, and expected behavior. Type @ to reference files."
                value={description}
                onChange={handleDescriptionChange}
                onPaste={handlePaste}
                onDragOver={handleTextareaDragOver}
                onDragLeave={handleTextareaDragLeave}
                onDrop={handleTextareaDrop}
                rows={5}
                disabled={isCreating}
                className={cn(
                  "resize-y min-h-[120px] max-h-[400px] relative bg-transparent",
                  // Visual feedback when dragging over textarea
                  isDragOverTextarea && !isCreating && "border-primary bg-primary/5 ring-2 ring-primary/20"
                )}
                style={{ caretColor: 'auto' }}
              />
              {/* File autocomplete popup */}
              {autocomplete?.show && projectPath && (
                <FileAutocomplete
                  query={autocomplete.query}
                  projectPath={projectPath}
                  position={autocomplete.position}
                  onSelect={handleAutocompleteSelect}
                  onClose={handleAutocompleteClose}
                />
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              Files and images can be copy/pasted or dragged & dropped into the description.
            </p>

            {/* Image Thumbnails - displayed inline below description */}
            {images.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {images.map((image) => (
                  <div
                    key={image.id}
                    className="relative group rounded-md border border-border overflow-hidden cursor-pointer hover:ring-2 hover:ring-primary/50 transition-all"
                    style={{ width: '64px', height: '64px' }}
                    onClick={() => {
                      // Open full-size image in a new window/modal could be added here
                    }}
                    title={image.filename}
                  >
                    {image.thumbnail ? (
                      <img
                        src={image.thumbnail}
                        alt={image.filename}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center bg-muted">
                        <ImageIcon className="h-6 w-6 text-muted-foreground" />
                      </div>
                    )}
                    {/* Remove button */}
                    {!isCreating && (
                      <button
                        type="button"
                        className="absolute top-0.5 right-0.5 h-4 w-4 flex items-center justify-center rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 transition-opacity"
                        onClick={(e) => {
                          e.stopPropagation();
                          setImages(prev => prev.filter(img => img.id !== image.id));
                        }}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Title (Optional - Auto-generated if empty) */}
          <div className="space-y-2">
            <Label htmlFor="title" className="text-sm font-medium text-foreground">
              Task Title <span className="text-muted-foreground font-normal">(optional)</span>
            </Label>
            <Input
              id="title"
              placeholder="Leave empty to auto-generate from description"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={isCreating}
            />
            <p className="text-xs text-muted-foreground">
              A short, descriptive title will be generated automatically if left empty.
            </p>
          </div>

          {/* Execution Mode Selector */}
          <div className="space-y-2">
            <Label className="text-sm font-medium text-foreground">
              Execution Mode
            </Label>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => {
                  setMode('quick');
                  setRequireReviewBeforeCoding(false); // Quick Mode skips review by default
                }}
                disabled={isCreating}
                className={cn(
                  "flex-1 p-3 rounded-lg border-2 transition-all text-left",
                  mode === 'quick'
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/50 hover:bg-muted/50"
                )}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-lg">⚡</span>
                  <span className="font-medium text-foreground">Quick Mode</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  ~70% fewer tokens, faster execution
                </p>
              </button>
              <button
                type="button"
                onClick={() => setMode('full')}
                disabled={isCreating}
                className={cn(
                  "flex-1 p-3 rounded-lg border-2 transition-all text-left",
                  mode === 'full'
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/50 hover:bg-muted/50"
                )}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-lg">📋</span>
                  <span className="font-medium text-foreground">Full Mode</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  Comprehensive analysis (default)
                </p>
              </button>
            </div>
            {mode === 'quick' && (
              <p className="text-xs text-info bg-info/10 p-2 rounded-md">
                Quick Mode uses simplified prompts and skips human review before coding for faster execution.
              </p>
            )}
          </div>

          {/* Agent Profile Selection */}
          <AgentProfileSelector
            profileId={profileId}
            model={model}
            thinkingLevel={thinkingLevel}
            phaseModels={phaseModels}
            phaseThinking={phaseThinking}
            onProfileChange={(newProfileId, newModel, newThinkingLevel) => {
              setProfileId(newProfileId);
              setModel(newModel);
              setThinkingLevel(newThinkingLevel);
            }}
            onModelChange={setModel}
            onThinkingLevelChange={setThinkingLevel}
            onPhaseModelsChange={setPhaseModels}
            onPhaseThinkingChange={setPhaseThinking}
            disabled={isCreating}
          />

          {/* Paste Success Indicator */}
          {pasteSuccess && (
            <div className="flex items-center gap-2 text-sm text-success animate-in fade-in slide-in-from-top-1 duration-200">
              <ImageIcon className="h-4 w-4" />
              Image added successfully!
            </div>
          )}

          {/* AI Skills Section */}
          <div className="rounded-lg border border-border">
            <button
              type="button"
              onClick={() => setShowSkillsBrowser(!showSkillsBrowser)}
              className={cn(
                'flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors',
                'w-full justify-between py-2 px-3 rounded-lg hover:bg-muted/50'
              )}
              disabled={isCreating}
            >
              <span className="flex items-center gap-2 font-medium">
                {t('skills.title')}
                {selectedSkills.length > 0 && (
                  <Badge variant="secondary" className="h-5 px-1.5 text-xs">
                    {selectedSkills.length}
                  </Badge>
                )}
              </span>
              <ChevronDown
                className={cn(
                  'h-4 w-4 transition-transform duration-200',
                  showSkillsBrowser && 'rotate-180'
                )}
              />
            </button>
            {showSkillsBrowser && (
              <SkillsBrowser
                selectedSkills={selectedSkills}
                onSkillsChange={setSelectedSkills}
                taskDescription={description}
                maxSkills={5}
              />
            )}
          </div>

          {/* Advanced Options Toggle */}
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className={cn(
              'flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors',
              'w-full justify-between py-2 px-3 rounded-md hover:bg-muted/50'
            )}
            disabled={isCreating}
          >
            <span>Classification (optional)</span>
            {showAdvanced ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </button>

          {/* Advanced Options */}
          {showAdvanced && (
            <div className="space-y-4 p-4 rounded-lg border border-border bg-muted/30">
              <div className="grid grid-cols-2 gap-4">
                {/* Category */}
                <div className="space-y-2">
                  <Label htmlFor="category" className="text-xs font-medium text-muted-foreground">
                    Category
                  </Label>
                  <Select
                    value={category}
                    onValueChange={(value) => setCategory(value as TaskCategory)}
                    disabled={isCreating}
                  >
                    <SelectTrigger id="category" className="h-9">
                      <SelectValue placeholder="Select category" />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(TASK_CATEGORY_LABELS).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Priority */}
                <div className="space-y-2">
                  <Label htmlFor="priority" className="text-xs font-medium text-muted-foreground">
                    Priority
                  </Label>
                  <Select
                    value={priority}
                    onValueChange={(value) => setPriority(value as TaskPriority)}
                    disabled={isCreating}
                  >
                    <SelectTrigger id="priority" className="h-9">
                      <SelectValue placeholder="Select priority" />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(TASK_PRIORITY_LABELS).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Complexity */}
                <div className="space-y-2">
                  <Label htmlFor="complexity" className="text-xs font-medium text-muted-foreground">
                    Complexity
                  </Label>
                  <Select
                    value={complexity}
                    onValueChange={(value) => setComplexity(value as TaskComplexity)}
                    disabled={isCreating}
                  >
                    <SelectTrigger id="complexity" className="h-9">
                      <SelectValue placeholder="Select complexity" />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(TASK_COMPLEXITY_LABELS).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Impact */}
                <div className="space-y-2">
                  <Label htmlFor="impact" className="text-xs font-medium text-muted-foreground">
                    Impact
                  </Label>
                  <Select
                    value={impact}
                    onValueChange={(value) => setImpact(value as TaskImpact)}
                    disabled={isCreating}
                  >
                    <SelectTrigger id="impact" className="h-9">
                      <SelectValue placeholder="Select impact" />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(TASK_IMPACT_LABELS).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <p className="text-xs text-muted-foreground">
                These labels help organize and prioritize tasks. They&apos;re optional but useful for filtering.
              </p>
            </div>
          )}

          {/* Review Requirement Toggle */}
          <div className="flex items-start gap-3 p-4 rounded-lg border border-border bg-muted/30">
            <Checkbox
              id="require-review"
              checked={requireReviewBeforeCoding}
              onCheckedChange={(checked) => setRequireReviewBeforeCoding(checked === true)}
              disabled={isCreating}
              className="mt-0.5"
            />
            <div className="flex-1 space-y-1">
              <Label
                htmlFor="require-review"
                className="text-sm font-medium text-foreground cursor-pointer"
              >
                Require human review before coding
              </Label>
              <p className="text-xs text-muted-foreground">
                When enabled, you&apos;ll be prompted to review the spec and implementation plan before the coding phase begins. This allows you to approve, request changes, or provide feedback.
              </p>
            </div>
          </div>

          {/* Remote Control Toggle — drives Claude Code's native --remote-control flag */}
          <div className="flex items-start gap-3 p-4 rounded-lg border border-border bg-muted/30">
            <Checkbox
              id="enable-remote-control"
              checked={enableRemoteControl}
              onCheckedChange={(checked) => setEnableRemoteControl(checked === true)}
              disabled={isCreating}
              className="mt-0.5"
            />
            <div className="flex-1 space-y-1">
              <Label
                htmlFor="enable-remote-control"
                className="text-sm font-medium text-foreground cursor-pointer"
              >
                Enable Remote Control
              </Label>
              <p className="text-xs text-muted-foreground">
                Drive this task from{' '}
                <a
                  href="https://claude.ai/code"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-foreground"
                >
                  claude.ai/code
                </a>{' '}
                or the Claude mobile app. The session appears as &quot;PFactory: &lt;spec-id&gt;&quot; in your Claude session list. Requires a paid Anthropic subscription (Pro/Max/Team/Enterprise) and{' '}
                <code className="text-xs bg-muted px-1 rounded">claude auth login</code> on the PFactory host.
              </p>
            </div>
          </div>

          {/* Delegation Toggle — hand the coding phase off to the
              project's autonomous coding agent (GitHub Copilot Coding
              Agent on GitHub, GitLab Duo Workflow on GitLab — V1.5,
              #98). PFactory still plans the spec locally. Disabled for
              Azure DevOps (no equivalent agent exists). */}
          {(() => {
            const delegationSupported =
              projectGitProvider === 'github' || projectGitProvider === 'gitlab';
            const delegationDisabled = isCreating || !delegationSupported;
            const delegationTooltip = !delegationSupported
              ? t('tasks:delegation.githubOrGitlabTooltip')
              : undefined;
            return (
              <div
                className="flex items-start gap-3 p-4 rounded-lg border border-border bg-muted/30"
                title={delegationTooltip}
              >
                <Checkbox
                  id="enable-delegation"
                  checked={enableDelegation && delegationSupported}
                  onCheckedChange={(checked) => setEnableDelegation(checked === true)}
                  disabled={delegationDisabled}
                  className="mt-0.5"
                />
                <div className="flex-1 space-y-1">
                  <Label
                    htmlFor="enable-delegation"
                    className={cn(
                      'text-sm font-medium cursor-pointer',
                      delegationDisabled
                        ? 'text-muted-foreground'
                        : 'text-foreground'
                    )}
                  >
                    {t('tasks:delegation.enableLabel')}
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    {t('tasks:delegation.enableHelp')}
                  </p>
                </div>
              </div>
            );
          })()}

          {/* Git Options Toggle */}
          <button
            type="button"
            onClick={() => setShowGitOptions(!showGitOptions)}
            className={cn(
              'flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors',
              'w-full justify-between py-2 px-3 rounded-md hover:bg-muted/50'
            )}
            disabled={isCreating}
          >
            <span className="flex items-center gap-2">
              <GitBranch className="h-4 w-4" />
              Git Options (optional)
              {baseBranch && baseBranch !== PROJECT_DEFAULT_BRANCH && (
                <span className="text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded">
                  {baseBranch}
                </span>
              )}
            </span>
            {showGitOptions ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </button>

          {/* Git Options */}
          {showGitOptions && (
            <div className="space-y-4 p-4 rounded-lg border border-border bg-muted/30">
              <div className="space-y-2">
                <Label htmlFor="base-branch" className="text-sm font-medium text-foreground">
                  Base Branch (optional)
                </Label>
                <Select
                  value={baseBranch}
                  onValueChange={setBaseBranch}
                  disabled={isCreating || isLoadingBranches}
                >
                  <SelectTrigger id="base-branch" className="h-9">
                    <SelectValue placeholder={`Use project default${projectDefaultBranch ? ` (${projectDefaultBranch})` : ''}`} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={PROJECT_DEFAULT_BRANCH}>
                      Use project default{projectDefaultBranch ? ` (${projectDefaultBranch})` : ''}
                    </SelectItem>
                    {branches.map((branch) => (
                      <SelectItem key={branch} value={branch}>
                        {branch}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Override the branch this task&apos;s worktree will be created from. Leave empty to use the project&apos;s configured default branch.
                </p>
              </div>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-destructive/10 border border-destructive/30 p-3 text-sm text-destructive">
              <X className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>

        <DialogFooter>
          <div className="flex items-center gap-2">
            {/* File Explorer Toggle Button */}
            {/* Only render Browse Files button when projectPath is available.
                Without a valid project path, the file explorer cannot load files.
                This was the root cause of the button not appearing - App.tsx was
                passing the wrong projectId in multi-tab scenarios. */}
            {projectPath && (
              <Button
                type="button"
                variant={showFileExplorer ? 'default' : 'outline'}
                size="sm"
                onClick={() => {
                  setShowFileExplorer(!showFileExplorer);
                }}
                disabled={isCreating}
                className="gap-1.5"
              >
                <FolderTree className="h-4 w-4" />
                {showFileExplorer ? 'Hide Files' : 'Browse Files'}
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={handleClose} disabled={isCreating}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={isCreating || !description.trim()}>
              {isCreating ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                'Create Task'
              )}
            </Button>
          </div>
        </DialogFooter>
          </div>

          {/* File Explorer Drawer */}
          {/* Only mount the drawer when projectPath is available.
              This prevents the component from trying to load files without a valid path. */}
          {projectPath && (
            <TaskFileExplorerDrawer
              isOpen={showFileExplorer}
              onClose={() => setShowFileExplorer(false)}
              projectPath={projectPath}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>

    {clarificationTask && (
      <TaskClarificationWizard
        open={clarificationOpen}
        onOpenChange={(isOpen) => {
          setClarificationOpen(isOpen);
          if (!isOpen) setClarificationTask(null);
        }}
        taskId={clarificationTask.id}
        taskTitle={clarificationTask.title}
        taskDescription={clarificationTask.description}
        projectId={projectId}
      />
    )}
    </>
  );
}
