import * as ep from "../api/endpoints";
import type { CaseOut } from "../api/types";
import { Button, Chip, Empty, ErrorBox, Loading, PageTitle, Panel, StatusChip, TlpChip } from "../components/ui";
import { useAuth } from "../lib/auth";
import { fmtDate, plural } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { Link } from "../lib/router";
import { ROLE_LABEL } from "../lib/labels";

export function HomePage() {
  const auth = useAuth();
  const cases = useAsync(() => ep.listCases({ limit: 200 }), []);

  return (
    <div className="page">
      <div className="hero">
        <PageTitle lead={`Estás como ${ROLE_LABEL[auth.user?.role ?? ""] ?? "usuario"}. Para cada caso, el camino es el mismo: reunir, revisar y entender.`}>
          Hola, {auth.user?.username}.
        </PageTitle>
      </div>

      <div className="steps-explain" aria-label="Cómo se trabaja un caso">
        <div>
          <strong>1 · Reunir</strong>
          <span className="small muted">Escribí o soltá un dato o un archivo. Te digo qué podés hacer con él.</span>
        </div>
        <div>
          <strong>2 · Revisar</strong>
          <span className="small muted">Lo que el sistema propone espera tu decisión. Nada se confirma solo.</span>
        </div>
        <div>
          <strong>3 · Entender y contar</strong>
          <span className="small muted">El caso ordenado, con su resumen, sus conexiones y su informe.</span>
        </div>
      </div>

      <Panel
        title="Tus casos"
        actions={
          auth.canWrite ? (
            <Link to="/casos/nuevo" className="btn btn-primary">
              Abrir un caso nuevo
            </Link>
          ) : null
        }
      >
        {cases.error ? <ErrorBox error={cases.error} onRetry={cases.reload} /> : null}
        {!cases.data && !cases.error && <Loading what="Buscando tus casos" />}
        {cases.data && cases.data.items.length === 0 && (
          <Empty
            title="Todavía no tenés casos."
            action={
              auth.canWrite ? (
                <Link to="/casos/nuevo" className="btn btn-primary btn-big">
                  Abrir el primer caso
                </Link>
              ) : undefined
            }
          >
            <p>
              {auth.canWrite
                ? "Te guía un asistente de tres pasos: nombre, base legal y nivel de reserva."
                : "Cuando un analista te agregue a un caso, o si tu rol ve todos, van a aparecer acá."}
            </p>
          </Empty>
        )}
        {cases.data && cases.data.items.length > 0 && (
          <>
            <p className="small muted" style={{ marginBottom: 10 }}>
              {plural(cases.data.total, "caso", "casos")}.
            </p>
            <ul className="list">
              {cases.data.items.map((c: CaseOut) => (
                <li key={c.id}>
                  <div className="item">
                    <div className="item-main">
                      <Link to={`/casos/${c.id}`} className="item-link">
                        <p className="item-title">{c.name}</p>
                      </Link>
                      <p className="item-meta">
                        Abierto el {fmtDate(c.created_at)}
                        {c.description ? ` · ${c.description.slice(0, 120)}` : ""}
                      </p>
                    </div>
                    <div className="row">
                      <TlpChip tlp={c.tlp} />
                      <StatusChip status={c.status} />
                      <Chip>Caso {c.id}</Chip>
                      <Link to={`/casos/${c.id}`} className="btn btn-secondary btn-small">
                        Abrir
                      </Link>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </Panel>

      {auth.canWrite && cases.data && cases.data.items.length > 0 && (
        <div className="btn-row">
          <Link to="/casos/nuevo" className="btn btn-secondary">
            Abrir otro caso
          </Link>
          <Button variant="ghost" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>
            Volver arriba
          </Button>
        </div>
      )}
    </div>
  );
}
