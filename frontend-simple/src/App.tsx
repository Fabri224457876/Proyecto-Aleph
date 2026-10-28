import { useEffect, type ReactNode } from "react";
import { useAuth } from "./lib/auth";
import { matchPath, navigate, useLocation } from "./lib/router";
import { Shell } from "./components/Shell";
import { CaseLayout } from "./components/CaseLayout";
import { Empty, Loading } from "./components/ui";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { NewCasePage } from "./pages/NewCasePage";
import { ReunirPage } from "./pages/ReunirPage";
import { RevisarPage } from "./pages/RevisarPage";
import { EntenderPage } from "./pages/EntenderPage";
import { EquipoPage } from "./pages/EquipoPage";
import { MasPage } from "./pages/MasPage";
import { ActividadPage } from "./pages/ActividadPage";
import { UsuariosPage } from "./pages/UsuariosPage";
import { CuentaPage } from "./pages/CuentaPage";
import { CaseIndex } from "./pages/CaseIndex";
import { Link } from "./lib/router";

type Params = Record<string, string>;

function caseId(params: Params): number {
  return Number(params.id);
}

const ROUTES: Array<[string, (params: Params) => ReactNode]> = [
  ["/", () => <HomePage />],
  ["/casos/nuevo", () => <NewCasePage />],
  ["/casos/:id", (p) => <CaseIndex key={caseId(p)} caseId={caseId(p)} />],
  [
    "/casos/:id/reunir",
    (p) => (
      <CaseLayout key={caseId(p)} caseId={caseId(p)} active="reunir">
        <ReunirPage />
      </CaseLayout>
    ),
  ],
  [
    "/casos/:id/revisar",
    (p) => (
      <CaseLayout key={caseId(p)} caseId={caseId(p)} active="revisar">
        <RevisarPage />
      </CaseLayout>
    ),
  ],
  [
    "/casos/:id/entender",
    (p) => (
      <CaseLayout key={caseId(p)} caseId={caseId(p)} active="entender">
        <EntenderPage />
      </CaseLayout>
    ),
  ],
  [
    "/casos/:id/equipo",
    (p) => (
      <CaseLayout key={caseId(p)} caseId={caseId(p)} active="equipo">
        <EquipoPage />
      </CaseLayout>
    ),
  ],
  [
    "/casos/:id/mas",
    (p) => (
      <CaseLayout key={caseId(p)} caseId={caseId(p)} active="mas">
        <MasPage />
      </CaseLayout>
    ),
  ],
  ["/actividad", () => <ActividadPage />],
  ["/usuarios", () => <UsuariosPage />],
  ["/cuenta", () => <CuentaPage />],
];

function resolve(pathname: string): ReactNode {
  for (const [pattern, render] of ROUTES) {
    const params = matchPath(pattern, pathname);
    if (params) return render(params);
  }
  return (
    <div className="page">
      <Empty
        title="Esa página no existe."
        action={
          <Link to="/" className="btn btn-primary">
            Ir a mis casos
          </Link>
        }
      />
    </div>
  );
}

function RedirectHome() {
  useEffect(() => {
    navigate("/", { replace: true });
  }, []);
  return <Loading what="Redirigiendo" />;
}

export function App() {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") return <Loading what="Cargando Aleph" />;
  if (auth.status === "anonymous") return <LoginPage />;
  if (location.pathname === "/login") return <RedirectHome />;

  return <Shell>{resolve(location.pathname)}</Shell>;
}
