import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Search, X, ExternalLink, Lightbulb } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { cn } from '../lib/utils';
import { ScrollArea } from './ui/scroll-area';
import {
  useSkillsStore,
  fetchCategories,
  fetchSkills,
  searchSkills,
  fetchSkillDetail,
} from '../stores/skills-store';
import type { SkillSummary } from '../shared/types/skills';

// ── Category color helper (same logic as SkillCard.tsx) ──────────────────────

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

function getCategoryColor(category: string): string {
  let hash = 5381;
  for (let i = 0; i < category.length; i++) {
    hash = (hash * 33) ^ category.charCodeAt(i);
  }
  return CATEGORY_COLORS[Math.abs(hash) % CATEGORY_COLORS.length];
}

// ── Component ────────────────────────────────────────────────────────────────

export function SkillsPage() {
  const { t } = useTranslation('tasks');

  // Store state
  const categories = useSkillsStore((s) => s.categories);
  const skillsByCategory = useSkillsStore((s) => s.skillsByCategory);
  const searchResults = useSkillsStore((s) => s.searchResults);
  const selectedSkillDetail = useSkillsStore((s) => s.selectedSkillDetail);
  const isLoadingCategories = useSkillsStore((s) => s.isLoadingCategories);
  const isLoadingSkills = useSkillsStore((s) => s.isLoadingSkills);
  const isSearching = useSkillsStore((s) => s.isSearching);
  const error = useSkillsStore((s) => s.error);

  // Local state
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSkillId, setActiveSkillId] = useState<string | null>(null);

  // Debounce timer ref
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Derived
  const isSearchMode = searchQuery.trim().length > 0;
  const displayedSkills: SkillSummary[] = isSearchMode
    ? searchResults
    : selectedCategory
      ? (skillsByCategory[selectedCategory] ?? [])
      : [];

  // ── Effects ──────────────────────────────────────────────────────────────

  // Fetch categories on mount
  useEffect(() => {
    fetchCategories();
  }, []);

  // Fetch skills when category is selected
  useEffect(() => {
    if (selectedCategory) {
      fetchSkills(selectedCategory);
    }
  }, [selectedCategory]);

  // Debounced search
  useEffect(() => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }

    if (!searchQuery.trim()) {
      useSkillsStore.getState().clearSearch();
      return;
    }

    debounceRef.current = setTimeout(() => {
      searchSkills(searchQuery);
    }, 300);

    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [searchQuery]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleSkillClick = (skill: SkillSummary) => {
    setActiveSkillId(skill.id);
    fetchSkillDetail(skill.category, skill.name);
  };

  const handleClearSearch = () => {
    setSearchQuery('');
    useSkillsStore.getState().clearSearch();
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-border px-6 py-4">
        <Lightbulb className="h-5 w-5 text-primary" />
        <h1 className="text-lg font-semibold text-foreground">
          {t('skills.pageTitle', { defaultValue: 'Skills Browser' })}
        </h1>
      </div>

      {/* Main content: master-detail */}
      <div className="flex min-h-0 flex-1">
        {/* ── Left panel: browse & search ──────────────────────────────── */}
        <div className="flex w-[45%] min-w-0 flex-col border-r border-border">
          {/* Search bar */}
          <div className="border-b border-border px-4 py-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t('skills.searchPlaceholder', { defaultValue: 'Search skills…' })}
                className={cn(
                  'w-full rounded-md border border-border bg-card py-2 pl-8 pr-8',
                  'text-sm text-foreground placeholder:text-muted-foreground',
                  'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1',
                )}
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={handleClearSearch}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground focus:outline-none"
                  aria-label={t('skills.clearSearch', { defaultValue: 'Clear search' })}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* Category sidebar + skill list */}
          <div className="flex min-h-0 flex-1">
            {/* Category sidebar */}
            {!isSearchMode && (
              <div className="flex w-44 shrink-0 flex-col border-r border-border bg-secondary/20">
                {isLoadingCategories ? (
                  <div className="flex flex-1 items-center justify-center">
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <ScrollArea className="flex-1">
                    <div className="p-2 space-y-0.5">
                      {categories.map((cat) => (
                        <button
                          key={cat.name}
                          type="button"
                          onClick={() => setSelectedCategory(cat.name)}
                          className={cn(
                            'w-full rounded-md px-2.5 py-2 text-left text-xs transition-colors',
                            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                            selectedCategory === cat.name
                              ? 'bg-primary/10 font-medium text-primary'
                              : 'text-foreground/70 hover:bg-accent hover:text-foreground',
                          )}
                        >
                          <span className="block truncate">{cat.name}</span>
                          <span className="text-[10px] text-muted-foreground">
                            {cat.count} {cat.count === 1 ? 'skill' : 'skills'}
                          </span>
                        </button>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </div>
            )}

            {/* Skill list */}
            <div className="min-w-0 flex-1">
              {isSearchMode && isSearching ? (
                <div className="flex h-full items-center justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              ) : isSearchMode && searchResults.length === 0 && searchQuery.trim() ? (
                <div className="flex h-full items-center justify-center">
                  <p className="text-sm text-muted-foreground">
                    {t('skills.noResults', { defaultValue: 'No results found' })}
                  </p>
                </div>
              ) : !isSearchMode && !selectedCategory ? (
                <div className="flex h-full items-center justify-center">
                  <p className="text-sm text-muted-foreground">
                    {t('skills.selectCategory', { defaultValue: 'Select a category' })}
                  </p>
                </div>
              ) : !isSearchMode && isLoadingSkills && displayedSkills.length === 0 ? (
                <div className="flex h-full items-center justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              ) : !isSearchMode && selectedCategory && !isLoadingSkills && displayedSkills.length === 0 ? (
                <div className="flex h-full items-center justify-center">
                  <p className="text-sm text-muted-foreground">
                    {error || t('skills.noSkillsInCategory', { defaultValue: 'No skills found in this category' })}
                  </p>
                </div>
              ) : (
                <ScrollArea className="h-full">
                  <div className="space-y-px p-2">
                    {displayedSkills.map((skill) => (
                      <button
                        key={skill.id}
                        type="button"
                        onClick={() => handleSkillClick(skill)}
                        className={cn(
                          'flex w-full flex-col gap-1 rounded-lg px-3 py-2.5 text-left transition-colors',
                          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                          activeSkillId === skill.id
                            ? 'bg-primary/10 border border-primary/30'
                            : 'hover:bg-accent/50 border border-transparent',
                        )}
                      >
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium text-foreground">
                            {skill.name}
                          </span>
                          <span
                            className={cn(
                              'inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[10px] font-medium',
                              getCategoryColor(skill.category),
                            )}
                          >
                            {skill.category}
                          </span>
                        </div>
                        <p className="line-clamp-2 text-xs text-muted-foreground">
                          {skill.description}
                        </p>
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              )}
            </div>
          </div>
        </div>

        {/* ── Right panel: skill detail ────────────────────────────────── */}
        <div className="flex min-w-0 flex-1 flex-col">
          {!activeSkillId ? (
            <div className="flex h-full items-center justify-center">
              <div className="text-center">
                <Lightbulb className="mx-auto mb-2 h-8 w-8 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">
                  {t('skills.selectSkillToView', { defaultValue: 'Select a skill to view its content' })}
                </p>
              </div>
            </div>
          ) : isLoadingSkills && !selectedSkillDetail ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : selectedSkillDetail ? (
            <>
              {/* Detail header */}
              <div className="border-b border-border px-6 py-4">
                <div className="flex items-center gap-3">
                  <h2 className="text-lg font-semibold text-foreground">
                    {selectedSkillDetail.name}
                  </h2>
                  <span
                    className={cn(
                      'inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium',
                      getCategoryColor(selectedSkillDetail.category),
                    )}
                  >
                    {selectedSkillDetail.category}
                  </span>
                </div>
                {selectedSkillDetail.description && (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {selectedSkillDetail.description}
                  </p>
                )}
                {selectedSkillDetail.source && (
                  <div className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
                    <ExternalLink className="h-3 w-3" />
                    <span className="truncate">{selectedSkillDetail.source}</span>
                  </div>
                )}
              </div>

              {/* Detail body: full markdown content */}
              <ScrollArea className="flex-1">
                <div className="px-6 py-4">
                  <div className="prose prose-sm dark:prose-invert max-w-none">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      rehypePlugins={[rehypeHighlight]}
                    >
                      {selectedSkillDetail.content}
                    </ReactMarkdown>
                  </div>
                </div>
              </ScrollArea>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
