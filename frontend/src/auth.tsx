import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, type Me } from "./api";

interface AuthConfig {
  mode: "dev" | "oidc";
  provider_name: string;
  app_name: string;
  environment: string;
}

interface AuthState {
  me: Me | null;
  config: AuthConfig | null;
  loading: boolean;
  can: (perm: string) => boolean;
  refresh: () => Promise<void>;
  setMe: (m: Me | null) => void;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthState>(null as unknown as AuthState);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setMe(await api.get<Me>("/api/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null);
      else throw e;
    }
  }, []);

  useEffect(() => {
    Promise.all([api.get<AuthConfig>("/api/auth/config").then(setConfig), refresh()]).finally(() =>
      setLoading(false),
    );
  }, [refresh]);

  const logout = async () => {
    const r = await api.post<{ logout_url: string | null }>("/api/auth/logout");
    setMe(null);
    if (r.logout_url) window.location.href = r.logout_url;
  };

  const can = (perm: string) => !!me && (me.is_admin || me.permissions.includes(perm));

  return <Ctx.Provider value={{ me, config, loading, can, refresh, setMe, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
