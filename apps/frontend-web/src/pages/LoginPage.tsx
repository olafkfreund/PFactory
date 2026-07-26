/**
 * Login page for the web UI — the front door.
 *
 * Atmospheric, branded sign-in over a Gruvbox "factory floor at night" canvas:
 * layered colour glows + a faint blueprint grid, the flask mark aglow, a glass
 * card with a confident CTA, and a staggered reveal on load. All auth handlers
 * (token login + OIDC SSO) are unchanged.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { FlaskConical, KeyRound, ArrowRight, Loader2, ShieldCheck } from 'lucide-react';
import { useAuthStore } from '../stores/auth-store';
import { getAuthToken } from '../lib/auth';

export function LoginPage() {
  // Pre-fill with existing token from localStorage (if any)
  const [token, setToken] = useState(() => getAuthToken() || '');
  const { login, isLoading, error } = useAuthStore();
  const navigate = useNavigate();

  // Silent SSO handoff (#149): switching between portals that share the one
  // Keycloak realm shouldn't force a manual "Sign in with SSO" click. On first
  // landing here with no session, probe silently (prompt=none) — if the realm
  // session is live, Keycloak returns a code and we log in without a prompt; if
  // not, the callback bounces back to /login with the guard already set, so the
  // manual form shows and this never loops. Gated on OIDC actually being enabled
  // (else a 404), and single-shot per tab via sessionStorage.
  useEffect(() => {
    if (sessionStorage.getItem('ssoAutoTried')) return;
    let cancelled = false;
    fetch('/api/auth/oidc/enabled')
      .then((res) => (res.ok ? res.json() : { enabled: false }))
      .then((data: { enabled?: boolean }) => {
        if (!cancelled && data.enabled) {
          sessionStorage.setItem('ssoAutoTried', '1');
          window.location.href = '/api/auth/oidc/login?prompt=none';
        }
      })
      .catch(() => {
        /* stay on the manual login form */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const success = await login(token);
    if (success) {
      navigate('/');
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-background">
      {/* ── atmosphere ─────────────────────────────────────────────── */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute -left-40 -top-40 h-[36rem] w-[36rem] rounded-full bg-primary/10 blur-[130px]" />
        <div className="absolute -bottom-48 -right-32 h-[34rem] w-[34rem] rounded-full bg-accent/10 blur-[130px]" />
        {/* faint blueprint grid */}
        <div
          className="absolute inset-0 opacity-[0.035]"
          style={{
            backgroundImage:
              'linear-gradient(hsl(var(--foreground)) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--foreground)) 1px, transparent 1px)',
            backgroundSize: '46px 46px',
          }}
        />
        {/* top sheen */}
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent" />
      </div>

      <div className="relative z-10 flex min-h-screen items-center justify-center p-6">
        <div className="w-full max-w-md">
          {/* ── brand lockup ─────────────────────────────────────────── */}
          <div className="login-rise mb-8 flex flex-col items-center text-center">
            <div className="relative mb-5">
              <div className="absolute inset-0 rounded-2xl bg-primary/30 blur-xl" aria-hidden />
              <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl border border-primary/30 bg-primary/10">
                <FlaskConical className="h-7 w-7 text-primary" aria-hidden />
              </div>
            </div>
            <h1 className="text-4xl font-bold tracking-tight text-foreground">PFactory</h1>
            <p className="mt-2.5 font-mono text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
              Autonomous test generation &amp; execution
            </p>
          </div>

          {/* ── card ─────────────────────────────────────────────────── */}
          <div
            className="login-rise rounded-2xl border border-border bg-card/70 p-7 shadow-2xl backdrop-blur-xl"
            style={{ animationDelay: '110ms' }}
          >
            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label htmlFor="token" className="mb-2 block text-sm font-medium text-foreground">
                  API Token
                </label>
                <div className="relative">
                  <KeyRound
                    className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                    aria-hidden
                  />
                  <input
                    id="token"
                    type="password"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="Paste your token"
                    autoFocus
                    className="w-full rounded-xl border border-border bg-background/60 py-3 pl-10 pr-4 font-mono text-sm text-foreground placeholder:font-sans placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
                    required
                  />
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  Set it in the server&apos;s <span className="font-mono text-foreground/70">.env</span>{' '}
                  (<span className="font-mono">APP_API_TOKEN</span>).
                </p>
              </div>

              {error && (
                <div
                  role="alert"
                  className="rounded-xl border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
                >
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={isLoading || !token}
                className="group relative flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-3 font-semibold text-primary-foreground shadow-lg shadow-primary/20 transition-all duration-150 hover:bg-primary/90 hover:shadow-primary/30 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    Authenticating…
                  </>
                ) : (
                  <>
                    Continue
                    <ArrowRight
                      className="h-4 w-4 transition-transform duration-150 group-hover:translate-x-0.5"
                      aria-hidden
                    />
                  </>
                )}
              </button>
            </form>

            {/* SSO — see #26 P3.7: shown unconditionally; backend 302s or 404s */}
            <div className="mt-6">
              <div className="relative mb-5 flex items-center">
                <div className="h-px flex-1 bg-border" />
                <span className="px-3 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  or
                </span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <button
                type="button"
                onClick={() => {
                  window.location.href = '/api/auth/oidc/login';
                }}
                className="flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-background/40 py-3 font-medium text-foreground transition-colors duration-150 hover:border-primary/40 hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background"
              >
                <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden />
                Sign in with SSO
              </button>
              <p className="mt-2.5 text-center text-xs text-muted-foreground">
                Single sign-on via your organization&apos;s identity provider
              </p>
            </div>
          </div>

          {/* ── footer signature ─────────────────────────────────────── */}
          <p
            className="login-rise mt-7 text-center font-mono text-[11px] text-muted-foreground/70"
            style={{ animationDelay: '210ms' }}
          >
            <span className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-success align-middle" />
            v0.2 · five-lane pipeline — unit · browser · api · integration · mutation
          </p>
        </div>
      </div>

      <style>{`
        @keyframes loginRise {
          from { opacity: 0; transform: translateY(14px); }
          to   { opacity: 1; transform: none; }
        }
        .login-rise { animation: loginRise 0.5s cubic-bezier(0.22, 1, 0.36, 1) both; }
        @media (prefers-reduced-motion: reduce) { .login-rise { animation: none; } }
      `}</style>
    </div>
  );
}
