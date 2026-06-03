import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, CheckCircle2, AlertCircle, RotateCcw, Check } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { cn } from '../lib/utils';

interface ClarificationQuestion {
  id: string;
  question: string;
  options: string[];
}

interface TaskClarificationWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  taskId: string;
  taskTitle: string;
  taskDescription: string;
  projectId: string;
}

export function TaskClarificationWizard({
  open,
  onOpenChange,
  taskId,
  taskTitle,
  taskDescription,
  projectId,
}: TaskClarificationWizardProps) {
  const { t } = useTranslation(['common']);

  const [isLoading, setIsLoading] = useState(false);
  const [questions, setQuestions] = useState<ClarificationQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [skip, setSkip] = useState(false);
  const [skipReason, setSkipReason] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchQuestions = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setQuestions([]);
    setAnswers({});
    setSkip(false);
    setSkipReason('');

    try {
      const result = await window.API.generateClarifications(taskId);
      if (result.success && result.data) {
        const data = result.data;
        if (data.skip || !data.questions?.length) {
          setSkip(true);
          setSkipReason(data.skipReason || t('common:clarificationWizard.noQuestionsMessage'));
        } else {
          setQuestions(data.questions);
          const initial: Record<string, string> = {};
          for (const q of data.questions) {
            initial[q.id] = '';
          }
          setAnswers(initial);
        }
      } else {
        setError(result.error || t('common:clarificationWizard.errorGenerating'));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common:clarificationWizard.errorGenerating'));
    } finally {
      setIsLoading(false);
    }
  }, [taskId, t]);

  useEffect(() => {
    if (open && taskId) {
      fetchQuestions();
    }
  }, [open, taskId, fetchQuestions]);

  const handleSelectOption = useCallback((questionId: string, option: string) => {
    setAnswers(prev => ({
      ...prev,
      [questionId]: prev[questionId] === option ? '' : option,
    }));
  }, []);

  const hasAnyAnswer = Object.values(answers).some(a => a.trim().length > 0);

  const handleSubmit = useCallback(async () => {
    const answeredQuestions = questions
      .filter(q => answers[q.id]?.trim())
      .map(q => ({
        questionId: q.id,
        question: q.question,
        answer: answers[q.id].trim(),
      }));

    if (answeredQuestions.length === 0) {
      onOpenChange(false);
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await window.API.submitClarificationAnswers(taskId, answeredQuestions);
      if (result.success) {
        onOpenChange(false);
      } else {
        setError(result.error || t('common:clarificationWizard.errorSubmitting'));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common:clarificationWizard.errorSubmitting'));
    } finally {
      setIsSubmitting(false);
    }
  }, [questions, answers, taskId, onOpenChange, t]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[550px]">
        <DialogHeader>
          <DialogTitle>{t('common:clarificationWizard.title')}</DialogTitle>
          <DialogDescription>
            {t('common:clarificationWizard.description')}
          </DialogDescription>
        </DialogHeader>

        {/* Loading state */}
        {isLoading && (
          <div className="flex flex-col items-center justify-center gap-3 py-12">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">
              {t('common:clarificationWizard.analyzing')}
            </p>
          </div>
        )}

        {/* Error state */}
        {!isLoading && error && (
          <div className="flex flex-col items-center justify-center gap-3 py-8">
            <AlertCircle className="h-8 w-8 text-destructive" />
            <p className="text-sm text-muted-foreground">{error}</p>
            <Button variant="outline" size="sm" onClick={fetchQuestions}>
              <RotateCcw className="mr-2 h-4 w-4" />
              {t('common:clarificationWizard.retry')}
            </Button>
          </div>
        )}

        {/* Skip state */}
        {!isLoading && !error && skip && (
          <div className="flex flex-col items-center justify-center gap-3 py-8">
            <CheckCircle2 className="h-10 w-10 text-green-500" />
            <p className="text-sm font-medium">
              {t('common:clarificationWizard.noQuestionsTitle')}
            </p>
            <p className="text-center text-sm text-muted-foreground">
              {skipReason}
            </p>
          </div>
        )}

        {/* Questions with multiple-choice options */}
        {!isLoading && !error && !skip && questions.length > 0 && (
          <div className="max-h-[400px] space-y-5 overflow-y-auto py-4 pr-1">
            {questions.map((q, index) => (
              <div key={q.id} className="space-y-2">
                <Label className="text-sm font-medium">
                  {index + 1}. {q.question}
                </Label>
                <div className="grid gap-2">
                  {q.options.map((option) => {
                    const isSelected = answers[q.id] === option;
                    return (
                      <button
                        key={option}
                        type="button"
                        disabled={isSubmitting}
                        onClick={() => handleSelectOption(q.id, option)}
                        className={cn(
                          'flex items-center gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors',
                          isSelected
                            ? 'border-primary bg-primary/10 text-primary'
                            : 'border-border bg-background text-foreground hover:border-primary/50 hover:bg-muted',
                          isSubmitting && 'cursor-not-allowed opacity-50'
                        )}
                      >
                        <div className={cn(
                          'flex h-4 w-4 shrink-0 items-center justify-center rounded-full border',
                          isSelected ? 'border-primary bg-primary' : 'border-muted-foreground'
                        )}>
                          {isSelected && <Check className="h-3 w-3 text-primary-foreground" />}
                        </div>
                        <span>{option}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        <DialogFooter>
          {!isLoading && (
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
            >
              {skip || error
                ? t('common:clarificationWizard.close')
                : t('common:clarificationWizard.skipClarifications')
              }
            </Button>
          )}

          {!isLoading && !error && !skip && questions.length > 0 && (
            <Button
              onClick={handleSubmit}
              disabled={isSubmitting || !hasAnyAnswer}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {t('common:clarificationWizard.submitting')}
                </>
              ) : (
                t('common:clarificationWizard.submitAnswers')
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
