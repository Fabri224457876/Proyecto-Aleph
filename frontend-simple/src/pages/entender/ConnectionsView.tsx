import { useState } from "react";
import * as ep from "../../api/endpoints";
import type { PathOut } from "../../api/types";
import { useCase } from "../../components/CaseLayout";
import { EntityPanel } from "../../components/EntityPanel";
import { GraphView } from "../../components/GraphView";
import { Button, Empty, ErrorBox, Loading, Notice, Panel, Help } from "../../components/ui";
import { useAsync } from "../../lib/useAsync";
import { errorMessage, plural } from "../../lib/format";

interface Endpoint {
  id: number;
  label: string;
}

export function ConnectionsView() {
  const ctx = useCase();
  const [withProposed, setWithProposed] = useState(false);
  const graph = useAsync(
    () => ep.getGraph(ctx.caseId, { statuses: withProposed ? ["confirmed", "proposed"] : ["confirmed"], limit: 300 }),
    [ctx.caseId, withProposed],
  );
  const [selected, setSelected] = useState<number | null>(null);
  const [from, setFrom] = useState<Endpoint | null>(null);
  const [to, setTo] = useState<Endpoint | null>(null);
  const [path, setPath] = useState<PathOut | null>(null);
  const [pathError, setPathError] = useState<unknown>(null);
  const [pathBusy, setPathBusy] = useState(false);

  function pickForPath(id: number, label: string) {
    setPath(null);
    setPathError(null);
    if (!from) setFrom({ id, label });
    else if (!to && id !== from.id) setTo({ id, label });
    else {
      setFrom({ id, label });
      setTo(null);
    }
  }

  async function findPath() {
    if (!from || !to) return;
    setPathBusy(true);
    setPathError(null);
    try {
      setPath(await ep.getPath(ctx.caseId, from.id, to.id, withProposed ? ["confirmed", "proposed"] : ["confirmed"]));
    } catch (err) {
      setPathError(err);
    } finally {
      setPathBusy(false);
    }
  }

  if (graph.error) return <ErrorBox error={graph.error} onRetry={graph.reload} />;
  if (!graph.data) return <Loading what="Dibujando las conexiones" />;
  const data = graph.data;

  return (
    <div className="stack">
      <Panel
        title="Conexiones"
        actions={
          <label className="check small">
            <input type="checkbox" checked={withProposed} onChange={(e) => setWithProposed(e.target.checked)} />
            Incluir propuestas (punteadas)
          </label>
        }
      >
        <p className="small muted">
          Una vista más del caso, para ver cómo se conectan las cosas. Hacé clic en un punto para ver sus detalles.{" "}
          <Help term="grafo" />
        </p>
        <div className="legend" style={{ marginTop: 8 }}>
          <span>Confirmada</span>
          <span className="l-prop">Propuesta</span>
          <span>{plural(data.nodes.length, "entidad", "entidades")} · {plural(data.edges.length, "relación", "relaciones")}</span>
          {data.truncated && <span>Mostramos una parte: el caso tiene más de lo que entra en pantalla.</span>}
        </div>
      </Panel>

      {data.nodes.length === 0 ? (
        <Empty title="No hay entidades para mostrar.">
          <p>Confirmá algo en Revisar o activá «Incluir propuestas».</p>
        </Empty>
      ) : (
        <div className="graph-layout">
          <GraphView
            graph={data}
            selectedId={selected}
            pathIds={path?.nodes?.map((n) => n.id) ?? []}
            pathEdgeIds={path?.edges?.map((e) => e.id) ?? []}
            onSelect={(id) => {
              setSelected(id);
            }}
          />
          <aside className="graph-side" aria-label="Detalle del punto elegido">
            <Panel title="Camino más corto">
              <div className="stack small">
                <p className="muted">
                  Elegí un origen y un destino con los botones de «usar como origen o destino», y buscá el camino.
                </p>
                <p>
                  <strong>Origen:</strong> {from ? from.label : "sin elegir"}
                  <br />
                  <strong>Destino:</strong> {to ? to.label : "sin elegir"}
                </p>
                <div className="btn-row">
                  <Button variant="primary" disabled={!from || !to || pathBusy} onClick={() => void findPath()}>
                    {pathBusy ? "Buscando…" : "Buscar camino"}
                  </Button>
                  <Button variant="ghost" onClick={() => { setFrom(null); setTo(null); setPath(null); setPathError(null); }}>
                    Limpiar
                  </Button>
                </div>
                {pathError ? <Notice tone="bad"><p>{errorMessage(pathError)}</p></Notice> : null}
                {path && !path.found && <Notice tone="warn"><p>No hay un camino entre ambos en el grafo.</p></Notice>}
                {path?.found && (
                  <Notice tone="info">
                    <p>Camino de {plural(path.length ?? 0, "paso", "pasos")}, resaltado en el mapa.</p>
                  </Notice>
                )}
              </div>
            </Panel>
            <div style={{ height: 14 }} />
            <Panel title={selected ? "Detalle" : "Elegí un punto"}>
              {selected ? (
                <EntityPanel
                  key={selected}
                  entityId={selected}
                  onPickPath={pickForPath}
                  onChanged={graph.reload}
                  onDeleted={() => {
                    setSelected(null);
                    graph.reload();
                  }}
                  onOpenEntity={(id) => setSelected(id)}
                />
              ) : (
                <p className="small muted">Hacé clic en un punto del mapa para ver sus datos y sus conexiones.</p>
              )}
            </Panel>
          </aside>
        </div>
      )}
    </div>
  );
}
