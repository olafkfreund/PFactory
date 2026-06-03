import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';
import { Badge } from './ui/badge';
import { Checkbox } from './ui/checkbox';
import type { SkillSummary } from '../shared/types/skills';

interface SkillCardProps {
  skill: SkillSummary;
  isSelected: boolean;
  onToggle: (skill: SkillSummary) => void;
  relevanceScore?: number;  // 0-1 float, shown as '87% match' badge
  disabled?: boolean;       // When max skills reached and this isn't selected
  showPreview?: boolean;    // Expand description inline
  onPreviewToggle?: (skillId: string) => void;
}

/**
 * Derives a stable color class from a category name using a simple hash.
 * Returns a Tailwind CSS class string for the badge background and text.
 */
function getCategoryColor(category: string): string {
  // Simple djb2-style hash reduced to index
  let hash = 5381;
  for (let i = 0; i < category.length; i++) {
    hash = (hash * 33) ^ category.charCodeAt(i);
  }
  const index = Math.abs(hash) % CATEGORY_COLORS.length;
  return CATEGORY_COLORS[index];
}

const CATEGORY_COLORS = [
  'bg-blue-500/10 text-blue-400 border-blue-500/20',
  'bg-purple-500/10 text-purple-400 border-purple-500/20',
  'bg-green-500/10 text-green-400 border-green-500/20',
  'bg-orange-500/10 text-orange-400 border-orange-500/20',
  'bg-pink-500/10 text-pink-400 border-pink-500/20',
  'bg-teal-500/10 text-teal-400 border-teal-500/20',
  'bg-yellow-500/10 text-yellow-600 border-yellow-500/20',
  'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
  'bg-red-500/10 text-red-400 border-red-500/20',
];

export function SkillCard({
  skill,
  isSelected,
  onToggle,
  relevanceScore,
  disabled = false,
  showPreview = false,
  onPreviewToggle,
}: SkillCardProps) {
  const { t } = useTranslation('tasks');
  const categoryColorClass = getCategoryColor(skill.category);

  const handleClick = () => {
    if (!disabled) {
      onToggle(skill);
    }
  };

  const handlePreviewToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onPreviewToggle) {
      onPreviewToggle(skill.id);
    }
  };

  const relevancePercent =
    relevanceScore !== undefined ? Math.round(relevanceScore * 100) : undefined;

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-pressed={isSelected}
      aria-disabled={disabled}
      onClick={handleClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          handleClick();
        }
      }}
      className={cn(
        'flex items-start gap-3 rounded-lg border border-border p-3 transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
        isSelected
          ? 'border-primary/50 bg-primary/5 ring-1 ring-primary/30'
          : 'bg-card hover:bg-accent/50',
        disabled && 'cursor-not-allowed opacity-50',
        !disabled && 'cursor-pointer',
      )}
    >
      {/* Checkbox */}
      <div className="mt-0.5 shrink-0">
        <Checkbox
          checked={isSelected}
          disabled={disabled}
          onCheckedChange={() => {
            if (!disabled) {
              onToggle(skill);
            }
          }}
          onClick={(e) => e.stopPropagation()}
          aria-label={t('skills.selectSkill', { name: skill.name, defaultValue: skill.name })}
        />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        {/* Top row: name + badges */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-sm font-medium text-foreground">{skill.name}</span>

          {/* Category badge */}
          <span
            className={cn(
              'inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium',
              categoryColorClass,
            )}
          >
            {skill.category}
          </span>

          {/* Relevance score badge */}
          {relevancePercent !== undefined && (
            <Badge
              className="border-transparent bg-success/10 px-1.5 py-0.5 text-xs text-success"
            >
              {t('skills.relevanceMatch', {
                score: relevancePercent,
                defaultValue: `${relevancePercent}%`,
              })}
            </Badge>
          )}
        </div>

        {/* Description */}
        <p
          className={cn(
            'mt-0.5 text-xs text-muted-foreground',
            showPreview ? '' : 'line-clamp-2',
          )}
        >
          {skill.description}
        </p>

        {/* Preview toggle link */}
        {onPreviewToggle && (
          <button
            type="button"
            onClick={handlePreviewToggle}
            className="mt-1 text-xs text-primary/70 hover:text-primary focus:outline-none"
          >
            {showPreview
              ? t('skills.hidePreview', { defaultValue: 'Hide preview' })
              : t('skills.showPreview', { defaultValue: 'Show preview' })}
          </button>
        )}
      </div>
    </div>
  );
}
