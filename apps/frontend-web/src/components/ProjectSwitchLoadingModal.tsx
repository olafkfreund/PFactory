import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface ProjectSwitchLoadingModalProps {
  open: boolean;
  projectName?: string;
}

export function ProjectSwitchLoadingModal({
  open,
  projectName
}: ProjectSwitchLoadingModalProps) {
  const { t } = useTranslation(['common']);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-4 rounded-2xl bg-card border border-border p-8 shadow-xl">
        <Loader2 className="h-10 w-10 animate-spin text-primary" />
        <div className="text-center">
          <p className="text-sm font-medium text-foreground">
            {t('common:loading.switchingProject', { projectName: projectName || 'project' })}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            {t('common:loading.loadingTasks')}
          </p>
        </div>
      </div>
    </div>
  );
}
