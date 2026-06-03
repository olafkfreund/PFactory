import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { GitPullRequestCreateArrow, Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '../../ui/dialog';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Textarea } from '../../ui/textarea';
import { Label } from '../../ui/label';
import { RadioGroup, RadioGroupItem } from '../../ui/radio-group';
import { Checkbox } from '../../ui/checkbox';
import { toast } from '../../../hooks/use-toast';
import type { Task } from '../../../shared/types';

interface ForkInfo {
  isFork: boolean;
  origin: string;
  defaultBranch: string;
  upstream?: string;
  upstreamDefaultBranch?: string;
}

interface CreatePRDialogProps {
  open: boolean;
  task: Task;
  projectPath: string;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (prUrl: string) => void;
  onError?: (error: string) => void;
}

export function CreatePRDialog({
  open,
  task,
  projectPath,
  onOpenChange,
  onSuccess,
  onError,
}: CreatePRDialogProps) {
  const { t } = useTranslation(['tasks']);

  const [forkInfo, setForkInfo] = useState<ForkInfo | null>(null);
  const [isLoadingFork, setIsLoadingFork] = useState(false);
  const [forkError, setForkError] = useState<string | null>(null);

  const [targetRepo, setTargetRepo] = useState<'origin' | 'upstream'>('origin');
  const [baseBranch, setBaseBranch] = useState('');
  const [prTitle, setPrTitle] = useState('');
  const [prBody, setPrBody] = useState('');
  const [isDraft, setIsDraft] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Fetch fork info when dialog opens
  useEffect(() => {
    if (!open || !projectPath) return;

    setIsLoadingFork(true);
    setForkError(null);

    window.API.getForkInfo(projectPath)
      .then((result) => {
        if (result.success && result.data) {
          setForkInfo(result.data);
          setBaseBranch(result.data.defaultBranch || 'main');
        } else {
          setForkError(result.error || t('tasks:createPR.forkDetectionFailed'));
          // Default to main if detection fails
          setBaseBranch('main');
        }
      })
      .catch(() => {
        setForkError(t('tasks:createPR.forkDetectionFailed'));
        setBaseBranch('main');
      })
      .finally(() => setIsLoadingFork(false));
  }, [open, projectPath, t]);

  // Pre-fill title and description from task when dialog opens
  useEffect(() => {
    if (open) {
      setPrTitle(task.title || '');
      setPrBody(task.description || '');
      setTargetRepo('origin');
      setIsDraft(false);
    }
  }, [open, task.title, task.description]);

  // Update baseBranch when target repo changes
  useEffect(() => {
    if (!forkInfo) return;
    if (targetRepo === 'upstream' && forkInfo.upstreamDefaultBranch) {
      setBaseBranch(forkInfo.upstreamDefaultBranch);
    } else {
      setBaseBranch(forkInfo.defaultBranch || 'main');
    }
  }, [targetRepo, forkInfo]);

  const handleSubmit = async () => {
    setIsSubmitting(true);
    try {
      const options: {
        title?: string;
        body?: string;
        draft?: boolean;
        baseBranch?: string;
        targetRepo?: string;
      } = {
        title: prTitle || undefined,
        body: prBody || undefined,
        draft: isDraft,
        baseBranch: baseBranch || undefined,
      };

      if (targetRepo === 'upstream' && forkInfo?.upstream) {
        options.targetRepo = forkInfo.upstream;
      }

      const result = await window.API.createPRFromTask(task.id, options);
      if (result.success && result.data) {
        toast({
          title: t('tasks:createPR.success'),
          description: result.data.prUrl
            ? `PR #${result.data.prNumber} ${t('tasks:createPR.createdSuccessfully')}`
            : t('tasks:createPR.createdSuccessfully'),
        });
        if (result.data.prUrl) {
          window.API.openExternal(result.data.prUrl);
        }
        onSuccess?.(result.data.prUrl);
        onOpenChange(false);
      } else {
        const errorMsg = result.error || t('tasks:createPR.failed');
        onError?.(errorMsg);
        toast({
          variant: 'destructive',
          title: t('tasks:createPR.failed'),
          description: errorMsg,
        });
      }
    } catch (err) {
      const errorMsg = `${t('tasks:createPR.failed')}: ${err}`;
      onError?.(errorMsg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitPullRequestCreateArrow className="h-5 w-5" />
            {t('tasks:createPR.title')}
          </DialogTitle>
          <DialogDescription>
            {t('tasks:createPR.description')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Target Repository */}
          <div className="space-y-2">
            <Label className="text-sm font-medium">{t('tasks:createPR.targetRepo')}</Label>
            {isLoadingFork ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t('tasks:createPR.detectingFork')}
              </div>
            ) : forkError ? (
              <p className="text-xs text-muted-foreground">{forkError}</p>
            ) : (
              <RadioGroup
                value={targetRepo}
                onValueChange={(v) => setTargetRepo(v as 'origin' | 'upstream')}
                className="space-y-2"
              >
                <label className="flex items-center gap-3 p-2.5 rounded-lg border border-border hover:bg-muted/50 cursor-pointer transition-colors">
                  <RadioGroupItem value="origin" id="target-origin" />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium">{t('tasks:createPR.yourRepo')}</span>
                    {forkInfo?.origin && (
                      <span className="text-xs text-muted-foreground ml-2">({forkInfo.origin})</span>
                    )}
                  </div>
                </label>

                {forkInfo?.isFork && forkInfo.upstream && (
                  <label className="flex items-center gap-3 p-2.5 rounded-lg border border-border hover:bg-muted/50 cursor-pointer transition-colors">
                    <RadioGroupItem value="upstream" id="target-upstream" />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-medium">{t('tasks:createPR.upstreamRepo')}</span>
                      <span className="text-xs text-muted-foreground ml-2">({forkInfo.upstream})</span>
                    </div>
                  </label>
                )}
              </RadioGroup>
            )}
          </div>

          {/* Base Branch */}
          <div className="space-y-1.5">
            <Label htmlFor="pr-base-branch" className="text-sm font-medium">
              {t('tasks:createPR.baseBranch')}
            </Label>
            <Input
              id="pr-base-branch"
              value={baseBranch}
              onChange={(e) => setBaseBranch(e.target.value)}
              placeholder="main"
            />
          </div>

          {/* PR Title */}
          <div className="space-y-1.5">
            <Label htmlFor="pr-title" className="text-sm font-medium">
              {t('tasks:createPR.prTitle')}
            </Label>
            <Input
              id="pr-title"
              value={prTitle}
              onChange={(e) => setPrTitle(e.target.value)}
              placeholder={task.title}
            />
          </div>

          {/* PR Description */}
          <div className="space-y-1.5">
            <Label htmlFor="pr-description" className="text-sm font-medium">
              {t('tasks:createPR.prDescription')}
            </Label>
            <Textarea
              id="pr-description"
              value={prBody}
              onChange={(e) => setPrBody(e.target.value)}
              rows={4}
              className="resize-none"
            />
          </div>

          {/* Draft checkbox */}
          <label className="inline-flex items-center gap-2.5 text-sm cursor-pointer select-none">
            <Checkbox
              checked={isDraft}
              onCheckedChange={(checked) => setIsDraft(checked === true)}
            />
            <span className="text-muted-foreground">{t('tasks:createPR.draft')}</span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            {t('tasks:createPR.cancel')}
          </Button>
          <Button onClick={handleSubmit} disabled={isSubmitting || isLoadingFork}>
            {isSubmitting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {t('tasks:createPR.creating')}
              </>
            ) : (
              <>
                <GitPullRequestCreateArrow className="mr-2 h-4 w-4" />
                {t('tasks:createPR.submit')}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
