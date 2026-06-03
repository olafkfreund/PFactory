import { useState, useEffect, useCallback, useMemo } from 'react';
import type {
  PRData,
  PRReviewResult,
  PRReviewProgress,
  NewCommitsCheck
} from '../../../shared/types/github-api';
import { usePRReviewStore, startPRReview as storeStartPRReview, startFollowupReview as storeStartFollowupReview } from '../../../stores/github/index';

// Re-export types for consumers
export type { PRData, PRReviewResult, PRReviewProgress };
export type { PRReviewFinding } from '../../../shared/types/github-api';

interface UseGitHubPRsResult {
  prs: PRData[];
  isLoading: boolean;
  isInitialLoading: boolean;
  error: string | null;
  selectedPR: PRData | null;
  selectedPRNumber: number | null;
  reviewResult: PRReviewResult | null;
  reviewProgress: PRReviewProgress | null;
  isReviewing: boolean;
  isConnected: boolean;
  repoFullName: string | null;
  activePRReviews: number[]; // PR numbers currently being reviewed
  selectPR: (prNumber: number | null) => void;
  refresh: () => Promise<void>;
  runReview: (prNumber: number) => Promise<void>;
  runFollowupReview: (prNumber: number) => Promise<void>;
  checkNewCommits: (prNumber: number) => Promise<NewCommitsCheck>;
  cancelReview: (prNumber: number) => Promise<boolean>;
  postReview: (prNumber: number, selectedFindingIds?: string[]) => Promise<boolean>;
  postComment: (prNumber: number, body: string) => Promise<boolean>;
  approvePR: (prNumber: number, body: string) => Promise<boolean>;
  mergePR: (prNumber: number, mergeMethod?: 'merge' | 'squash' | 'rebase') => Promise<boolean>;
  assignPR: (prNumber: number, username: string) => Promise<boolean>;
  getReviewStateForPR: (prNumber: number) => { isReviewing: boolean; progress: PRReviewProgress | null; result: PRReviewResult | null; previousResult: PRReviewResult | null; error: string | null; newCommitsCheck?: NewCommitsCheck | null } | null;
}

