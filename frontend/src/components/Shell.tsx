import type { ReactNode } from "react";
import { useAuth } from "../lib/auth";
import { useCaseChip } from "../lib/caseChip";
import { ROLE_LABEL } from "../lib/labels";
import { Link, useLocation } from "../lib/router";
import { classNames } from "../lib/format";
import { Badge, IconButton, TlpBadge } from "./ui";

function isActive(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function Shell({ children }: { children: ReactNode }) {
  const { user, canAudit, logout } = useAuth();
  const { pathname } = useLocation();
  const chip = useCaseChip();

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/casos" className="brand" aria-label="Aleph, ir a casos">
          <svg className="brand-mark" viewBox="0 0 32 32" width="18" height="18" aria-hidden="true">
            <path d="M8 24 16 8l8 16" fill="none" stroke="currentColor" strokeWidth="2.6" />
            <circle cx="16" cy="19" r="2.2" fill="currentColor" />
          </svg>
          <span className="brand-name">ALEPH</span>
          <span className="brand-sub">P.R.O.A.</span>
        </Link>

        <nav className="topnav" aria-label="Principal">
          <Link to="/casos" className={classNames("topnav-item", isActive(pathname, "/casos") && "on")}>
            Casos
          </Link>
          {canAudit ? (
            <Link to="/auditoria" className={classNames("topnav-item", isActive(pathname, "/auditoria") && "on")}>
              Auditoría
            </Link>
          ) : null}
        </nav>

        <div className="topbar-case">
          {chip ? (
            <Link to={`/casos/${chip.id}/grafo`} className="case-chip" title="Caso activo">
              <span className="case-chip-id">#{chip.id}</span>
              <span className="case-chip-name">{chip.name}</span>
              <TlpBadge tlp={chip.tlp} />
            </Link>
          ) : null}
        </div>

        <div className="userbox">
          {user ? (
            <>
              <span className="user-name" title={`Sesión de ${user.username}`}>
                {user.username}
              </span>
              <Badge tone={user.role === "admin" ? "amber" : "neutral"}>{ROLE_LABEL[user.role] ?? user.role}</Badge>
            </>
          ) : null}
          <IconButton icon="logout" label="Cerrar sesión" onClick={() => logout("manual")} />
        </div>
      </header>
      <main className="main">{children}</main>
    </div>
  );
}
