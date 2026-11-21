import type { ReactNode } from 'react';
import { useParams, Link } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { AgentConsole } from '../components/task-detail/AgentConsole';
import { useProjectStore } from '../stores/project-store';
import { useEnsureProjects } from '../hooks/useEnsureProjects';
import { findProjectByRef, projectSlug } from '../lib/project-route';

/**
 * Standalone deep-link page that drops you straight into the rmux Live
 * Agent Console for a given task, no portal chrome / kanban detour.
 *
 * URL pattern: ``/p/:slug/console/:specId`` (legacy ``/console/:projectId/:specId`` redirects here)
 *
 *   https://pfactory.example.com/p/my-project/console/001-gh1-add-healthz
 *
 * Shareable to teammates over the LAN/VPN, openable from a phone
 * browser as a fullscreen terminal.  The "Copy console link" button in
 * the task-detail panel writes this URL to the clipboard.
 *
 * Auth is whatever the user already has — the page lives inside the
 * AuthenticatedApp gate so an unauthenticated visitor gets bounced to
 * /login first.
 */
export function ConsolePage(): ReactNode {
  const { slug, specId } = useParams<{ slug: string; specId?: string }>();
  useEnsureProjects();
  const projects = useProjectStore((s) => s.projects);
  const project = slug ? findProjectByRef(projects, slug) : undefined;
  const projectId = project?.id ?? slug;
  const slugOrRef = project ? projectSlug(projects, project) : slug;

  if (!projectId || !specId) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        <p>
          Invalid console URL. Expected{' '}
          <code className="text-xs bg-muted px-1 rounded">
            /p/&lt;project&gt;/console/&lt;specId&gt;
          </code>
          .
        </p>
      </div>
    );
  }

  const taskId = `${projectId}:${specId}`;

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex items-center gap-3 border-b border-border px-4 py-2 text-sm">
        <Link
          to={project ? `/p/${slugOrRef}` : '/'}
          className="flex items-center gap-1 text-muted-foreground hover:text-foreground"
          title="Back to portal"
        >
          <ArrowLeft className="h-4 w-4" />
          <span>Back to portal</span>
        </Link>
        <span className="text-border">·</span>
        <span className="text-muted-foreground">
          {project?.name ?? projectId.slice(0, 8)}
        </span>
        <span className="text-border">·</span>
        <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">
          {specId}
        </code>
      </header>
      <main className="flex-1 overflow-hidden">
        <AgentConsole taskId={taskId} />
      </main>
    </div>
  );
}
