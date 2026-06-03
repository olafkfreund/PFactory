/**
 * Authentication utilities for web API
 */

const TOKEN_KEY = 'pfactory-token';

export function getAuthToken(): string | null {
  let token = localStorage.getItem(TOKEN_KEY);
  if (!token) {
    try {
      const legacyToken = localStorage.getItem('magestic-ai-token');
      if (legacyToken) {
        localStorage.setItem(TOKEN_KEY, legacyToken);
        localStorage.removeItem('magestic-ai-token');
        token = legacyToken;
      }
    } catch {
      // localStorage may be unavailable
    }
  }
  return token;
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return !!getAuthToken();
}

export function getAuthHeaders(): Record<string, string> {
  const token = getAuthToken();
  if (!token) {
    return {};
  }
  return {
    Authorization: `Bearer ${token}`,
  };
}

/**
 * Get WebSocket URL with auth token as query param
 * Uses VITE_WS_BASE_URL env var if set, otherwise uses current host
 *
 * In development mode with Vite proxy, we need to connect to the Vite server
 * which will proxy the WebSocket connection to the backend.
 */
export function getAuthenticatedWsUrl(path: string): string {
  const token = getAuthToken();

  // Check for explicit WebSocket base URL (for remote deployments or production)
  const wsBaseUrl = import.meta.env.VITE_WS_BASE_URL;

  let url: URL;
  if (wsBaseUrl) {
    // Use configured WebSocket URL (for production or explicit configuration)
    url = new URL(path, wsBaseUrl);
  } else {
    // Use same host as page - this works with Vite's proxy in dev mode
    // because the browser connects to Vite, which forwards to the backend
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    url = new URL(`${protocol}//${host}${path}`);
  }

  if (token) {
    url.searchParams.set('token', token);
  }
  return url.toString();
}
