/**
 * ReviewFindings - Interactive findings display with selection and filtering
 *
 * Features:
 * - Grouped by severity (Critical/High vs Medium/Low)
 * - Checkboxes for selecting which findings to post
 * - Quick select actions (Critical/High, All, None)
 * - Collapsible sections for less important findings
 * - Visual summary of finding counts
 */

import { useState, useMemo } from 'react';
import {
  CheckCircle,
  AlertTriangle,
  CheckSquare,
  Square,
  Send,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '../../ui/button';
import { cn } from '../../../lib/utils';
import type { PRReviewFinding } from '../hooks/useGitHubPRs';
import { useFindingSelection } from '../hooks/useFindingSelection';
import { FindingsSummary } from './FindingsSummary';
import { SeverityGroupHeader } from './SeverityGroupHeader';
import { FindingItem } from './FindingItem';
import type { SeverityGroup } from '../constants/severity-config';
import { SEVERITY_ORDER, SEVERITY_CONFIG } from '../constants/severity-config';

interface ReviewFindingsProps {
  findings: PRReviewFinding[];
  selectedIds: Set<string>;
  postedIds?: Set<string>;
  onSelectionChange: (selectedIds: Set<string>) => void;
}

export function ReviewFindings({
  findings,
  selectedIds,
  postedIds = new Set(),
  onSelectionChange,
}: ReviewFindingsProps) {
  const { t } = useTranslation('common');

  // Track which sections are expanded
  const [expandedSections, setExpandedSections] = useState<Set<SeverityGroup>>(
    new Set<SeverityGroup>(['critical', 'high']) // Critical and High expanded by default
  );

  // Filter out posted findings - only show unposted findings for selection
  const unpostedFindings = useMemo(() =>
    findings.filter(f => !postedIds.has(f.id)),
    [findings, postedIds]
  );

  // Check if all findings are posted
  const allFindingsPosted = findings.length > 0 && unpostedFindings.length === 0;

  // Group ALL findings by severity (posted + unposted)
  const groupedFindings = useMemo(() => {
    const groups: Record<SeverityGroup, PRReviewFinding[]> = {
      critical: [],
      high: [],
      medium: [],
      low: [],
    };

    for (const finding of findings) {
      const severity = finding.severity as SeverityGroup;
      if (groups[severity]) {
        groups[severity].push(finding);
      }
    }

    return groups;
  }, [findings]);

  // Count by severity (all findings) + unposted counts for selection
  const counts = useMemo(() => ({
    critical: groupedFindings.critical.length,
    high: groupedFindings.high.length,
    medium: groupedFindings.medium.length,
    low: groupedFindings.low.length,
    total: findings.length,
    unpostedImportant: unpostedFindings.filter(f => f.severity === 'critical' || f.severity === 'high').length,
    posted: postedIds.size,
  }), [groupedFindings, findings.length, unpostedFindings, postedIds.size]);

  // Selection hooks - use unposted findings only
  const {
    toggleFinding,
    selectAll,
    selectNone,
    selectImportant,
    toggleSeverityGroup,
  } = useFindingSelection({
    findings: unpostedFindings,
    selectedIds,
    onSelectionChange,
    groupedFindings,
  });

  // Toggle section expansion
  const toggleSection = (severity: SeverityGroup) => {
    setExpandedSections(prev => {
      const next = new Set(prev);
      if (next.has(severity)) {
        next.delete(severity);
      } else {
        next.add(severity);
      }
      return next;
    });
  };

  return (
    <div className="space-y-4">
      {/* Success banner when all findings are posted */}
      {allFindingsPosted && (
        <div className="flex items-center gap-2 py-2 px-3 bg-success/5 rounded-lg border border-success/20">
          <Send className="h-4 w-4 text-success flex-shrink-0" />
          <p className="text-sm font-medium text-success">{t('prReview.allFindingsPosted')}</p>
          <p className="text-xs text-muted-foreground">
            ({t('prReview.findingsPostedCount', { count: counts.posted })})
          </p>
        </div>
      )}

      {/* Summary Stats Bar & Quick Select Actions - only when there are unposted findings */}
      {!allFindingsPosted && (
        <>
          <FindingsSummary
            findings={unpostedFindings}
            selectedCount={selectedIds.size}
          />

          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="outline"
              size="sm"
              onClick={selectImportant}
              className="text-xs"
              disabled={counts.unpostedImportant === 0}
            >
              <AlertTriangle className="h-3 w-3 mr-1" />
              {t('prReview.selectCriticalHigh', { count: counts.unpostedImportant })}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={selectAll}
              className="text-xs"
            >
              <CheckSquare className="h-3 w-3 mr-1" />
              {t('prReview.selectAll')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={selectNone}
              className="text-xs"
              disabled={selectedIds.size === 0}
            >
              <Square className="h-3 w-3 mr-1" />
              {t('prReview.clear')}
            </Button>
          </div>
        </>
      )}

      {/* Grouped Findings (all findings - posted shown with posted styling) */}
      <div className="space-y-3">
        {SEVERITY_ORDER.map((severity) => {
          const group = groupedFindings[severity];
          if (group.length === 0) return null;

          const config = SEVERITY_CONFIG[severity];
          const isExpanded = expandedSections.has(severity);
          const selectedInGroup = group.filter(f => selectedIds.has(f.id)).length;

          return (
            <div
              key={severity}
              className={cn(
                "rounded-lg border",
                config.bgColor
              )}
            >
              {/* Group Header */}
              <SeverityGroupHeader
                severity={severity}
                count={group.length}
                selectedCount={selectedInGroup}
                expanded={isExpanded}
                onToggle={() => toggleSection(severity)}
                onSelectAll={allFindingsPosted ? undefined : (e) => {
                  e.stopPropagation();
                  toggleSeverityGroup(severity);
                }}
              />

              {/* Group Content */}
              {isExpanded && (
                <div className="p-3 pt-0 space-y-2">
                  {group.map((finding) => {
                    const isPosted = postedIds.has(finding.id);
                    return (
                      <FindingItem
                        key={finding.id}
                        finding={finding}
                        selected={!isPosted && selectedIds.has(finding.id)}
                        posted={isPosted}
                        onToggle={isPosted ? undefined : () => toggleFinding(finding.id)}
                      />
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Empty State - no findings at all */}
      {findings.length === 0 && (
        <div className="text-center py-8 text-muted-foreground">
          <CheckCircle className="h-8 w-8 mx-auto mb-2 text-success" />
          <p className="text-sm">{t('prReview.noIssuesFound')}</p>
        </div>
      )}
    </div>
  );
}
