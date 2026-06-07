/**
 * Authentication store for web UI
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { getAuthToken, setAuthToken, clearAuthToken } from '../lib/auth';

interface AuthState {
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;

  // Actions
  login: (token: string) => Promise<boolean>;
  logout: () => void;
  checkAuth: () => Promise<boolean>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      isAuthenticated: false,
      isLoading: false,
      error: null,

      login: async (token: string) => {
        set({ isLoading: true, error: null });

        try {
          // Validate token by making a test request
          const response = await fetch('/api/health', {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          });

          if (response.ok) {
            setAuthToken(token);
            set({ isAuthenticated: true, isLoading: false });
            return true;
          } else {
            set({ error: 'Invalid token', isLoading: false });
            return false;
          }
        } catch (error) {
          set({
            error: error instanceof Error ? error.message : 'Login failed',
            isLoading: false,
          });
          return false;
        }
      },

      logout: () => {
        clearAuthToken();
        set({ isAuthenticated: false, error: null });
      },

      checkAuth: async () => {
        // Two ways to be authenticated:
        //  1) a Bearer token in localStorage (API-token / password login), or
        //  2) the HttpOnly `access_token` cookie set by the OIDC/SSO callback.
        // The cookie can't be read from JS, so we can't short-circuit on a
        // missing localStorage token — that bug bounced SSO logins straight
        // back to /login. Instead, ask the backend: /api/auth/me is the source
        // of truth. Same-origin fetch sends the cookie; we also attach the
        // Bearer header when a token is present.
        const token = getAuthToken();

        set({ isLoading: true });

        try {
          const response = await fetch('/api/auth/me', {
            credentials: 'include',
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });

          if (response.ok) {
            set({ isAuthenticated: true, isLoading: false });
            return true;
          }

          // Rejected — clear a stale localStorage token if we had one.
          if (response.status === 401 || response.status === 403) {
            if (token) clearAuthToken();
            set({ isAuthenticated: false, isLoading: false });
          } else {
            // Server error — don't clear anything, backend might be starting.
            set({ isAuthenticated: false, isLoading: false });
          }

          return false;
        } catch {
          // Network error - don't clear token, backend might just be starting up
          set({ isAuthenticated: false, isLoading: false });
          return false;
        }
      },
    }),
    {
      name: 'pfactory-auth',
      partialize: (state) => ({
        // Don't persist isAuthenticated - always re-check on load
      }),
    }
  )
);
