import { useEffect, useState } from "react";
import * as ep from "../api/endpoints";
import type { AuditEventOut, CaseOut, User } from "../api/types";
import { Button, Chip, ErrorBox, Loading, Notice, PageTitle, Panel, Help, type Tone } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { fmtDateTime, fmtInt, shortHash } from "../lib/format";
import { auditActionLabel } from "../lib/auditLabels";
import { useAuth } from "../lib/auth";
import { navigate, useLocation, Link } from "../lib/router";

const ACTION_FILTERS: Array<{ value: string; label: string }> = [
  { value: "", label: "Todas las acciones" },
  { value: "auth.", label: "Inicios de sesión y contraseñas" },
  { value: "case.", label: "Casos y equipo" },
  { value: "collect.", label: "Recolecciones" },
  { value: "tool.", label: "Herramientas OSINT" },
  { value: "entity.", label: "Entidades" },
  { value: "menard.", label: "MENARD" },
  { value: "funes.", label: "FUNES" },
  { value: "attack.", label: "ATT&CK" },
  { value: "source.", label: "Fuentes" },
  { value: "audit.", label: "Auditoría" },
];

interface Page {
  events: AuditEventOut[];
  total: number;
}

function outcomeTone(action: string): Tone {
  return action.includes("failed") ? "bad" : action.includes("delete") ? "warn" : "neutral";
}

export function ActividadPage() {
  const auth = useAuth();
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const caseParam = params.get("caso") ?? "";
  const [caseId, setCaseId] = useState<string>(caseParam);
  const [action, setAction] = useState<string>("");
  const [limit, setLimit] = useState(50);

  const canAudit = auth.canAudit;
  const verify = useAsync(() => (canAudit ? ep.verifyAudit() : Promise.resolve(null)), [canAudit]);
  const cases = useAsync<CaseOut[]>(() => ep.listCases({ limit: 200 }).then((p) => p.items).catch(() => []), []);
  const users = useAsync<User[]>(
    () => (auth.isAdmin ? ep.listUsers().then((p) => p.items).catch(() => []) : Promise.resolve([])),
    [auth.isAdmin],
  );
  const events = useAsync<Page | null>(
    () =>
      !canAudit
        ? Promise.resolve(null)
        : ep
        .listAuditEvents({
          case_id: caseId ? Number(caseId) : undefined,
          action: action || undefined,
          order: "desc",
          limit,
        })
        .then((p) => ({ events: p.items, total: p.total })),
    [caseId, action, limit, canAudit],
  );

  useEffect(() => {
    setCaseId(caseParam);
  }, [caseParam]);

  if (!canAudit) {
    return <Notice tone="warn">Esta pantalla es solo para auditores y administradores.</Notice>;
  }

  const userName = (id: number | null) => {
    if (id === null) return "sistema";
    const found = users.data?.find((u) => u.id === id);
    return found ? found.username : `usuario ${id}`;
  };
  const caseName = (id: number | null) => {
    if (id === null) return "";
    return cases.data?.find((c) => c.id === id)?.name ?? `caso ${id}`;
  };

  return (
    <div className="page">
      <PageTitle lead="Cada acción queda registrada con la hora y quién la hizo. Cada registro incluye la huella del anterior, así que si alguien altera uno, la cadena se rompe.">
        Registro de actividad
      </PageTitle>

      <Panel
        title="Integridad de la cadena"
        actions={
          <Button variant="secondary" onClick={verify.reload}>
            Verificar de nuevo
          </Button>
        }
      >
        {verify.error ? <ErrorBox error={verify.error} onRetry={verify.reload} /> : null}
        {!verify.data && !verify.error && <Loading what="Verificando la cadena" />}
        {verify.data && (
          <Notice tone={verify.data.ok ? "ok" : "bad"} title={verify.data.ok ? "Íntegra" : "Cadena rota"}>
            <p>{verify.data.detail}</p>
            <p className="small">
              Eventos verificados: {fmtInt(verify.data.total_events)}.{" "}
              <Help term="cadena" />
            </p>
          </Notice>
        )}
      </Panel>

      <Panel title="Eventos">
        <div className="row" style={{ marginBottom: 14 }}>
          <label className="row small" style={{ gap: 6 }}>
            <span className="muted">Caso</span>
            <select
              className="select"
              value={caseId}
              onChange={(e) => {
                setCaseId(e.target.value);
                navigate(e.target.value ? `/actividad?caso=${e.target.value}` : "/actividad", { replace: true });
              }}
            >
              <option value="">Todos los casos</option>
              {(cases.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="row small" style={{ gap: 6 }}>
            <span className="muted">Tipo</span>
            <select className="select" value={action} onChange={(e) => setAction(e.target.value)}>
              {ACTION_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {events.error ? <ErrorBox error={events.error} onRetry={events.reload} /> : null}
        {!events.data && !events.error && <Loading what="Cargando eventos" />}
        {events.data && (
          <>
            <p className="small muted">
              Mostrando {fmtInt(events.data.events.length)} de {fmtInt(events.data.total)} eventos.
            </p>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Cuándo</th>
                    <th scope="col">Quién</th>
                    <th scope="col">Qué pasó</th>
                    <th scope="col">Dónde</th>
                    <th scope="col">Huella</th>
                  </tr>
                </thead>
                <tbody>
                  {events.data.events.map((ev) => (
                    <tr key={ev.id}>
                      <td className="small" style={{ whiteSpace: "nowrap" }}>{fmtDateTime(ev.ts)}</td>
                      <td>{userName(ev.user_id)}</td>
                      <td>
                        <Chip tone={outcomeTone(ev.action)}>{auditActionLabel(ev.action)}</Chip>
                        {ev.target && <div className="small muted mono">{ev.target}</div>}
                      </td>
                      <td className="small">
                        {ev.case_id !== null ? (
                          <Link to={`/casos/${ev.case_id}/entender`}>{caseName(ev.case_id)}</Link>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="mono small" title={ev.hash}>
                        {shortHash(ev.hash, 12)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {events.data.events.length < events.data.total && (
              <div style={{ marginTop: 12 }}>
                <Button onClick={() => setLimit((n) => n + 50)}>Mostrar más</Button>
              </div>
            )}
          </>
        )}
        {!auth.isAdmin && (
          <p className="small muted" style={{ marginTop: 10 }}>
            Los nombres de usuario se ven solo para el administrador; para vos aparece el número de usuario.
          </p>
        )}
      </Panel>
    </div>
  );
}
