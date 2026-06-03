import { create } from 'zustand';
import type {
  GitHubInvestigationStatus,
  GitHubInvestigationResult
} from '../../shared/types';
import { loadTasks } from '../task-store';

interface InvestigationState {
  // Investigation state
  investigationStatus: GitHubInvestigationStatus;
  lastInvestigationResult: GitHubInvestigationResult | null;

  // Actions
  setInvestigationStatus: (status: GitHubInvestigationStatus) => void;
  setInvestigationResult: (result: GitHubInvestigationResult | null) => void;
  clearInvestigation: () => void;
}

export const useInvestigationStore = create<InvestigationState>((set) => ({
  // Initial state
  investigationStatus: {
    phase: 'idle',
    progress: 0,
    message: ''
  },
  lastInvestigationResult: null,

  // Actions
  setInvestigationStatus: (investigationStatus) => set({ investigationStatus }),

  setInvestigationResult: (lastInvestigationResult) => set({ lastInvestigationResult }),

  clearInvestigation: () => set({
    investigationStatus: { phase: 'idle', progress: 0, message: '' },
    lastInvestigationResult: null
  })
}));

/**
 * Start investigating a GitHub issue.
 *
 * Calls the REST endpoint directly and updates store state from the response.
 * Also creates a task from the analysis results.
 */
export async function investigateGitHubIssue(
  projectId: string,
  issueNumber: number,
  selectedCommentIds?: number[]
): Promise<void> {
  const store = useInvestigationStore.getState();
  store.setInvestigationStatus({
    phase: 'fetching',
    issueNumber,
    progress: 10,
    message: 'Fetching issue data...'
  });
  store.setInvestigationResult(null);

  try {
    store.setInvestigationStatus({
      phase: 'analyzing',
      issueNumber,
      progress: 30,
      message: 'Analyzing issue with AI...'
    });

    // Call the REST endpoint and await the response
    const response = await window.API.investigateGitHubIssue(projectId, issueNumber, selectedCommentIds);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const result = response as any;
    if (!result?.success) {
      // Defensive: even though api-client.ts now stringifies errors at the
      // boundary, guard against `result.error` ever being a non-string here.
      // Passing an object to new Error() would yield "[object Object]".
      const errMsg = typeof result?.error === 'string'
        ? result.error
        : 'Investigation request failed';
      throw new Error(errMsg);
    }

    const data = result.data;
    const analysis = data?.analysis || {};
    const issue = data?.issue || {};

    store.setInvestigationStatus({
      phase: 'creating_task',
      issueNumber,
      progress: 70,
      message: 'Creating task...'
    });

    // Create a task from the investigation results
    const title = issue.title || `GitHub Issue #${issueNumber}`;
    const description = [
      analysis.summary || issue.body || '',
      '',
      `**Source:** GitHub Issue #${issueNumber}`,
      analysis.issue_type ? `**Type:** ${analysis.issue_type}` : '',
      analysis.complexity ? `**Complexity:** ${analysis.complexity}` : '',
      analysis.suggestions?.length ? `\n**Suggestions:**\n${analysis.suggestions.map((s: string) => `- ${s}`).join('\n')}` : '',
      analysis.affected_areas?.length ? `\n**Affected Areas:**\n${analysis.affected_areas.map((a: string) => `- ${a}`).join('\n')}` : '',
    ].filter(Boolean).join('\n');

    const taskResult = await window.API.createTask(projectId, title, description, {
      sourceType: 'github',
      githubIssueNumber: issueNumber,
      complexity: analysis.complexity || undefined,
      affectedFiles: analysis.affected_areas || undefined,
      acceptanceCriteria: analysis.suggestions || undefined,
    });

    const taskId = taskResult.data?.id;

    // Refresh the task store so the new task appears in the UI
    if (taskId) {
      await loadTasks(projectId);
    }

    const investigationResult: GitHubInvestigationResult = {
      success: true,
      issueNumber,
      analysis: {
        summary: analysis.summary || '',
        proposedSolution: analysis.suggestions?.join('\n') || '',
        affectedFiles: analysis.affected_areas || [],
        estimatedComplexity: analysis.complexity || 'standard',
        acceptanceCriteria: analysis.suggestions || [],
      },
      taskId,
    };

    store.setInvestigationResult(investigationResult);
    store.setInvestigationStatus({
      phase: 'complete',
      issueNumber,
      progress: 100,
      message: taskId ? 'Task created successfully' : 'Investigation complete'
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Investigation failed';
    store.setInvestigationStatus({
      phase: 'error',
      issueNumber,
      progress: 0,
      message,
      // Populate `error` so InvestigationDialog's red error band has visible
      // text (it renders `{investigationStatus.error}`, which was previously
      // left undefined and rendered as an empty red box).
      error: message,
    });
    store.setInvestigationResult({
      success: false,
      issueNumber,
      analysis: {
        summary: '',
        proposedSolution: '',
        affectedFiles: [],
        estimatedComplexity: 'standard',
        acceptanceCriteria: [],
      },
      error: message,
    });
  }
}
