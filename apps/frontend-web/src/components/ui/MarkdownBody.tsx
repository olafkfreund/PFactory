/**
 * MarkdownBody — renders GitHub/GitLab/ADO PR and issue body text.
 *
 * Accepts "GitHub Flavored Markdown with embedded HTML" — the exact shape
 * Dependabot, GitHub Actions, GitLab, and Azure DevOps emit in PR/MR/issue
 * descriptions. Uses the same react-markdown pipeline already in use across
 * the codebase (Insights, EditorPage, Skills, TaskMetadata, etc.), augmented
 * with:
 *
 *   rehype-raw       parses embedded HTML (<details>, <summary>, <ul>,
 *                    <a href>, …) into the AST so it actually renders
 *                    instead of escaping as literal text.
 *   rehype-sanitize  drops <script>, on* event handlers, javascript: URLs,
 *                    <style>, etc. — XSS-safe allowlist.
 *
 * Plugin order is load-bearing: rehype-raw must run BEFORE rehype-sanitize,
 * otherwise sanitize sees raw text tokens instead of HTML AST nodes and the
 * dangerous markup is never inspected.
 *
 * Usage:
 *
 *   <MarkdownBody source={pr.body} />
 *   <MarkdownBody source={issue.body} className="max-h-96 overflow-y-auto" />
 */

import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeHighlight from "rehype-highlight";
import { cn } from "../../lib/utils";

// Extend the default sanitize schema to preserve <details>/<summary> (used by
// Dependabot for collapsible "Commits"/"Changelog" sections) and explicit
// link attributes the default schema already allows but worth pinning so
// future schema updates can't strip them.
const sanitizeSchema = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames ?? []),
    "details",
    "summary",
  ],
  attributes: {
    ...defaultSchema.attributes,
    a: ["href", "title", "target", "rel"],
    img: ["src", "alt", "title", "width", "height"],
    code: ["className"], // language-* class for rehype-highlight
    pre: ["className"],
  },
};

// Custom link renderer: external links open in a new tab with noopener;
// non-http(s) URLs (e.g. javascript:) render as a muted span so XSS via
// markdown links is impossible. Mirrors the SafeLink pattern in Insights.tsx.
type AnchorProps = ComponentPropsWithoutRef<"a">;

function MarkdownLink({ href, children, ...props }: AnchorProps) {
  const safe =
    typeof href === "string" &&
    (href.startsWith("https://") ||
      href.startsWith("http://") ||
      href.startsWith("/") ||
      href.startsWith("#") ||
      href.startsWith("mailto:"));

  if (!safe) {
    return <span className="text-muted-foreground">{children}</span>;
  }

  const isExternal = href.startsWith("http://") || href.startsWith("https://");
  return (
    <a
      href={href}
      {...props}
      {...(isExternal && { target: "_blank", rel: "noopener noreferrer" })}
      className="text-primary underline underline-offset-2 hover:text-primary/80 transition-colors"
    >
      {children}
    </a>
  );
}

// Component map: applies Tailwind classes inline so each rendered element
// is styled even without the `prose` plugin (Tailwind v4's prose support is
// in flux and varies by element). Each class is compile-time-visible to the
// v4 scanner.
const markdownComponents: Components = {
  a: MarkdownLink,
  h1: ({ children }) => (
    <h1 className="text-xl font-bold mt-4 mb-2 text-foreground">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-semibold mt-3 mb-1.5 text-foreground">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-semibold mt-2 mb-1 text-foreground">{children}</h3>
  ),
  p: ({ children }) => (
    <p className="text-sm text-foreground/90 leading-relaxed my-2">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc list-outside pl-5 my-2 space-y-0.5 text-sm text-foreground/90">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-outside pl-5 my-2 space-y-0.5 text-sm text-foreground/90">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="text-foreground/90">{children}</li>,
  code: ({ children, className }) => {
    const isBlock = typeof className === "string" && className.startsWith("language-");
    if (isBlock) {
      // Block code — rehype-highlight handles the syntax styling via highlight.js classes.
      return <code className={cn("text-xs font-mono", className)}>{children}</code>;
    }
    return (
      <code className="px-1 py-0.5 rounded bg-muted text-xs font-mono text-foreground/90">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-3 p-3 rounded-md bg-muted overflow-x-auto text-xs font-mono border border-border/50">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 pl-3 border-l-2 border-border text-sm text-muted-foreground italic">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full text-sm border-collapse border border-border/50">
        {children}
      </table>
    </div>
  ),
  th: ({ children }) => (
    <th className="px-3 py-1.5 text-left font-semibold bg-muted/50 border border-border/50 text-foreground text-xs uppercase tracking-wide">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-3 py-1.5 border border-border/50 text-foreground/90">{children}</td>
  ),
  // Task list checkboxes (GFM). Readonly — clicks don't write back to the PR.
  input: ({ type, checked }) =>
    type === "checkbox" ? (
      <input
        type="checkbox"
        checked={!!checked}
        readOnly
        className="mr-1.5 accent-primary align-middle"
      />
    ) : null,
  hr: () => <hr className="my-4 border-border/50" />,
  // <details> + <summary> pass through via rehype-raw and render as native
  // disclosure widgets. No custom component needed — the browser handles
  // the toggle behaviour.
};

interface MarkdownBodyProps {
  /** The raw body text — may contain GFM plus embedded HTML (Dependabot et al.) */
  source: string;
  /** Optional layout-level className, e.g. for max-height + overflow */
  className?: string;
}

export function MarkdownBody({ source, className }: MarkdownBodyProps) {
  return (
    <div className={cn("min-w-0 break-words", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          rehypeRaw, // must run BEFORE sanitize — parses embedded HTML into AST
          [rehypeSanitize, sanitizeSchema],
          rehypeHighlight,
        ]}
        components={markdownComponents}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
