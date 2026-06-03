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
        const token = getAuthToken();

        // No token stored — not authenticated, don't even call the backend
        if (!token) {
          set({ isAuthenticated: false, isLoading: false });
          return false;
        }

        set({ isLoading: true });

        try {
          // Validate token against a protected endpoint (not /api/health which is public)
          const response = await fetch('/api/settings', {
            headers: { Authorization: `Bearer ${token}` },
          });

          if (response.ok) {
            set({ isAuthenticated: true, isLoading: false });
            return true;
          }

          // Token rejected — clear it
          if (response.status === 401 || response.status === 403) {
            clearAuthToken();
            set({ isAuthenticated: false, isLoading: false });
          } else {
            // Server error but token might still be valid - keep it
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
