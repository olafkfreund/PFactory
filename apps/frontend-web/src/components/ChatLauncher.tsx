import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles, X } from 'lucide-react';
import { Insights } from './Insights';
import type { SidebarView } from './Sidebar';

interface ChatLauncherProps {
  projectId: string;
  onNavigate?: (view: SidebarView) => void;
}

/**
 * Floating chat assistant — a bottom-right FAB that toggles a popup window
 * rendering the Insights chat in compact mode. Mirrors CFactory's Copilot
 * popup so the Factory portals share one chat UX. Replaces the old "Chat"
 * sidebar nav item.
 */
export function ChatLauncher({ projectId, onNavigate }: ChatLauncherProps) {
  const { t } = useTranslation(['navigation']);
  const [open, setOpen] = useState(false);

  // Esc closes the popup.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  // No project selected → no chat target; hide the launcher.
  if (!projectId) return null;

  return (
    <>
      {open && (
        <div className="chat-pop" role="dialog" aria-label={t('navigation:items.chat')}>
          <div className="chat-pop__head">
            <span className="chat-pop__title">
              <Sparkles className="h-4 w-4" />
              {t('navigation:items.chat')}
            </span>
            <button
              type="button"
              className="chat-pop__x"
              onClick={() => setOpen(false)}
              aria-label="Close chat"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="chat-pop__body">
            <Insights projectId={projectId} onNavigate={onNavigate} compact />
          </div>
        </div>
      )}

      <button
        type="button"
        className={`chat-fab${open ? ' chat-fab--open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={t('navigation:items.chat')}
      >
        {open ? <X className="h-5 w-5" /> : <Sparkles className="h-5 w-5" />}
      </button>
    </>
  );
}
