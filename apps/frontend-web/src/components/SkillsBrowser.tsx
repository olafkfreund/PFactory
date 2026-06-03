import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Search, X } from 'lucide-react';
import { cn } from '../lib/utils';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { ScrollArea, ScrollBar } from './ui/scroll-area';
import { SkillCard } from './SkillCard';
import {
  useSkillsStore,
  fetchCategories,
  fetchSkills,
  searchSkills,
  fetchSuggestions,
} from '../stores/skills-store';
import type { SelectedSkill } from '../shared/types/task';
import type { SkillSummary } from '../shared/types/skills';

interface SkillsBrowserProps {
  selectedSkills: SelectedSkill[];
  onSkillsChange: (skills: SelectedSkill[]) => void;
  taskDescription?: string;
  maxSkills?: number;
  className?: string;
}

type TabValue = 'browse' | 'search' | 'suggestions';

export function SkillsBrowser({
  selectedSkills,
  onSkillsChange,
  taskDescription,
  maxSkills = 5,
  className,
}: SkillsBrowserProps) {
  const { t } = useTranslation('tasks');

  // Store state
  const categories = useSkillsStore((s) => s.categories);
  const skillsByCategory = useSkillsStore((s) => s.skillsByCategory);
  const searchResults = useSkillsStore((s) => s.searchResults);
  const suggestions = useSkillsStore((s) => s.suggestions);
  const isLoadingCategories = useSkillsStore((s) => s.isLoadingCategories);
  const isLoadingSkills = useSkillsStore((s) => s.isLoadingSkills);
  const isSearching = useSkillsStore((s) => s.isSearching);
  const isFetchingSuggestions = useSkillsStore((s) => s.isFetchingSuggestions);

  // Local state
  const [activeTab, setActiveTab] = useState<TabValue>('browse');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [suggestionsLoaded, setSuggestionsLoaded] = useState(false);

  // Debounce timer ref
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Derived
  const selectedIds = new Set(selectedSkills.map((s) => s.id));
  const atMax = selectedSkills.length >= maxSkills;
  const currentSkills = selectedCategory ? (skillsByCategory[selectedCategory] ?? []) : [];

  // ── Effects ─────────────────────────────────────────────────────────────

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
    if (activeTab !== 'search') return;

    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }

    debounceRef.current = setTimeout(() => {
      searchSkills(searchQuery);
    }, 300);

    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [searchQuery, activeTab]);

  // Auto-load suggestions when Suggestions tab is first opened
  useEffect(() => {
    if (
      activeTab === 'suggestions' &&
      !suggestionsLoaded &&
      taskDescription &&
      taskDescription.length >= 20
    ) {
      setSuggestionsLoaded(true);
      fetchSuggestions(taskDescription);
    }
  }, [activeTab, suggestionsLoaded, taskDescription]);

  // ── Handlers ────────────────────────────────────────────────────────────

  const handleToggle = (skill: SkillSummary) => {
    if (selectedIds.has(skill.id)) {
      // Deselect
      onSkillsChange(selectedSkills.filter((s) => s.id !== skill.id));
    } else {
      // Select (only if below max)
      if (!atMax) {
        const newSkill: SelectedSkill = {
          id: skill.id,
          name: skill.name,
          category: skill.category,
          source: skill.source,
        };
        onSkillsChange([...selectedSkills, newSkill]);
      }
    }
  };

  const handleRemoveSkill = (skillId: string) => {
    onSkillsChange(selectedSkills.filter((s) => s.id !== skillId));
  };

  const handleClearSearch = () => {
    setSearchQuery('');
  };

  const handleTabChange = (value: string) => {
    setActiveTab(value as TabValue);
  };

  // ── Render helpers ───────────────────────────────────────────────────────

  const renderBrowseTab = () => (
    <div className="flex h-72 gap-2">
      {/* Category sidebar */}
      <div className="flex w-40 shrink-0 flex-col rounded-lg border border-border bg-secondary/30">
        {isLoadingCategories ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <ScrollArea className="flex-1">
            <div className="p-1">
              {categories.map((cat) => (
                <button
                  key={cat.name}
                  type="button"
                  onClick={() => setSelectedCategory(cat.name)}
                  className={cn(
                    'w-full rounded-md px-2.5 py-1.5 text-left text-xs transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    selectedCategory === cat.name
                      ? 'bg-primary/10 font-medium text-primary'
                      : 'text-foreground/70 hover:bg-accent hover:text-foreground',
                  )}
                >
                  <span className="block truncate">{cat.name}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {t('skills.categoryCount', { count: cat.count, defaultValue: `${cat.count}` })}
                  </span>
                </button>
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      {/* Skills grid */}
      <div className="relative min-w-0 flex-1 rounded-lg border border-border">
        {!selectedCategory ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-xs text-muted-foreground">
              {t('skills.selectCategory', { defaultValue: 'Select a category' })}
            </p>
          </div>
        ) : isLoadingSkills ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <ScrollArea className="h-full">
            <div className="grid grid-cols-1 gap-2 p-2 sm:grid-cols-2">
              {currentSkills.map((skill) => (
                <SkillCard
                  key={skill.id}
                  skill={skill}
                  isSelected={selectedIds.has(skill.id)}
                  onToggle={handleToggle}
                  disabled={atMax && !selectedIds.has(skill.id)}
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  );

  const renderSearchTab = () => (
    <div className="flex h-72 flex-col gap-2">
      {/* Search input */}
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

      {/* Results */}
      <div className="relative min-h-0 flex-1 rounded-lg border border-border">
        {isSearching ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : searchQuery && searchResults.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-xs text-muted-foreground">
              {t('skills.noResults', { defaultValue: 'No results found' })}
            </p>
          </div>
        ) : (
          <ScrollArea className="h-full">
            <div className="grid grid-cols-1 gap-2 p-2 sm:grid-cols-2">
              {searchResults.map((skill) => (
                <SkillCard
                  key={skill.id}
                  skill={skill}
                  isSelected={selectedIds.has(skill.id)}
                  onToggle={handleToggle}
                  disabled={atMax && !selectedIds.has(skill.id)}
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    </div>
  );

  const renderSuggestionsTab = () => {
    const descriptionTooShort = !taskDescription || taskDescription.length < 20;

    return (
      <div className="h-72 rounded-lg border border-border">
        {descriptionTooShort ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-xs text-muted-foreground">
              {t('skills.noSuggestions', {
                defaultValue: 'Add a longer task description to get suggestions',
              })}
            </p>
          </div>
        ) : isFetchingSuggestions ? (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            <p className="text-xs text-muted-foreground">
              {t('skills.analyzing', { defaultValue: 'Analyzing…' })}
            </p>
          </div>
        ) : suggestions.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-xs text-muted-foreground">
              {t('skills.noSuggestions', {
                defaultValue: 'No suggestions found',
              })}
            </p>
          </div>
        ) : (
          <ScrollArea className="h-full">
            <div className="grid grid-cols-1 gap-2 p-2 sm:grid-cols-2">
              {suggestions.map((suggestion) => (
                <SkillCard
                  key={suggestion.skill.id}
                  skill={suggestion.skill}
                  isSelected={selectedIds.has(suggestion.skill.id)}
                  onToggle={handleToggle}
                  relevanceScore={suggestion.relevanceScore}
                  disabled={atMax && !selectedIds.has(suggestion.skill.id)}
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    );
  };

  // ── Main render ──────────────────────────────────────────────────────────

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList className="w-full">
          <TabsTrigger value="browse" className="flex-1">
            {t('skills.browseTab', { defaultValue: 'Browse' })}
          </TabsTrigger>
          <TabsTrigger value="search" className="flex-1">
            {t('skills.searchTab', { defaultValue: 'Search' })}
          </TabsTrigger>
          {taskDescription !== undefined && (
            <TabsTrigger value="suggestions" className="flex-1">
              {t('skills.suggestionsTab', { defaultValue: 'Suggestions' })}
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="browse">{renderBrowseTab()}</TabsContent>
        <TabsContent value="search">{renderSearchTab()}</TabsContent>
        {taskDescription !== undefined && (
          <TabsContent value="suggestions">{renderSuggestionsTab()}</TabsContent>
        )}
      </Tabs>

      {/* Selected skills row */}
      <div className="rounded-lg border border-border bg-secondary/20 p-2">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs font-medium text-foreground">
            {t('skills.selectedCount', {
              count: selectedSkills.length,
              max: maxSkills,
              defaultValue: `${selectedSkills.length}/${maxSkills} skills selected`,
            })}
          </span>
          {atMax && (
            <span className="text-xs font-medium text-warning">
              {t('skills.maximumReached', { defaultValue: 'Maximum reached' })}
            </span>
          )}
        </div>

        {selectedSkills.length === 0 ? (
          <p className="py-1 text-xs text-muted-foreground">
            {t('skills.noSkillsSelected', { defaultValue: 'No skills selected' })}
          </p>
        ) : (
          <ScrollArea className="w-full" type="scroll">
            <div className="flex gap-1.5 pb-1">
              {selectedSkills.map((skill) => (
                <div
                  key={skill.id}
                  className={cn(
                    'flex shrink-0 items-center gap-1 rounded-full border border-primary/30',
                    'bg-primary/10 px-2.5 py-0.5 text-xs text-primary',
                  )}
                >
                  <span className="max-w-[140px] truncate">{skill.name}</span>
                  <button
                    type="button"
                    onClick={() => handleRemoveSkill(skill.id)}
                    className="ml-0.5 rounded-full text-primary/60 hover:text-primary focus:outline-none focus:ring-1 focus:ring-ring"
                    aria-label={t('skills.removeSkill', {
                      name: skill.name,
                      defaultValue: `Remove ${skill.name}`,
                    })}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>
        )}
      </div>
    </div>
  );
}
