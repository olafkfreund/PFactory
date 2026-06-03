/**
 * Highlight.js configuration and language registration
 *
 * This module configures highlight.js with a curated set of languages
 * to keep bundle size manageable while supporting common code files.
 */

import hljs from 'highlight.js/lib/core';

// Tier 1 languages (always loaded)
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import json from 'highlight.js/lib/languages/json';
import markdown from 'highlight.js/lib/languages/markdown';
import yaml from 'highlight.js/lib/languages/yaml';
import bash from 'highlight.js/lib/languages/bash';

// Register Tier 1 languages
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('json', json);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('bash', bash);

// Aliases for common extensions
hljs.registerLanguage('jsx', javascript);
hljs.registerLanguage('tsx', typescript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('py', python);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('sh', bash);

/**
 * Language detection from file extension
 *
 * @param filename - The filename with extension (e.g., "script.py")
 * @returns Language identifier for highlight.js, or undefined if not supported
 */
export function detectLanguage(filename: string): string | undefined {
  const ext = filename.split('.').pop()?.toLowerCase();
  if (!ext) return undefined;

  const langMap: Record<string, string> = {
    // JavaScript family
    js: 'javascript',
    jsx: 'javascript',
    mjs: 'javascript',
    cjs: 'javascript',

    // TypeScript family
    ts: 'typescript',
    tsx: 'typescript',

    // Python
    py: 'python',
    pyw: 'python',

    // JSON
    json: 'json',
    jsonc: 'json',

    // Markdown
    md: 'markdown',
    markdown: 'markdown',

    // YAML
    yaml: 'yaml',
    yml: 'yaml',

    // Shell
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
  };

  return langMap[ext];
}

/**
 * Get human-readable language name
 *
 * @param lang - Language identifier from highlight.js
 * @returns Display name for the language
 */
export function getLanguageDisplayName(lang: string): string {
  const displayNames: Record<string, string> = {
    javascript: 'JavaScript',
    typescript: 'TypeScript',
    python: 'Python',
    json: 'JSON',
    markdown: 'Markdown',
    yaml: 'YAML',
    bash: 'Shell',
  };

  return displayNames[lang] || lang.toUpperCase();
}

export default hljs;
