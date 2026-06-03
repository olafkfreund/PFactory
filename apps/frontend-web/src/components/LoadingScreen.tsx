import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

interface LoadingScreenProps {
  duration?: number;
  onComplete?: () => void;
}

export function LoadingScreen({ duration = 5000, onComplete }: LoadingScreenProps) {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const startTime = Date.now();
    const interval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const newProgress = Math.min((elapsed / duration) * 100, 100);
      setProgress(newProgress);

      if (elapsed >= duration) {
        clearInterval(interval);
        onComplete?.();
      }
    }, 50);

    return () => clearInterval(interval);
  }, [duration, onComplete]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background">
      {/* Logo and branding */}
      <div className="mb-8 flex flex-col items-center">
        <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-2xl">
          <img src="/logo.svg" alt="PFactory" className="h-20 w-20 rounded-2xl" />
        </div>
        <h1 className="text-2xl font-bold text-foreground">PFactory</h1>
        <p className="mt-2 text-sm text-muted-foreground">Preparing your workspace...</p>
      </div>

      {/* Progress bar */}
      <div className="w-64">
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all duration-100 ease-linear"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="mt-2 text-center text-xs text-muted-foreground">
          {Math.round(progress)}%
        </p>
      </div>

      {/* Loading tips */}
      <div className="mt-8 max-w-md px-4 text-center">
        <p className="text-sm text-muted-foreground">
          {progress < 30 && 'Initializing AI agents...'}
          {progress >= 30 && progress < 60 && 'Loading project data...'}
          {progress >= 60 && progress < 90 && 'Syncing task states...'}
          {progress >= 90 && 'Almost ready...'}
        </p>
      </div>
    </div>
  );
}
