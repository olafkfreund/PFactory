/**
 * PlanUploadForm — drag-and-drop file + paste-text ingest form.
 *
 * Accepts .pdf, .docx, .md files via drag-and-drop or browse,
 * OR a pasted/typed text block. Title is optional.
 * Calls either ingestFile or ingestText via the store.
 *
 * Tests inject fetchFn for the store (the store is the seam).
 */

import { useState, useRef, useCallback, useEffect, type DragEvent } from 'react';
import { Upload, FileText, X, Loader2, Plus } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Textarea } from '../ui/textarea';
import { cn } from '../../lib/utils';
import { usePlanStore } from '../../stores/plan-store';
import { getCategories } from '../../lib/planning-api';
import type { PlanSession, CategoryEntry } from '../../shared/types/plan';

const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.md'];
const ACCEPTED_MIME = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/markdown',
  'text/x-markdown',
];

function isAccepted(file: File): boolean {
  const ext = `.${file.name.split('.').pop()?.toLowerCase() ?? ''}`;
  return ACCEPTED_EXTENSIONS.includes(ext) || ACCEPTED_MIME.includes(file.type);
}

interface Props {
  onSuccess?: (session: PlanSession) => void;
  onCancel?: () => void;
  /** Test seam: injected fetchFn for the store (set before mount). */
  fetchFn?: typeof fetch;
}

