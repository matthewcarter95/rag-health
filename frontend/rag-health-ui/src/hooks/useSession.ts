import { useState, useEffect, useCallback } from 'react';

const API_URL = 'https://5e3ecqd7qkwygbyik3fmh5qq4u0qlhul.lambda-url.us-east-1.on.aws';

interface User {
  id: string;
  email: string;
  name?: string;
  picture?: string;
  subscription_tier: string;
  roles: string[];
}

interface SessionState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
}

interface UseSessionReturn extends SessionState {
  login: () => Promise<void>;
  logout: () => Promise<void>;
  checkSession: () => Promise<void>;
}

/**
 * Custom hook for BFF session-based authentication.
 *
 * Replaces Auth0 React SDK's useAuth0 hook.
 * Uses HTTP-only cookies for session management.
 */
export function useSession(): UseSessionReturn {
  const [state, setState] = useState<SessionState>({
    user: null,
    isLoading: true,
    isAuthenticated: false,
  });

  /**
   * Check if user has an active session.
   * Called on mount and after OAuth callback.
   */
  const checkSession = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/auth/me`, {
        method: 'GET',
        credentials: 'include', // Send cookies
      });

      if (res.ok) {
        const data = await res.json();
        if (data.authenticated && data.user) {
          setState({
            user: data.user,
            isLoading: false,
            isAuthenticated: true,
          });
          return;
        }
      }

      // Not authenticated
      setState({
        user: null,
        isLoading: false,
        isAuthenticated: false,
      });
    } catch (error) {
      console.error('Session check failed:', error);
      setState({
        user: null,
        isLoading: false,
        isAuthenticated: false,
      });
    }
  }, []);

  /**
   * Initiate login flow.
   * Calls /auth/login to get Auth0 authorization URL, then redirects.
   */
  const login = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/auth/login`, {
        method: 'POST',
        credentials: 'include',
      });

      if (!res.ok) {
        throw new Error('Failed to initiate login');
      }

      const data = await res.json();
      if (data.authorization_url) {
        // Redirect to Auth0
        window.location.href = data.authorization_url;
      } else {
        console.error('No authorization URL in response');
      }
    } catch (error) {
      console.error('Login failed:', error);
    }
  }, []);

  /**
   * Log out the user.
   * Calls /auth/logout to clear server-side session.
   */
  const logout = useCallback(async () => {
    try {
      await fetch(`${API_URL}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch (error) {
      console.error('Logout request failed:', error);
    }

    // Clear local state regardless of server response
    setState({
      user: null,
      isLoading: false,
      isAuthenticated: false,
    });
  }, []);

  // Check session on mount
  useEffect(() => {
    checkSession();
  }, [checkSession]);

  // Handle OAuth callback (check URL for auth_error or code)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);

    // Check for auth error
    const authError = params.get('auth_error');
    if (authError) {
      console.error('Authentication error:', authError);
      // Clean URL
      window.history.replaceState({}, '', window.location.pathname);
      return;
    }

    // If we have code/state params, the callback was handled server-side
    // and we just need to refresh session state
    if (params.has('code') || params.has('state')) {
      // Clean URL and refresh session
      window.history.replaceState({}, '', window.location.pathname);
      checkSession();
    }
  }, [checkSession]);

  return {
    user: state.user,
    isLoading: state.isLoading,
    isAuthenticated: state.isAuthenticated,
    login,
    logout,
    checkSession,
  };
}

export default useSession;
