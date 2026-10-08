import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import * as endpoints from "../api/endpoints";
import { onUnauthorized, readStoredToken, setToken } from "../api/client";
import type { Role, User } from "../api/types";
import { navigate } from "./router";

type Status = "loading" | "anonymous" | "authenticated";

export interface AuthValue {
  status: Status;
  user: User | null;
  /** Analista o administrador: puede crear y modificar (con el caso abierto). */
  canWrite: boolean;
  /** Auditor o administrador: ve la auditoría. */
  canAudit: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: (reason?: "manual" | "expired") => void;
}

const AuthContext = createContext<AuthValue | null>(null);

const WRITE_ROLES: Role[] = ["admin", "analyst"];
const AUDIT_ROLES: Role[] = ["admin", "auditor"];

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);

  const clearSession = useCallback(() => {
    setToken(null);
    setUser(null);
    setStatus("anonymous");
  }, []);

  useEffect(() => {
    onUnauthorized(() => {
      clearSession();
      // Se recuerda dónde estaba el analista para volver ahí después de entrar de nuevo.
      const here = window.location.pathname + window.location.search;
      const next = here.startsWith("/login") || here === "/" ? "" : `&next=${encodeURIComponent(here)}`;
      navigate(`/login?motivo=vencida${next}`, { replace: true });
    });
    return () => onUnauthorized(null);
  }, [clearSession]);

  useEffect(() => {
    const stored = readStoredToken();
    if (!stored) {
      setStatus("anonymous");
      return;
    }
    setToken(stored);
    endpoints
      .me()
      .then((current) => {
        setUser(current);
        setStatus("authenticated");
      })
      .catch(() => {
        clearSession();
      });
  }, [clearSession]);

  const login = useCallback(async (username: string, password: string) => {
    const token = await endpoints.login(username, password);
    setToken(token.access_token);
    setUser(token.user);
    setStatus("authenticated");
    return token.user;
  }, []);

  const logout = useCallback(
    (reason: "manual" | "expired" = "manual") => {
      clearSession();
      navigate(reason === "expired" ? "/login?motivo=vencida" : "/login", { replace: true });
    },
    [clearSession],
  );

  const value = useMemo<AuthValue>(() => {
    const role = (user?.role ?? "") as Role;
    return {
      status,
      user,
      canWrite: WRITE_ROLES.includes(role),
      canAudit: AUDIT_ROLES.includes(role),
      isAdmin: role === "admin",
      login,
      logout,
    };
  }, [status, user, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth debe usarse dentro de AuthProvider");
  return value;
}
