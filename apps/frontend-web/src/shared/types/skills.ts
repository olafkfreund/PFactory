/**
 * Skills-related types
 */

export interface SkillCategory {
  name: string;
  count: number;
  description?: string;
}

export interface SkillSummary {
  id: string;         // '{category}/{skill_name}'
  name: string;
  category: string;
  description: string;
  source?: string;
}

export interface SkillDetail extends SkillSummary {
  content: string;    // full markdown content
}

export interface SkillSuggestion {
  skill: SkillSummary;
  relevanceScore: number;
  reason: string;
}