export function useGitHubPRs(projectId?: string): UseGitHubPRsResult {
  const [prs, setPrs] = useState<PRData[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPRNumber, setSelectedPRNumber] = useState<number | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [repoFullName, setRepoFullName] = useState<string | null>(null);

  // Get PR review state from the global store
  const prReviews = usePRReviewStore((state) => state.prReviews);
  const getPRReviewState = usePRReviewStore((state) => state.getPRReviewState);
  const getActivePRReviews = usePRReviewStore((state) => state.getActivePRReviews);
  const setNewCommitsCheckAction = usePRReviewStore((state) => state.setNewCommitsCheck);

  // Get review state for the selected PR from the store
  const selectedPRReviewState = useMemo(() => {
    if (!projectId || selectedPRNumber === null) return null;
    return getPRReviewState(projectId, selectedPRNumber);
  }, [projectId, selectedPRNumber, prReviews, getPRReviewState]);

  // Derive values from store state
  const reviewResult = selectedPRReviewState?.result ?? null;
  const reviewProgress = selectedPRReviewState?.progress ?? null;
  const isReviewing = selectedPRReviewState?.isReviewing ?? false;

  // Get list of PR numbers currently being reviewed
  const activePRReviews = useMemo(() => {
    if (!projectId) return [];
    return getActivePRReviews(projectId).map(review => review.prNumber);
  }, [projectId, prReviews, getActivePRReviews]);

  // Helper to get review state for any PR
  const getReviewStateForPR = useCallback((prNumber: number) => {
    if (!projectId) return null;
    const state = getPRReviewState(projectId, prNumber);
    if (!state) return null;
    return {
      isReviewing: state.isReviewing,
      progress: state.progress,
      result: state.result,
      previousResult: state.previousResult,
      error: state.error,
      newCommitsCheck: state.newCommitsCheck
    };
  }, [projectId, prReviews, getPRReviewState]);

  const selectedPR = prs.find(pr => pr.number === selectedPRNumber) || null;

  // Check connection and fetch PRs
  const fetchPRs = useCallback(async () => {
    if (!projectId) return;

    setIsLoading(true);
    setError(null);

    try {
      // First check connection
      const connectionResult = await window.API.checkGitHubConnection(projectId);
      if (connectionResult.success && connectionResult.data) {
        setIsConnected(connectionResult.data.connected);
        setRepoFullName(connectionResult.data.repoFullName || null);

        if (connectionResult.data.connected) {
          // Fetch PRs
          const result = await window.API.github.listPRs(projectId);
          if (result) {
            setPrs(result);

            // Preload review results for all PRs
            const preloadPromises = result.map(async (pr) => {
              const existingState = getPRReviewState(projectId, pr.number);
              // Only fetch from disk if we don't have a result in the store
              if (!existingState?.result) {
                const reviewResult = await window.API.github.getPRReview(projectId, pr.number);
                if (reviewResult) {
                  // Update store with the loaded result
                  // Preserve newCommitsCheck during preload to avoid race condition with new commits check
                  usePRReviewStore.getState().setPRReviewResult(projectId, reviewResult, { preserveNewCommitsCheck: true });
                  return { prNumber: pr.number, reviewResult };
                }
              } else {
                return { prNumber: pr.number, reviewResult: existingState.result };
              }
              return null;
            });

            // Wait for all preloads to complete, then check for new commits
            const preloadResults = await Promise.all(preloadPromises);

            // Check for new commits on PRs that have been reviewed
            const prsWithReviews = preloadResults.filter(
              (r): r is { prNumber: number; reviewResult: PRReviewResult } =>
                r !== null && !!r.reviewResult?.reviewedCommitSha
            );

            if (prsWithReviews.length > 0) {
              // Check new commits in parallel for all reviewed PRs
              await Promise.all(
                prsWithReviews.map(async ({ prNumber }) => {
                  try {
                    const newCommitsResult = await window.API.github.checkNewCommits(projectId, prNumber);
                    // Use the action from the hook subscription to ensure proper React re-renders
                    setNewCommitsCheckAction(projectId, prNumber, newCommitsResult);
                  } catch (err) {
                    // Silently fail for individual PR checks - don't block the list
                    console.warn(`Failed to check new commits for PR #${prNumber}:`, err);
                  }
                })
              );
            }
          }
        }
      } else {
        setIsConnected(false);
        setRepoFullName(null);
        setError(connectionResult.error || 'Failed to check connection');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch PRs');
      setIsConnected(false);
    } finally {
      setIsLoading(false);
      setIsInitialLoading(false);
    }
  }, [projectId, getPRReviewState, setNewCommitsCheckAction]);

  useEffect(() => {
    fetchPRs();
  }, [fetchPRs]);

  // No need for local IPC listeners - they're handled globally in github-store

  const selectPR = useCallback((prNumber: number | null) => {
    setSelectedPRNumber(prNumber);
    // Note: Don't reset review result - it comes from the store now
    // and persists across navigation

    // Load existing review from disk if not already in store
    if (prNumber && projectId) {
      const existingState = getPRReviewState(projectId, prNumber);
      // Only fetch from disk if we don't have a result in the store
      if (!existingState?.result) {
        window.API.github.getPRReview(projectId, prNumber).then(result => {
          if (result) {
            // Update store with the loaded result
            // Preserve newCommitsCheck when loading existing review from disk
            usePRReviewStore.getState().setPRReviewResult(projectId, result, { preserveNewCommitsCheck: true });
          }
        });
      }

      // Always check for new commits when selecting a PR
      window.API.github.checkNewCommits(projectId, prNumber).then(result => {
        setNewCommitsCheckAction(projectId, prNumber, result);
      }).catch(() => {});
    }
  }, [projectId, getPRReviewState, setNewCommitsCheckAction]);

  const refresh = useCallback(async () => {
    await fetchPRs();
  }, [fetchPRs]);

  const runReview = useCallback(async (prNumber: number) => {
    if (!projectId) return;

    // Use the store function which handles both state and IPC
    storeStartPRReview(projectId, prNumber);
  }, [projectId]);

  const runFollowupReview = useCallback(async (prNumber: number) => {
    if (!projectId) return;

    // Use the store function which handles both state and IPC
    storeStartFollowupReview(projectId, prNumber);
  }, [projectId]);

  const checkNewCommits = useCallback(async (prNumber: number): Promise<NewCommitsCheck> => {
    if (!projectId) {
      return { hasNewCommits: false, newCommitCount: 0 };
    }

    try {
      const result = await window.API.github.checkNewCommits(projectId, prNumber);
      // Cache the result in the store so the list view can use it
      // Use the action from the hook subscription to ensure proper React re-renders
      setNewCommitsCheckAction(projectId, prNumber, result);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check for new commits');
      return { hasNewCommits: false, newCommitCount: 0 };
    }
  }, [projectId, setNewCommitsCheckAction]);

  const cancelReview = useCallback(async (prNumber: number): Promise<boolean> => {
    if (!projectId) return false;

    try {
      const success = await window.API.github.cancelPRReview(projectId, prNumber);
      if (success) {
        // Update store to mark review as cancelled
        usePRReviewStore.getState().setPRReviewError(projectId, prNumber, 'Review cancelled by user');
      }
      return success;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel review');
      return false;
    }
  }, [projectId]);

  const postReview = useCallback(async (prNumber: number, selectedFindingIds?: string[]): Promise<boolean> => {
    if (!projectId) return false;

    try {
      const success = await window.API.github.postPRReview(projectId, prNumber, selectedFindingIds);
      if (success) {
        // Reload review result to get updated postedAt and finding status
        const result = await window.API.github.getPRReview(projectId, prNumber);
        if (result) {
          // Preserve newCommitsCheck - posting doesn't change whether there are new commits
          usePRReviewStore.getState().setPRReviewResult(projectId, result, { preserveNewCommitsCheck: true });
        }
      }
      return success;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post review');
      return false;
    }
  }, [projectId]);

  const postComment = useCallback(async (prNumber: number, body: string): Promise<boolean> => {
    if (!projectId) return false;

    try {
      return await window.API.github.postPRComment(projectId, prNumber, body);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to post comment');
      return false;
    }
  }, [projectId]);

  const approvePR = useCallback(async (prNumber: number, body: string): Promise<boolean> => {
    if (!projectId) return false;

    try {
      return await window.API.github.approvePR(projectId, prNumber, body);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve PR');
      return false;
    }
  }, [projectId]);

  const mergePR = useCallback(async (prNumber: number, mergeMethod: 'merge' | 'squash' | 'rebase' = 'squash'): Promise<boolean> => {
    if (!projectId) return false;

    try {
      const success = await window.API.github.mergePR(projectId, prNumber, mergeMethod);
      if (success) {
        // Refresh PR list after merge
        await fetchPRs();
      }
      return success;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to merge PR');
      return false;
    }
  }, [projectId, fetchPRs]);

  const assignPR = useCallback(async (prNumber: number, username: string): Promise<boolean> => {
    if (!projectId) return false;

    try {
      const success = await window.API.github.assignPR(projectId, prNumber, username);
      if (success) {
        // Refresh PR list to update assignees
        await fetchPRs();
      }
      return success;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to assign user');
      return false;
    }
  }, [projectId, fetchPRs]);

  return {
    prs,
    isLoading,
    isInitialLoading,
    error,
    selectedPR,
    selectedPRNumber,
    reviewResult,
    reviewProgress,
    isReviewing,
    isConnected,
    repoFullName,
    activePRReviews,
    selectPR,
    refresh,
    runReview,
    runFollowupReview,
    checkNewCommits,
    cancelReview,
    postReview,
    postComment,
    approvePR,
    mergePR,
    assignPR,
    getReviewStateForPR,
  };
}
