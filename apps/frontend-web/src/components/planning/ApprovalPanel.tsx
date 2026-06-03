/**
 * ApprovalPanel — Approve / Reject controls.
 *
 * Approve button is disabled unless review.gates_passed is true.
 * Shows 409 "gates not passed" errors inline.
 * Displays human_approval.valid + plan_hash if present.
 */

import { useState } from 'react';
import { CheckCircle2, XCircle, Shield, Hash, Loader2 } from 'lucide-react';
import { Button } from '../ui/button';
import { Textarea } from '../ui/textarea';
import { Badge } from '../ui/badge';
import { Input } from '../ui/input';
import { cn } from '../../lib/utils';
import { usePlanStore } from '../../stores/plan-store';
import type { PlanSession } from '../../shared/types/plan';

interface Props {
  session: PlanSession;
  onUpdated?: (session: PlanSession) => void;
}

export function ApprovalPanel({ session, onUpdated }: Props) {
  const store = usePlanStore();
  const { sessionLoading, error } = store;

  const [approver, setApprover] = useState('');
  const [feedback, setFeedback] = useState('');
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const review = session.review;
  const approval = review?.human_approval;
  const gatesPassed = review?.gates_passed ?? false;

  const handleApprove = async () => {
    if (!approver.trim()) {
      setLocalError('Approver name is required.');
      return;
    }
    setLocalError(null);
    store.clearError();
    try {
      await store.approveCurrentSession({
        approver: approver.trim(),
        feedback: feedback.trim() || undefined,
      });
      const updated = store.currentSession;
      if (updated) onUpdated?.(updated);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Surface 409 as a "gates not passed" message
      setLocalError(msg);
    }
  };

  const handleReject = async () => {
    if (!approver.trim()) {
      setLocalError('Approver name is required.');
      return;
    }
    if (!rejectFeedback.trim()) {
      setLocalError('Feedback is required when rejecting.');
      return;
    }
    setLocalError(null);
    store.clearError();
    try {
      await store.rejectCurrentSession({
        approver: approver.trim(),
        feedback: rejectFeedback.trim(),
      });
      const updated = store.currentSession;
      if (updated) onUpdated?.(updated);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  const isApproved = session.status === 'approved';
  const isRejected = session.status === 'rejected';
  const displayError = localError ?? error;

  return (
    <div className="flex flex-col gap-5" data-testid="approval-panel">
      {/* Human approval status */}
      {approval && (
        <div className="flex flex-col gap-2 rounded-lg border border-border/60 bg-card/40 px-4 py-3">
          <div className="flex items-center gap-3">
            <Shield className="h-4 w-4 text-muted-foreground" aria-hidden />
            <span className="text-sm font-medium text-foreground">Human Approval</span>
            <Badge
              variant={approval.valid ? 'success' : 'warning'}
              data-testid="approval-valid-badge"
            >
              {approval.valid ? 'Valid' : 'Invalidated'}
            </Badge>
          </div>
          {approval.approved_by && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground ml-7">
              <span>Approved by <strong className="text-foreground">{approval.approved_by}</strong></span>
              {approval.approved_at && (
                <span>· {new Date(approval.approved_at).toLocaleString()}</span>
              )}
            </div>
          )}
          {approval.plan_hash && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground ml-7">
              <Hash className="h-3 w-3" aria-hidden />
              <span className="font-mono truncate">{approval.plan_hash.slice(0, 16)}…</span>
            </div>
          )}
          {approval.feedback.length > 0 && (
            <div className="ml-7 text-xs text-muted-foreground">
              <p className="font-medium text-foreground mb-1">Feedback:</p>
              <ul className="space-y-0.5">
                {approval.feedback.map((f, i) => (
                  <li key={i} className="list-disc ml-4">{f}</li>
                ))}
              </ul>
            </div>
          )}
          {approval.review_count > 0 && (
            <p className="ml-7 text-xs text-muted-foreground">
              Review count: {approval.review_count}
            </p>
          )}
        </div>
      )}

      {/* Final status */}
      {(isApproved || isRejected) && (
        <div
          className={cn(
            'flex items-center gap-3 rounded-lg border px-4 py-3',
            isApproved
              ? 'border-success/30 bg-success/5 text-success'
              : 'border-destructive/30 bg-destructive/5 text-destructive',
          )}
          role="status"
          aria-label={isApproved ? 'Session approved' : 'Session rejected'}
        >
          {isApproved
            ? <CheckCircle2 className="h-5 w-5 shrink-0" aria-hidden />
            : <XCircle className="h-5 w-5 shrink-0" aria-hidden />
          }
          <span className="text-sm font-medium">
            {isApproved ? 'Session approved' : 'Session rejected'}
          </span>
        </div>
      )}

      {/* Gates not passed warning */}
      {!gatesPassed && review && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-sm text-warning"
          data-testid="gates-warning"
        >
          <span className="font-medium">Review gates have not passed.</span>
          <span className="text-xs opacity-80">Approval is disabled until all blocking findings are resolved.</span>
        </div>
      )}

      {/* Approve / Reject form */}
      {!isApproved && !isRejected && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="approver-name" className="text-sm font-medium text-foreground">
              Your name <span className="text-destructive">*</span>
            </label>
            <Input
              id="approver-name"
              placeholder="e.g. Jane Smith"
              value={approver}
              onChange={(e) => setApprover(e.target.value)}
              aria-label="Approver name"
              data-testid="approver-input"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="approve-feedback" className="text-sm font-medium text-foreground">
              Feedback <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <Textarea
              id="approve-feedback"
              placeholder="Optional comment…"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
              aria-label="Approval feedback"
            />
          </div>

          {displayError && (
            <div role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {displayError}
            </div>
          )}

          <div className="flex gap-3">
            <Button
              onClick={handleApprove}
              disabled={!gatesPassed || sessionLoading || !approver.trim()}
              className="flex-1"
              data-testid="approve-btn"
              aria-label="Approve session"
            >
              {sessionLoading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <CheckCircle2 className="mr-2 h-4 w-4" aria-hidden />
              )}
              Approve
            </Button>
            <Button
              variant="outline"
              onClick={() => setShowRejectForm((v) => !v)}
              disabled={sessionLoading}
              data-testid="reject-toggle-btn"
              aria-label="Show reject form"
            >
              <XCircle className="mr-2 h-4 w-4 text-destructive" aria-hidden />
              Reject
            </Button>
          </div>

          {showRejectForm && (
            <div className="flex flex-col gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
              <p className="text-sm font-semibold text-destructive">Reject session</p>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="reject-feedback" className="text-sm font-medium text-foreground">
                  Reason <span className="text-destructive">*</span>
                </label>
                <Textarea
                  id="reject-feedback"
                  placeholder="Explain why this plan is being rejected…"
                  value={rejectFeedback}
                  onChange={(e) => setRejectFeedback(e.target.value)}
                  rows={3}
                  aria-label="Rejection reason"
                  data-testid="reject-feedback-input"
                />
              </div>
              <Button
                variant="destructive"
                onClick={handleReject}
                disabled={sessionLoading || !approver.trim() || !rejectFeedback.trim()}
                data-testid="reject-confirm-btn"
                aria-label="Confirm rejection"
              >
                {sessionLoading ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <XCircle className="mr-2 h-4 w-4" aria-hidden />
                )}
                Confirm rejection
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
