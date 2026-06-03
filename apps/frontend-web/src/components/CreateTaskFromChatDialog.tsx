import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { Label } from './ui/label';

interface CreateTaskFromChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialTitle: string;
  initialDescription: string;
  isGenerating: boolean;
  onConfirm: (title: string, description: string) => void;
  isCreating: boolean;
}

export function CreateTaskFromChatDialog({
  open,
  onOpenChange,
  initialTitle,
  initialDescription,
  isGenerating,
  onConfirm,
  isCreating,
}: CreateTaskFromChatDialogProps) {
  const { t } = useTranslation(['common']);

  const [title, setTitle] = useState(initialTitle);
  const [description, setDescription] = useState(initialDescription);

  // Sync local state when initial values change (after generation completes)
  useEffect(() => {
    setTitle(initialTitle);
  }, [initialTitle]);

  useEffect(() => {
    setDescription(initialDescription);
  }, [initialDescription]);

  const handleConfirm = () => {
    onConfirm(title.trim(), description.trim());
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[550px]">
        <DialogHeader>
          <DialogTitle>{t('common:insights.createTask.dialogTitle')}</DialogTitle>
          <DialogDescription>
            {t('common:insights.createTask.dialogDescription')}
          </DialogDescription>
        </DialogHeader>

        {isGenerating ? (
          <div className="flex flex-col items-center justify-center gap-3 py-12">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">
              {t('common:insights.createTask.generating')}
            </p>
          </div>
        ) : (
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="task-title">
                {t('common:insights.createTask.titleLabel')}
              </Label>
              <Input
                id="task-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t('common:insights.createTask.titlePlaceholder')}
                disabled={isCreating}
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="task-description">
                {t('common:insights.createTask.descriptionLabel')}
              </Label>
              <Textarea
                id="task-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t('common:insights.createTask.descriptionPlaceholder')}
                disabled={isCreating}
                className="min-h-[200px] resize-y"
              />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isCreating}
          >
            {t('common:insights.createTask.cancel')}
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={isGenerating || isCreating || !title.trim()}
          >
            {isCreating ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {t('common:insights.createTask.creating')}
              </>
            ) : (
              t('common:insights.createTask.confirm')
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
