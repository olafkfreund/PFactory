import { useEffect, useState } from 'react';
import { useProjectStore, loadProjects } from '../stores/project-store';

/** Load projects for routes outside AuthenticatedApp on a cold visit. */
export function useEnsureProjects(): boolean {
  const have = useProjectStore((s) => s.projects.length > 0);
  const [done, setDone] = useState(have);
  useEffect(() => {
    if (have) {
      setDone(true);
      return;
    }
    let cancelled = false;
    void loadProjects().finally(() => { if (!cancelled) setDone(true); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount
  }, []);
  return done;
}
