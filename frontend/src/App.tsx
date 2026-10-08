import { useEffect } from "react";
import { useAuth } from "./lib/auth";
import { matchPath, navigate, useLocation } from "./lib/router";
import { Shell } from "./components/Shell";
import { Empty, Loading } from "./components/ui";
import { LoginPage } from "./pages/LoginPage";
import { CasesPage } from "./pages/CasesPage";
import { CasePage, CASE_TABS, type CaseTab } from "./pages/CasePage";
import { AuditPage } from "./pages/AuditPage";

export function App() {
  const { status, user, canAudit } = useAuth();
  const { pathname, search } = useLocation();

  // Sin sesión: todo lleva al login y se recuerda a dónde se quería ir.
  useEffect(() => {
    if (status === "anonymous" && pathname !== "/login") {
      navigate(`/login?next=${encodeURIComponent(`${pathname}${search}`)}`, { replace: true });
    }
    if (status === "authenticated" && pathname === "/login") {
      const next = new URLSearchParams(search).get("next");
      navigate(next && next.startsWith("/") ? next : "/casos", { replace: true });
    }
    if (status === "authenticated" && (pathname === "/" || pathname === "")) {
      navigate("/casos", { replace: true });
    }
  }, [status, pathname, search]);

  if (status === "loading") {
    return (
      <div className="splash">
        <Loading label="Verificando la sesión…" rows={2} />
      </div>
    );
  }

  if (status === "anonymous" || pathname === "/login") {
    return <LoginPage />;
  }

  if (!user) {
    return null;
  }

  const caseMatch = matchPath("/casos/:caseId", pathname);
  if (caseMatch) {
    return <Redirect to={`/casos/${caseMatch.caseId}/grafo`} />;
  }

  const caseTabMatch = matchPath("/casos/:caseId/:tab", pathname);
  if (caseTabMatch) {
    const tab = caseTabMatch.tab as CaseTab;
    const caseId = Number(caseTabMatch.caseId);
    if (!Number.isInteger(caseId) || caseId <= 0 || !CASE_TABS.includes(tab)) {
      return <NotFound />;
    }
    return (
      <Shell>
        <CasePage caseId={caseId} tab={tab} />
      </Shell>
    );
  }

  if (matchPath("/casos", pathname)) {
    return (
      <Shell>
        <CasesPage />
      </Shell>
    );
  }

  if (matchPath("/auditoria", pathname)) {
    return (
      <Shell>
        {canAudit ? (
          <AuditPage />
        ) : (
          <Empty title="No tenés acceso a la auditoría">
            La auditoría la ven los roles auditor y administrador. Tu rol actual no la incluye.
          </Empty>
        )}
      </Shell>
    );
  }

  return <NotFound />;
}

function Redirect({ to }: { to: string }) {
  useEffect(() => {
    navigate(to, { replace: true });
  }, [to]);
  return null;
}

function NotFound() {
  return (
    <Shell>
      <Empty title="Esta pantalla no existe" action={<a className="btn btn-default" href="/casos">Volver a casos</a>}>
        Revisá la dirección o volvé a la lista de casos.
      </Empty>
    </Shell>
  );
}
