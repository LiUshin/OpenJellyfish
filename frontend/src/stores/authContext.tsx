import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import type { User } from '../types';
import * as api from '../services/api';
import { setTzOffset } from '../utils/timezone';

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, regKey: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = api.getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    api
      .getMe()
      .then((u) => {
        setUser(u);
        api.getPreferences().then(p => setTzOffset(p.tz_offset_hours ?? 8)).catch(() => {});
      })
      .catch(() => api.clearToken())
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await api.login(username, password);
    api.setToken(result.token);
    localStorage.setItem('user_id', result.user_id);
    localStorage.setItem('username', result.username);
    setUser({ user_id: result.user_id, username: result.username });
  }, []);

  const register = useCallback(async (username: string, password: string, regKey: string) => {
    const result = await api.register(username, password, regKey);
    api.setToken(result.token);
    localStorage.setItem('user_id', result.user_id);
    localStorage.setItem('username', result.username);
    setUser({ user_id: result.user_id, username: result.username });
  }, []);

  const logout = useCallback(() => {
    api.clearToken();
    localStorage.removeItem('user_id');
    localStorage.removeItem('username');
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
