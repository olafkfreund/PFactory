import { create } from 'zustand';
import type { SkillCategory, SkillSummary, SkillDetail, SkillSuggestion } from '../shared/types';
import { get } from '../lib/api-client';

interface SkillsState {
  // Data
  categories: SkillCategory[];
  skillsByCategory: Record<string, SkillSummary[]>;
  searchResults: SkillSummary[];
  suggestions: SkillSuggestion[];
  selectedSkillDetail: SkillDetail | null;

  // Loading flags
  isLoadingCategories: boolean;
  isLoadingSkills: boolean;
  isSearching: boolean;
  isFetchingSuggestions: boolean;

  // Error
  error: string | null;

  // Actions
  setCategories: (categories: SkillCategory[]) => void;
  setSkillsForCategory: (category: string, skills: SkillSummary[]) => void;
  setSearchResults: (results: SkillSummary[]) => void;
  setSuggestions: (suggestions: SkillSuggestion[]) => void;
  setSelectedSkillDetail: (skill: SkillDetail | null) => void;
  setLoadingCategories: (loading: boolean) => void;
  setLoadingSkills: (loading: boolean) => void;
  setSearching: (searching: boolean) => void;
  setFetchingSuggestions: (fetching: boolean) => void;
  setError: (error: string | null) => void;
  clearSearch: () => void;
  clearSuggestions: () => void;
  clearError: () => void;

  // Selectors
  getSkillsForCategory: (category: string) => SkillSummary[] | undefined;
  hasCachedSkills: (category: string) => boolean;
}

export const useSkillsStore = create<SkillsState>((set, get) => ({
  // Initial state
  categories: [],
  skillsByCategory: {},
  searchResults: [],
  suggestions: [],
  selectedSkillDetail: null,
  isLoadingCategories: false,
  isLoadingSkills: false,
  isSearching: false,
  isFetchingSuggestions: false,
  error: null,

  // Actions
  setCategories: (categories) => set({ categories }),

  setSkillsForCategory: (category, skills) =>
    set((state) => ({
      skillsByCategory: {
        ...state.skillsByCategory,
        [category]: skills,
      },
    })),

  setSearchResults: (searchResults) => set({ searchResults }),

  setSuggestions: (suggestions) => set({ suggestions }),

  setSelectedSkillDetail: (selectedSkillDetail) => set({ selectedSkillDetail }),

  setLoadingCategories: (loading) => set({ isLoadingCategories: loading }),

  setLoadingSkills: (loading) => set({ isLoadingSkills: loading }),

  setSearching: (searching) => set({ isSearching: searching }),

  setFetchingSuggestions: (fetching) => set({ isFetchingSuggestions: fetching }),

  setError: (error) => set({ error }),

  clearSearch: () => set({ searchResults: [] }),

  clearSuggestions: () => set({ suggestions: [] }),

  clearError: () => set({ error: null }),

  // Selectors
  getSkillsForCategory: (category) => get().skillsByCategory[category],

  hasCachedSkills: (category) => category in get().skillsByCategory,
}));

/**
 * Load all skill categories. Caches results and skips re-fetching if already loaded.
 */
export async function fetchCategories(force = false): Promise<void> {
  const store = useSkillsStore.getState();

  if (!force && store.categories.length > 0) {
    return;
  }

  store.setLoadingCategories(true);
  store.clearError();

  try {
    const result = await get<SkillCategory[]>('/skills/categories');
    if (result.success && result.data) {
      store.setCategories(result.data);
    } else {
      store.setError(result.error ?? 'Failed to load skill categories');
    }
  } finally {
    store.setLoadingCategories(false);
  }
}

/**
 * Load skills for a given category. Caches results per category.
 */
export async function fetchSkills(category: string, force = false): Promise<void> {
  const store = useSkillsStore.getState();

  if (!force && store.hasCachedSkills(category)) {
    return;
  }

  store.setLoadingSkills(true);
  store.clearError();

  try {
    const params = new URLSearchParams({ category, limit: '100' });
    const result = await get<{ items: SkillSummary[]; total: number; page: number; limit: number; has_next: boolean }>(
      `/skills/list?${params.toString()}`
    );
    if (result.success && result.data) {
      store.setSkillsForCategory(category, result.data.items);
    } else {
      store.setError(result.error ?? `Failed to load skills for category: ${category}`);
    }
  } finally {
    store.setLoadingSkills(false);
  }
}

/**
 * Search skills by query string and optional category filter.
 * Debounce should be handled in the component, not here.
 */
export async function searchSkills(query: string, category?: string): Promise<void> {
  const store = useSkillsStore.getState();

  if (!query.trim()) {
    store.clearSearch();
    return;
  }

  store.setSearching(true);
  store.clearError();

  try {
    const params = new URLSearchParams({ q: query.trim() });
    if (category) {
      params.set('category', category);
    }

    const result = await get<{ items: SkillSummary[]; total: number; page: number; limit: number; has_next: boolean }>(
      `/skills/search?${params.toString()}`
    );
    if (result.success && result.data) {
      store.setSearchResults(result.data.items);
    } else {
      store.setError(result.error ?? 'Failed to search skills');
    }
  } finally {
    store.setSearching(false);
  }
}

/**
 * Load the full content of a specific skill.
 */
export async function fetchSkillDetail(category: string, name: string): Promise<void> {
  const store = useSkillsStore.getState();
  store.setLoadingSkills(true);
  store.clearError();

  try {
    const result = await get<SkillDetail>(
      `/skills/${encodeURIComponent(category)}/${encodeURIComponent(name)}`
    );
    if (result.success && result.data) {
      store.setSelectedSkillDetail(result.data);
    } else {
      store.setError(result.error ?? `Failed to load skill: ${category}/${name}`);
    }
  } finally {
    store.setLoadingSkills(false);
  }
}

/**
 * Fetch AI-powered skill suggestions for a given task description.
 */
export async function fetchSuggestions(taskDescription: string): Promise<void> {
  const store = useSkillsStore.getState();

  if (!taskDescription.trim()) {
    store.clearSuggestions();
    return;
  }

  store.setFetchingSuggestions(true);
  store.clearError();

  try {
    const params = new URLSearchParams({
      task_description: taskDescription.trim(),
      limit: '10',
    });
    const result = await get<SkillSuggestion[]>(`/skills/suggest?${params.toString()}`);
    if (result.success && result.data) {
      const mapped = result.data.map((s: any) => ({
        ...s,
        relevanceScore: s.relevance_score ?? s.relevanceScore ?? 0,
      }));
      store.setSuggestions(mapped);
    } else {
      store.setError(result.error ?? 'Failed to fetch skill suggestions');
    }
  } finally {
    store.setFetchingSuggestions(false);
  }
}
