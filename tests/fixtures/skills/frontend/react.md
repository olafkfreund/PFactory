# react

> Source: [facebook/react](https://github.com/facebook/react) | Stars: 200k | Installs: 15,000 | Weekly: 14,500 | First seen: Jan 1, 2024

---

# React - JavaScript UI Library

React is a JavaScript library for building user interfaces. It lets you compose complex UIs from small and isolated pieces of code called "components".

## When to Activate

Use when building interactive web applications with component-based architecture:

- Building single-page applications (SPAs)
- Creating reusable UI components
- Managing complex UI state with hooks
- Server-side rendering with Next.js
- React Native mobile development

## Core Concepts

### Components

React applications are built from components — independent, reusable pieces of UI.

```tsx
// Functional component with TypeScript
interface ButtonProps {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

export function Button({ label, onClick, disabled = false }: ButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="btn btn-primary"
    >
      {label}
    </button>
  );
}
```

### Hooks

React Hooks let you use state and other React features in functional components.

```tsx
import { useState, useEffect, useCallback } from 'react';

function DataFetcher({ url }: { url: string }) {
  const [data, setData] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);
      const json = await response.json();
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error.message}</div>;
  return <pre>{JSON.stringify(data, null, 2)}</pre>;
}
```

## Performance Optimization

### Memoization

Use `React.memo`, `useMemo`, and `useCallback` to avoid unnecessary re-renders.

```tsx
import { memo, useMemo, useCallback } from 'react';

const ExpensiveList = memo(({ items }: { items: string[] }) => {
  const sorted = useMemo(() => [...items].sort(), [items]);
  return <ul>{sorted.map(item => <li key={item}>{item}</li>)}</ul>;
});
```

### Code Splitting

Use `React.lazy` and `Suspense` for lazy loading components.

```tsx
import { lazy, Suspense } from 'react';

const HeavyComponent = lazy(() => import('./HeavyComponent'));

function App() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <HeavyComponent />
    </Suspense>
  );
}
```

## State Management

### Context API

For sharing state across components without prop drilling.

```tsx
import { createContext, useContext, useState, ReactNode } from 'react';

interface ThemeContextType {
  theme: 'light' | 'dark';
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextType | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const toggleTheme = useCallback(() => {
    setTheme(prev => prev === 'light' ? 'dark' : 'light');
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used within ThemeProvider');
  return context;
}
```

## Best Practices

- Use TypeScript for type safety
- Keep components small and focused (single responsibility)
- Lift state up to the nearest common ancestor
- Prefer controlled components for form inputs
- Use keys correctly in lists (stable, unique identifiers)
- Avoid mutating state directly — always use setState
- Clean up side effects in useEffect return functions
- Use ESLint with react-hooks/exhaustive-deps rule