export function PlanUploadForm({ onSuccess, onCancel, fetchFn }: Props) {
  const [mode, setMode] = useState<'file' | 'text'>('file');
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [fileError, setFileError] = useState<string | null>(null);
  const [categories, setCategories] = useState<CategoryEntry[]>([]);
  const [category, setCategory] = useState('');
  const [template, setTemplate] = useState('');

  const store = usePlanStore();
  const inputRef = useRef<HTMLInputElement>(null);

  // Wire fetchFn into store if provided
  if (fetchFn && store.fetchFn !== fetchFn) {
    store.setFetchFn(fetchFn);
  }

  const { loading, error } = store;

  // Load the intake categories (#1) for the picker. Best-effort: a fetch failure
  // just leaves the picker empty — ingest still works (auto-detect).
  useEffect(() => {
    let cancelled = false;
    getCategories({ fetchFn: fetchFn ?? store.fetchFn })
      .then((r) => { if (!cancelled) setCategories(Array.isArray(r?.categories) ? r.categories : []); })
      .catch(() => { /* picker is optional */ });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchFn]);

  // Templates available for the chosen category.
  const templatesForCategory =
    categories.find((c) => c.category === category)?.templates ?? [];

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  const handleDrop = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (!dropped) return;
    if (!isAccepted(dropped)) {
      setFileError(`Unsupported file type. Accepted: ${ACCEPTED_EXTENSIONS.join(', ')}`);
      return;
    }
    setFileError(null);
    setFile(dropped);
  }, []);

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (!selected) return;
    if (!isAccepted(selected)) {
      setFileError(`Unsupported file type. Accepted: ${ACCEPTED_EXTENSIONS.join(', ')}`);
      return;
    }
    setFileError(null);
    setFile(selected);
  }, []);

  const handleSubmit = async () => {
    store.clearError();
    try {
      let session: PlanSession;
      if (mode === 'file') {
        if (!file) return;
        session = await store.ingestFilePlan(
          file, title || undefined, category || undefined, template || undefined,
        );
      } else {
        if (!text.trim()) return;
        session = await store.ingestTextPlan({
          text: text.trim(),
          title: title || undefined,
          category: category || undefined,
          template: template || undefined,
        });
      }
      onSuccess?.(session);
    } catch {
      // error is stored in the store
    }
  };

  const canSubmit =
    !loading &&
    (mode === 'file' ? file !== null : text.trim().length > 0);

  return (
    <div className="flex flex-col gap-5" data-testid="plan-upload-form">
      {/* Mode toggle */}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setMode('file')}
          className={cn(
            'flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
            mode === 'file'
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted text-muted-foreground hover:text-foreground'
          )}
        >
          <Upload className="h-3.5 w-3.5" aria-hidden />
          Upload file
        </button>
        <button
          type="button"
          onClick={() => setMode('text')}
          className={cn(
            'flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
            mode === 'text'
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted text-muted-foreground hover:text-foreground'
          )}
        >
          <FileText className="h-3.5 w-3.5" aria-hidden />
          Paste text
        </button>
      </div>

      {/* Title */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="plan-title" className="text-sm font-medium text-foreground">
          Title <span className="text-muted-foreground font-normal">(optional)</span>
        </label>
        <Input
          id="plan-title"
          placeholder="e.g. Q3 Feature Specification"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-label="Plan title"
        />
      </div>

      {/* Category + template picker (#1) */}
      {categories.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="plan-category" className="text-sm font-medium text-foreground">
              Category <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <select
              id="plan-category"
              value={category}
              onChange={(e) => { setCategory(e.target.value); setTemplate(''); }}
              aria-label="Plan category"
              className="h-9 rounded-md border border-input bg-background px-3 text-sm capitalize
                         focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="">Auto-detect</option>
              {categories.map((c) => (
                <option key={c.category} value={c.category}>{c.category}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="plan-template" className="text-sm font-medium text-foreground">
              Template <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <select
              id="plan-template"
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              aria-label="Plan template"
              disabled={templatesForCategory.length === 0}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm
                         focus:outline-none focus:ring-2 focus:ring-ring
                         disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="">
                {templatesForCategory.length === 0 ? 'No templates for category' : 'None — suggest only'}
              </option>
              {templatesForCategory.map((t) => (
                <option key={t.name} value={t.name}>{t.title}</option>
              ))}
            </select>
            {template && (
              <p className="text-xs text-muted-foreground">
                Selecting a template enforces its policy during review.
              </p>
            )}
          </div>
        </div>
      )}

      {/* File input */}
      {mode === 'file' && (
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium text-foreground">
            Document <span className="text-muted-foreground text-xs">(.pdf, .docx, .md)</span>
          </label>
          <div
            role="button"
            tabIndex={0}
            aria-label="Drop a file here or click to browse"
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
            className={cn(
              'flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-8',
              'cursor-pointer transition-colors duration-200',
              dragging
                ? 'border-primary bg-primary/5 text-primary'
                : 'border-border bg-card/40 text-muted-foreground hover:border-primary/50 hover:bg-primary/5'
            )}
          >
            {file ? (
              <div className="flex items-center gap-2 text-foreground">
                <FileText className="h-5 w-5 text-primary shrink-0" aria-hidden />
                <span className="text-sm font-medium truncate max-w-[220px]">{file.name}</span>
                <button
                  type="button"
                  aria-label="Remove file"
                  onClick={(e) => { e.stopPropagation(); setFile(null); }}
                  className="ml-1 rounded p-0.5 hover:bg-destructive/10 hover:text-destructive transition-colors"
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                </button>
              </div>
            ) : (
              <>
                <Upload className="h-8 w-8 opacity-50" aria-hidden />
                <div className="text-center">
                  <p className="text-sm font-medium">Drop file here</p>
                  <p className="text-xs text-muted-foreground mt-0.5">or click to browse</p>
                </div>
              </>
            )}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.md"
            className="sr-only"
            aria-label="Choose file"
            onChange={handleFileChange}
            data-testid="file-input"
          />
          {fileError && (
            <p role="alert" className="text-xs text-destructive">{fileError}</p>
          )}
        </div>
      )}

      {/* Text input */}
      {mode === 'text' && (
        <div className="flex flex-col gap-1.5">
          <label htmlFor="plan-text" className="text-sm font-medium text-foreground">
            Plan text
          </label>
          <Textarea
            id="plan-text"
            placeholder="Paste your product requirements, feature spec, or technical plan here…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={10}
            aria-label="Plan text"
            data-testid="plan-text-input"
          />
          <p className="text-xs text-muted-foreground text-right">
            {text.trim().length} characters
          </p>
        </div>
      )}

      {/* Error */}
      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {/* Actions */}
      <div className="flex justify-end gap-3">
        {onCancel && (
          <Button variant="outline" onClick={onCancel} disabled={loading}>
            Cancel
          </Button>
        )}
        <Button
          onClick={handleSubmit}
          disabled={!canSubmit}
          data-testid="submit-btn"
          aria-label="Ingest plan"
        >
          {loading ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
              Ingesting…
            </>
          ) : (
            <>
              <Plus className="mr-2 h-4 w-4" aria-hidden />
              Ingest plan
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
