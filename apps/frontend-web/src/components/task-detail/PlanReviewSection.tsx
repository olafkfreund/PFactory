/**
 * PlanReviewSection Component
 *
 * Displays a banner when a task is waiting for human approval of the implementation plan.
 * This is shown when status is 'human_review' and reviewReason is 'plan_review'.
 */

import { useState } from 'react';
import { Button } from '../ui/button';
import { Loader2, CheckCircle2, ClipboardCheck, FileText, Play, ExternalLink } from 'lucide-react';
import type { Task, ImplementationPlan } from '../../shared/types';
import { post } from '../../lib/api-client';
import { getAuthToken } from '../../lib/auth';

interface PlanReviewSectionProps {
  task: Task;
  /** Optional plan data - used to check if plan was already approved */
  plan?: ImplementationPlan | null;
  onApproved?: () => void;
  onResume?: () => void;
}

export function PlanReviewSection({ task, plan, onApproved, onResume }: PlanReviewSectionProps) {
  const [isApproving, setIsApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Check if plan was already approved but coding didn't start
  const isAlreadyApproved = plan?.planStatus === 'approved';

  const handleViewPlanHtml = async () => {
    try {
      // Fetch HTML with authentication
      const token = getAuthToken();
      console.log('[PlanReview] Fetching plan HTML for task:', task.id);
      const response = await fetch(`/api/tasks/${task.id}/plan-html`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      console.log('[PlanReview] Response status:', response.status, response.statusText);

      if (!response.ok) {
        let errorMsg = 'Failed to load plan HTML';
        try {
          const error = await response.json();
          errorMsg = error.detail || errorMsg;
        } catch {
          errorMsg = `${response.status} ${response.statusText}`;
        }
        console.error('[PlanReview] Error loading plan:', errorMsg);
        setError(errorMsg);
        return;
      }

      // Get HTML content
      const html = await response.text();
      console.log('[PlanReview] Received HTML, length:', html.length);

      // Create blob and open in new tab
      const blob = new Blob([html], { type: 'text/html' });
      const blobUrl = URL.createObjectURL(blob);
      const newWindow = window.open(blobUrl, '_blank');

      if (!newWindow) {
        console.error('[PlanReview] Failed to open new window - popup blocked?');
        setError('Failed to open new window. Please allow popups for this site.');
        URL.revokeObjectURL(blobUrl);
        return;
      }

      // Clean up blob URL after window loads
      newWindow.addEventListener('load', () => {
        console.log('[PlanReview] Plan HTML loaded successfully');
        URL.revokeObjectURL(blobUrl);
      });
    } catch (err) {
      console.error('[PlanReview] Exception:', err);
      setError(err instanceof Error ? err.message : 'Failed to open plan');
    }
  };

  const handleApprovePlan = async () => {
    console.log('[PlanReview] Approve button clicked, taskId:', task.id);
    setIsApproving(true);
    setError(null);

    try {
      console.log('[PlanReview] Calling approve-plan API...');
      const response = await post(`/tasks/${task.id}/approve-plan`, {
        auto_restart: true,
      });
      console.log('[PlanReview] API response:', response);

      if (response.success) {
        console.log('[PlanReview] Plan approved successfully!');
        setSuccess(true);
        onApproved?.();
      } else {
        console.error('[PlanReview] API returned error:', response.error);
        setError(response.error || 'Failed to approve plan');
      }
    } catch (err) {
      console.error('[PlanReview] Exception caught:', err);
      setError(err instanceof Error ? err.message : 'Failed to approve plan');
    } finally {
      setIsApproving(false);
    }
  };

  // Show success message after approval
  if (success) {
    return (
      <div className="p-4 border-l-4 border-green-500 bg-green-50 dark:bg-green-900/20 rounded-r-lg">
        <div className="flex items-center gap-2 text-green-700 dark:text-green-300">
          <CheckCircle2 className="h-5 w-5" />
          <span className="font-medium">Plan approved! Coding phase starting...</span>
        </div>
      </div>
    );
  }

  // Handle case where plan was approved but coding never started (inconsistent state)
  if (isAlreadyApproved) {
    return (
      <div className="p-4 border-l-4 border-orange-500 bg-orange-50 dark:bg-orange-900/20 rounded-r-lg space-y-3">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="h-5 w-5 text-orange-600 dark:text-orange-400 mt-0.5 shrink-0" />
          <div className="flex-1">
            <h3 className="font-semibold text-orange-800 dark:text-orange-200">
              Plan Already Approved
            </h3>
            <p className="text-sm text-orange-700 dark:text-orange-300 mt-1">
              This plan was approved but the coding phase didn't start automatically.
              Click Resume to continue implementation.
            </p>
          </div>
        </div>

        {error && (
          <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded pl-8">
            {error}
          </div>
        )}

        <div className="flex gap-2 pl-8">
          <Button
            onClick={onResume}
            disabled={isApproving}
            className="bg-orange-600 hover:bg-orange-700 text-white"
          >
            <Play className="mr-2 h-4 w-4" />
            Resume Coding
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 border-l-4 border-yellow-500 bg-yellow-50 dark:bg-yellow-900/20 rounded-r-lg space-y-3">
      <div className="flex items-start gap-3">
        <ClipboardCheck className="h-5 w-5 text-yellow-600 dark:text-yellow-400 mt-0.5 shrink-0" />
        <div className="flex-1">
          <h3 className="font-semibold text-yellow-800 dark:text-yellow-200">
            Plan Review Required
          </h3>
          <p className="text-sm text-yellow-700 dark:text-yellow-300 mt-1">
            The implementation plan has been created and requires your approval before coding begins.
            Review the plan in the Subtasks tab or check the test_plan.json file.
          </p>
        </div>
      </div>

      {/* Quick info about the plan */}
      {task.subtasks.length > 0 && (
        <div className="flex items-center gap-2 text-sm text-yellow-700 dark:text-yellow-300 pl-8">
          <FileText className="h-4 w-4" />
          <span>{task.subtasks.length} subtasks planned</span>
        </div>
      )}

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded pl-8">
          {error}
        </div>
      )}

      <div className="flex gap-2 pl-8">
        <Button
          onClick={handleViewPlanHtml}
          className="bg-yellow-500 hover:bg-yellow-600 text-black font-medium"
        >
          <ExternalLink className="mr-2 h-4 w-4" />
          View Plan
        </Button>
        <Button
          onClick={handleApprovePlan}
          disabled={isApproving}
          className="bg-green-500 hover:bg-green-600 text-white"
        >
          {isApproving ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Approving...
            </>
          ) : (
            <>
              <CheckCircle2 className="mr-2 h-4 w-4" />
              Approve Plan & Continue
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
