import cytoscape from "cytoscape";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import * as endpoints from "../../api/endpoints";
import type { FindingOut, GraphEdge, GraphNode, GraphOut, PathOut, SourceOut } from "../../api/types";
import { Admiralty, Badge, Bar, Btn, Empty, ErrorBox, IconButton, Loading, StatusBadge } from "../../components/ui";
import { Icon } from "../../components/Icon";
import { ConfirmDialog } from "../../components/Dialogs";
import { errorMessage, fmtDateTime, fmtInt, pct, propText, truncate } from "../../lib/format";
import { ITEM_STATUS_LABEL, SOURCE_KIND_LABEL, entityTypeLabel, relationLabel } from "../../lib/labels";
import { navigate, useLocation } from "../../lib/router";
import { useToast } from "../../lib/toast";
import { useAsync } from "../../lib/useAsync";
import type { CaseTabProps } from "./tabProps";
import { GRAPH_STYLE, normalizeText, typeStyle } from "./graphStyle";
import { EntityDialog, MergeDialog, RelationDialog } from "./GraphDialogs";

const ALL_STATUSES = ["proposed", "confirmed", "rejected"] as const;
const EMPTY_NODES: GraphNode[] = [];
const EMPTY_EDGES: GraphEdge[] = [];

interface Filters {
  query: string;
  hiddenTypes: string[];
  showProposed: boolean;
  showConfirmed: boolean;
  showRejected: boolean;
  minConfidence: number; // 0..100
  hideIsolated: boolean;
}

const DEFAULT_FILTERS: Filters = {
  query: "",
  hiddenTypes: [],
  showProposed: true,
  showConfirmed: true,
  showRejected: false,
  minConfidence: 0,
  hideIsolated: false,
};

type Selection = { kind: "node"; id: number } | { kind: "edge"; id: number } | null;

type Dialog =
  | { kind: "new-entity" }
  | { kind: "edit-entity"; node: GraphNode }
  | { kind: "merge"; node: GraphNode }
  | { kind: "new-relation"; node: GraphNode }
  | { kind: "delete-entity"; node: GraphNode }
  | { kind: "delete-relation"; edge: GraphEdge }
  | null;

interface VisibleGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

function computeVisible(nodes: GraphNode[], edges: GraphEdge[], filters: Filters): VisibleGraph {
  const allowed = new Set<string>();
  if (filters.showProposed) allowed.add("proposed");
  if (filters.showConfirmed) allowed.add("confirmed");
  if (filters.showRejected) allowed.add("rejected");
  const minConf = filters.minConfidence / 100;
  const hidden = new Set(filters.hiddenTypes);

  const baseNodes = nodes.filter((n) => allowed.has(n.status) && !hidden.has(n.type) && n.confidence >= minConf - 1e-9);
  const ids = new Set(baseNodes.map((n) => n.id));
  const visibleEdges = edges.filter(
    (e) => allowed.has(e.status) && ids.has(e.source) && ids.has(e.target) && e.confidence >= minConf - 1e-9,
  );
  if (!filters.hideIsolated) return { nodes: baseNodes, edges: visibleEdges };

  const connected = new Set<number>();
  for (const e of visibleEdges) {
    connected.add(e.source);
    connected.add(e.target);
  }
  return { nodes: baseNodes.filter((n) => connected.has(n.id)), edges: visibleEdges };
}

function shortLabel(label: string): string {
  return truncate(label, 28);
}

export function GraphTab({ caseId, writable, refreshCase }: CaseTabProps) {
  const toast = useToast();
  const { search } = useLocation();
  const graph = useAsync<GraphOut>(
    () => endpoints.getGraph(caseId, { statuses: [...ALL_STATUSES], limit: 2000 }),
    [caseId],
  );

  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [selection, setSelection] = useState<Selection>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [pathFrom, setPathFrom] = useState<number | null>(null);
  const [pathTo, setPathTo] = useState<number | null>(null);
  const [pathResult, setPathResult] = useState<PathOut | null>(null);
  const [pathBusy, setPathBusy] = useState(false);
  const [pathError, setPathError] = useState<unknown>(null);

  const hostRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const positionsRef = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pendingNodeRef = useRef<number | null>(
    (() => {
      const value = Number(new URLSearchParams(search).get("nodo"));
      return Number.isInteger(value) && value > 0 ? value : null;
    })(),
  );

  const allNodes = useMemo(() => graph.data?.nodes ?? EMPTY_NODES, [graph.data]);
  const allEdges = useMemo(() => graph.data?.edges ?? EMPTY_EDGES, [graph.data]);
  const nodeById = useMemo(() => new Map(allNodes.map((n) => [n.id, n])), [allNodes]);
  const visible = useMemo(() => computeVisible(allNodes, allEdges, filters), [allNodes, allEdges, filters]);
  const visibleIds = useMemo(() => new Set(visible.nodes.map((n) => n.id)), [visible]);

  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of allNodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [allNodes]);

  const matchIds = useMemo(() => {
    const q = normalizeText(filters.query.trim());
    if (!q) return null;
    return new Set(
      visible.nodes
        .filter(
          (n) =>
            normalizeText(n.label).includes(q) ||
            normalizeText(entityTypeLabel(n.type)).includes(q) ||
            normalizeText(JSON.stringify(n.props ?? {})).includes(q),
        )
        .map((n) => n.id),
    );
  }, [visible, filters.query]);

  const incidentEdges = useMemo(() => {
    const map = new Map<number, GraphEdge[]>();
    for (const edge of visible.edges) {
      if (!map.has(edge.source)) map.set(edge.source, []);
      if (!map.has(edge.target)) map.set(edge.target, []);
      map.get(edge.source)?.push(edge);
      map.get(edge.target)?.push(edge);
    }
    return map;
  }, [visible.edges]);

  const degree = (id: number) => incidentEdges.get(id)?.length ?? 0;

  // ------------------------------------------------------------ Cytoscape: creación una vez

  useEffect(() => {
    if (!hostRef.current) return;
    const cy = cytoscape({
      container: hostRef.current,
      style: GRAPH_STYLE,
      minZoom: 0.15,
      maxZoom: 3.5,
      boxSelectionEnabled: false,
    });
    cyRef.current = cy;
    cy.on("tap", "node", (event) => setSelection({ kind: "node", id: Number(event.target.id()) }));
    cy.on("tap", "edge", (event) => setSelection({ kind: "edge", id: Number(String(event.target.id()).slice(1)) }));
    cy.on("tap", (event) => {
      if (event.target === cy) setSelection(null);
    });
    cy.on("dragfree", "node", (event) => {
      positionsRef.current.set(Number(event.target.id()), { ...event.target.position() });
    });
    // El lienzo cambia de tamaño con el diseño de la página: Cytoscape tiene que enterarse para encuadrar bien.
    const host = hostRef.current;
    const observer = new ResizeObserver(() => cy.resize());
    observer.observe(host);
    return () => {
      observer.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // ------------------------------------------------------------ Cytoscape: elementos y layout

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const positions = positionsRef.current;
    const anyKnown = visible.nodes.some((n) => positions.has(n.id));

    // Nodos nuevos aparecen cerca del centro de los conocidos, para que el layout no los tire lejos.
    let cx = 0;
    let cyY = 0;
    let known = 0;
    for (const node of visible.nodes) {
      const p = positions.get(node.id);
      if (p) {
        cx += p.x;
        cyY += p.y;
        known += 1;
      }
    }
    const center = known ? { x: cx / known, y: cyY / known } : { x: 0, y: 0 };

    const nodeElements: cytoscape.ElementDefinition[] = visible.nodes.map((node) => {
      const p = positions.get(node.id) ?? {
        x: center.x + (Math.random() - 0.5) * 240,
        y: center.y + (Math.random() - 0.5) * 240,
      };
      return {
        group: "nodes",
        data: {
          id: String(node.id),
          label: node.label,
          short: shortLabel(node.label),
          type: node.type,
          status: node.status,
          confidence: node.confidence,
        },
        position: { x: p.x, y: p.y },
      };
    });
    const edgeElements: cytoscape.ElementDefinition[] = visible.edges.map((edge) => ({
      group: "edges",
      data: {
        id: `e${edge.id}`,
        source: String(edge.source),
        target: String(edge.target),
        type: edge.type,
        status: edge.status,
        relLabel: relationLabel(edge.type),
      },
    }));

    cy.batch(() => {
      cy.elements().remove();
      cy.add([...nodeElements, ...edgeElements]);
    });

    if (visible.nodes.length === 0) return;

    // Los nodos con relaciones se acomodan con fuerzas; los aislados (cuentas o eventos sueltos)
    // van en filas compactas debajo, para que no estiren el grafo en vertical.
    const isolated = cy.nodes().filter((node) => node.connectedEdges().length === 0);
    const connected = cy.elements().not(isolated);
    const placeIsolated = () => {
      const bb = connected.nodes().nonempty() ? connected.nodes().boundingBox() : { x1: 0, y2: 0 };
      const cols = 5;
      const pending = isolated.filter((node) => !positions.has(Number(node.id())));
      pending.forEach((node, index) => {
        node.position({
          x: bb.x1 + (index % cols) * 150,
          y: bb.y2 + 84 + Math.floor(index / cols) * 92,
        });
      });
    };
    const saveAll = () => {
      cy.nodes().forEach((node) => {
        positions.set(Number(node.id()), { ...node.position() });
      });
    };

    if (connected.nodes().length === 0) {
      placeIsolated();
      saveAll();
      cy.fit(undefined, 30);
      return;
    }

    // Separación generosa: las etiquetas de los nodos no deben pisarse entre sí.
    const layout = connected.layout({
      name: "cose",
      animate: false,
      randomize: !anyKnown,
      fit: false,
      padding: 48,
      nodeRepulsion: () => 22000,
      nodeOverlap: 36,
      idealEdgeLength: () => 110,
      edgeElasticity: () => 45,
      gravity: 0.3,
      numIter: 1000,
    } as cytoscape.LayoutOptions);
    layout.one("layoutstop", () => {
      placeIsolated();
      saveAll();
      cy.fit(undefined, 30);
    });
    layout.run();
  }, [visible]);

  // ------------------------------------------------------------ Cytoscape: resaltados

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("sel path dim");
      if (matchIds) {
        cy.nodes().forEach((node) => {
          if (!matchIds.has(Number(node.id()))) node.addClass("dim");
        });
        cy.edges().forEach((edge) => {
          const a = matchIds.has(Number(edge.source().id()));
          const b = matchIds.has(Number(edge.target().id()));
          if (!(a && b)) edge.addClass("dim");
        });
      }
      if (selection?.kind === "node") {
        const node = cy.getElementById(String(selection.id));
        if (node.nonempty()) {
          node.addClass("sel");
          node.connectedEdges().addClass("sel");
        }
      } else if (selection?.kind === "edge") {
        const edge = cy.getElementById(`e${selection.id}`);
        if (edge.nonempty()) edge.addClass("sel");
      }
      if (pathResult?.found) {
        for (const node of pathResult.nodes) cy.getElementById(String(node.id)).addClass("path");
        for (const edge of pathResult.edges) cy.getElementById(`e${edge.id}`).addClass("path");
      }
    });
  }, [selection, matchIds, pathResult, visible]);

  // Selección inicial desde ?nodo=ID (p. ej. desde el expediente).
  useEffect(() => {
    const wanted = pendingNodeRef.current;
    if (wanted && nodeById.has(wanted)) {
      pendingNodeRef.current = null;
      setSelection({ kind: "node", id: wanted });
      const cy = cyRef.current;
      window.setTimeout(() => {
        const node = cy?.getElementById(String(wanted));
        if (cy && node && node.nonempty()) cy.animate({ center: { eles: node }, zoom: 1.2 }, { duration: 260 });
      }, 300);
    }
  }, [nodeById]);

  // Si la selección desaparece del grafo (p. ej. tras borrar), se limpia.
  useEffect(() => {
    if (selection?.kind === "node" && !nodeById.has(selection.id)) setSelection(null);
  }, [nodeById, selection]);

  // ------------------------------------------------------------ acciones

  function afterMutation(message?: string) {
    if (message) toast.notify(message, "ok");
    setActionError(null);
    graph.reload();
    refreshCase();
  }

  async function review(node: GraphNode, decision: "accept" | "reject") {
    setActionError(null);
    try {
      await endpoints.reviewEntity(caseId, node.id, decision);
      afterMutation(decision === "accept" ? `«${shortLabel(node.label)}» confirmada.` : `«${shortLabel(node.label)}» rechazada.`);
    } catch (err) {
      setActionError(err);
    }
  }

  async function reviewEdge(edge: GraphEdge, decision: "accept" | "reject") {
    setActionError(null);
    try {
      await endpoints.reviewRelation(caseId, edge.id, decision);
      afterMutation(decision === "accept" ? "Relación confirmada." : "Relación rechazada.");
    } catch (err) {
      setActionError(err);
    }
  }

  function fitGraph() {
    cyRef.current?.fit(undefined, 40);
  }

  function reorder() {
    positionsRef.current.clear();
    setFilters((current) => ({ ...current }));
  }

  function centerOn(id: number) {
    const cy = cyRef.current;
    const node = cy?.getElementById(String(id));
    if (cy && node && node.nonempty()) {
      setSelection({ kind: "node", id });
      cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: 240 });
    }
  }

  function onSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" && matchIds && matchIds.size > 0) {
      event.preventDefault();
      const first = visible.nodes.find((n) => matchIds.has(n.id));
      if (first) centerOn(first.id);
    }
    if (event.key === "Escape") setFilters((f) => ({ ...f, query: "" }));
  }

  async function runPath() {
    if (pathFrom === null || pathTo === null) return;
    setPathBusy(true);
    setPathError(null);
    try {
      const result = await endpoints.getPath(caseId, pathFrom, pathTo, ["proposed", "confirmed"]);
      setPathResult(result);
    } catch (err) {
      setPathError(err);
      setPathResult(null);
    } finally {
      setPathBusy(false);
    }
  }

  function clearPath() {
    setPathFrom(null);
    setPathTo(null);
    setPathResult(null);
    setPathError(null);
  }

  function zoomBy(factor: number) {
    const cy = cyRef.current;
    if (!cy) return;
    const host = hostRef.current;
    cy.zoom({
      level: cy.zoom() * factor,
      renderedPosition: { x: (host?.clientWidth ?? 0) / 2, y: (host?.clientHeight ?? 0) / 2 },
    });
  }

  const selectedNode = selection?.kind === "node" ? nodeById.get(selection.id) : undefined;
  const selectedEdge = selection?.kind === "edge" ? allEdges.find((e) => e.id === selection.id) : undefined;
  const hasNodes = allNodes.length > 0;

  const proposedCount = allNodes.filter((n) => n.status === "proposed").length;
  const hypothesisCount = allEdges.filter((e) => e.type === "same_operator").length;

  return (
    <div className="graph-layout">
      {/* -------------------------------------------------- filtros */}
      <aside className="graph-side" aria-label="Filtros del grafo">
        <div className="side-block">
          <label className="field-label" htmlFor="graph-search">
            Buscar en el grafo
          </label>
          <div className="search-box">
            <Icon name="search" />
            <input
              id="graph-search"
              className="input"
              type="search"
              placeholder="Etiqueta, tipo o dato"
              value={filters.query}
              onChange={(event) => setFilters((f) => ({ ...f, query: event.target.value }))}
              onKeyDown={onSearchKey}
            />
          </div>
          {matchIds ? (
            <p className="field-hint">
              {matchIds.size === 0 ? "Sin coincidencias." : `${fmtInt(matchIds.size)} coincidencias. Enter centra la primera.`}
            </p>
          ) : null}
        </div>

        <div className="side-block">
          <div className="side-head">
            <span className="side-title">Tipos</span>
            <span className="side-links">
              <button type="button" className="link-btn" onClick={() => setFilters((f) => ({ ...f, hiddenTypes: [] }))}>
                todos
              </button>
              <button
                type="button"
                className="link-btn"
                onClick={() => setFilters((f) => ({ ...f, hiddenTypes: typeCounts.map(([type]) => type) }))}
              >
                ninguno
              </button>
            </span>
          </div>
          {typeCounts.length === 0 ? <p className="dim small">Sin entidades todavía.</p> : null}
          <ul className="type-list">
            {typeCounts.map(([type, count]) => {
              const style = typeStyle(type);
              const checked = !filters.hiddenTypes.includes(type);
              return (
                <li key={type}>
                  <label className="type-row">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setFilters((f) => ({
                          ...f,
                          hiddenTypes: checked ? [...f.hiddenTypes, type] : f.hiddenTypes.filter((t) => t !== type),
                        }))
                      }
                    />
                    <span className="type-dot" style={{ background: style.color }} aria-hidden="true" />
                    <span className="type-name">{entityTypeLabel(type)}</span>
                    <span className="type-code mono">{style.code}</span>
                    <span className="type-count mono">{count}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="side-block">
          <span className="side-title">Estado</span>
          <label className="check-row">
            <input type="checkbox" checked={filters.showProposed} onChange={(e) => setFilters((f) => ({ ...f, showProposed: e.target.checked }))} />
            Propuestas <span className="dim mono">({proposedCount})</span>
          </label>
          <label className="check-row">
            <input type="checkbox" checked={filters.showConfirmed} onChange={(e) => setFilters((f) => ({ ...f, showConfirmed: e.target.checked }))} />
            Confirmadas
          </label>
          <label className="check-row">
            <input type="checkbox" checked={filters.showRejected} onChange={(e) => setFilters((f) => ({ ...f, showRejected: e.target.checked }))} />
            Rechazadas
          </label>
          <label className="check-row">
            <input type="checkbox" checked={filters.hideIsolated} onChange={(e) => setFilters((f) => ({ ...f, hideIsolated: e.target.checked }))} />
            Ocultar nodos sin relaciones
          </label>
        </div>

        <div className="side-block">
          <label className="field-label" htmlFor="graph-conf">
            Confianza mínima: <span className="mono">{filters.minConfidence}%</span>
          </label>
          <input
            id="graph-conf"
            className="range"
            type="range"
            min={0}
            max={100}
            step={5}
            value={filters.minConfidence}
            onChange={(event) => setFilters((f) => ({ ...f, minConfidence: Number(event.target.value) }))}
          />
        </div>

        <div className="side-block legend-block" aria-label="Leyenda">
          <span className="side-title">Leyenda</span>
          <ul className="legend-list">
            <li>
              <span className="lg-node" /> Nodo confirmado
            </li>
            <li>
              <span className="lg-node lg-proposed" /> Nodo propuesto (revisar)
            </li>
            <li>
              <span className="lg-node lg-rejected" /> Nodo rechazado (oculto por defecto)
            </li>
            <li>
              <span className="lg-edge" /> Relación confirmada
            </li>
            <li>
              <span className="lg-edge lg-edge-dash" /> Relación propuesta
            </li>
            <li>
              <span className="lg-edge lg-edge-hyp" /> Hipótesis de mismo operador (MENARD)
            </li>
          </ul>
        </div>
      </aside>

      {/* -------------------------------------------------- lienzo */}
      <section className="graph-main" aria-label="Grafo del caso">
        <div className="graph-toolbar">
          <div className="graph-stats mono">
            <span>{fmtInt(visible.nodes.length)} nodos</span>
            <span>{fmtInt(visible.edges.length)} relaciones</span>
            {hypothesisCount ? <span className="hot">{hypothesisCount} hipótesis MENARD</span> : null}
            {graph.data?.truncated ? <span className="hot">Límite alcanzado: el grafo está truncado</span> : null}
          </div>
          <div className="graph-tools">
            <Btn size="sm" icon="refresh" onClick={reorder} title="Recalcular la disposición">
              Reordenar
            </Btn>
            <IconButton icon="search" label="Ajustar a la pantalla" onClick={fitGraph} />
            <IconButton icon="plus" label="Acercar" onClick={() => zoomBy(1.2)} />
            <IconButton icon="x" label="Alejar" onClick={() => zoomBy(1 / 1.2)} />
            <span className="tool-sep" />
            <Btn size="sm" icon="plus" variant="primary" disabled={!writable} title={writable ? undefined : "Solo con el caso abierto y un rol de escritura"} onClick={() => setDialog({ kind: "new-entity" })}>
              Nueva entidad
            </Btn>
          </div>
        </div>

        {pathFrom !== null || pathTo !== null ? (
          <div className="path-bar" role="region" aria-label="Camino más corto">
            <span className="path-label">Camino</span>
            <PathEnd label="Origen" id={pathFrom} nodeById={nodeById} onClear={() => setPathFrom(null)} />
            <span className="dim">→</span>
            <PathEnd label="Destino" id={pathTo} nodeById={nodeById} onClear={() => setPathTo(null)} />
            <Btn size="sm" variant="primary" disabled={pathFrom === null || pathTo === null || pathFrom === pathTo || pathBusy} onClick={runPath}>
              {pathBusy ? "Buscando…" : "Buscar camino más corto"}
            </Btn>
            <Btn size="sm" variant="ghost" onClick={clearPath}>
              Limpiar
            </Btn>
            <span className="path-result" role="status">
              {pathError ? <span className="inline-error">{errorMessage(pathError)}</span> : null}
              {pathResult && !pathResult.found ? "No hay camino entre estas entidades con lo que está visible." : null}
              {pathResult?.found ? `Camino de ${pathResult.length ?? 0} saltos, resaltado en el grafo.` : null}
            </span>
          </div>
        ) : null}

        <div className="cy-frame">
          <div ref={hostRef} className="cy-host" data-testid="graph-canvas" />
          {graph.loading && !graph.data ? (
            <div className="cy-overlay">
              <Loading label="Cargando el grafo…" rows={2} />
            </div>
          ) : null}
          {graph.error ? (
            <div className="cy-overlay">
              <ErrorBox error={graph.error} onRetry={graph.reload} title="No se pudo cargar el grafo" />
            </div>
          ) : null}
          {graph.data && !hasNodes ? (
            <div className="cy-overlay">
              <Empty
                title="El grafo está vacío"
                action={
                  writable ? (
                    <Btn variant="primary" icon="plus" onClick={() => setDialog({ kind: "new-entity" })}>
                      Crear la primera entidad
                    </Btn>
                  ) : undefined
                }
              >
                Creá entidades a mano, incorporá hallazgos desde el expediente o confirmá vínculos de MENARD.
              </Empty>
            </div>
          ) : null}
          {graph.data && hasNodes && visible.nodes.length === 0 ? (
            <div className="cy-overlay cy-overlay-soft">
              <Empty title="Ningún nodo pasa los filtros">Ajustá el estado, los tipos o la confianza mínima en la columna de la izquierda.</Empty>
            </div>
          ) : null}
        </div>
        <p className="graph-foot dim small">
          Las hipótesis de MENARD son sugerencias con evidencia para revisión humana. Nada entra al grafo como identidad confirmada sin una decisión del analista.
        </p>
      </section>

      {/* -------------------------------------------------- inspector */}
      <aside className="graph-inspector" aria-label="Inspector">
        {actionError ? (
          <div className="inline-error inspector-error" role="alert">
            {errorMessage(actionError)}
          </div>
        ) : null}

        {selectedNode ? (
          <NodeInspector
            key={selectedNode.id}
            caseId={caseId}
            node={selectedNode}
            edges={incidentEdges.get(selectedNode.id) ?? []}
            nodeById={nodeById}
            visibleIds={visibleIds}
            degree={degree(selectedNode.id)}
            writable={writable}
            onSelect={(id) => setSelection({ kind: "node", id })}
            onReview={(decision) => review(selectedNode, decision)}
            onEdit={() => setDialog({ kind: "edit-entity", node: selectedNode })}
            onMerge={() => setDialog({ kind: "merge", node: selectedNode })}
            onRelate={() => setDialog({ kind: "new-relation", node: selectedNode })}
            onDelete={() => setDialog({ kind: "delete-entity", node: selectedNode })}
            onPathPick={(which) => {
              if (which === "from") setPathFrom(selectedNode.id);
              else setPathTo(selectedNode.id);
            }}
            onClear={() => setSelection(null)}
          />
        ) : selectedEdge ? (
          <EdgeInspector
            key={selectedEdge.id}
            caseId={caseId}
            edge={selectedEdge}
            nodeById={nodeById}
            writable={writable}
            onReview={(decision) => reviewEdge(selectedEdge, decision)}
            onDelete={() => setDialog({ kind: "delete-relation", edge: selectedEdge })}
            onSelectNode={(id) => setSelection({ kind: "node", id })}
            onClear={() => setSelection(null)}
          />
        ) : (
          <Overview nodes={allNodes} edges={allEdges} proposed={proposedCount} hypotheses={hypothesisCount} />
        )}
      </aside>

      {/* -------------------------------------------------- diálogos */}
      {dialog?.kind === "new-entity" ? (
        <EntityDialog
          caseId={caseId}
          onClose={() => setDialog(null)}
          onDone={(saved) => {
            setDialog(null);
            afterMutation(`Entidad «${shortLabel(saved.label)}» creada.`);
            pendingNodeRef.current = saved.id;
          }}
        />
      ) : null}
      {dialog?.kind === "edit-entity" ? (
        <EntityDialog
          caseId={caseId}
          entity={dialog.node}
          onClose={() => setDialog(null)}
          onDone={(saved) => {
            setDialog(null);
            afterMutation(`Entidad «${shortLabel(saved.label)}» actualizada.`);
          }}
        />
      ) : null}
      {dialog?.kind === "merge" ? (
        <MergeDialog
          caseId={caseId}
          current={dialog.node}
          candidates={allNodes.filter((n) => n.type === dialog.node.type && n.id !== dialog.node.id)}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            setSelection(null);
            afterMutation("Entidades fusionadas.");
          }}
        />
      ) : null}
      {dialog?.kind === "new-relation" ? (
        <RelationDialog
          caseId={caseId}
          source={dialog.node}
          targets={allNodes.filter((n) => n.id !== dialog.node.id)}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            afterMutation("Relación creada.");
          }}
        />
      ) : null}
      {dialog?.kind === "delete-entity" ? (
        <ConfirmDialog
          title="Borrar la entidad"
          confirmLabel="Borrar entidad"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await endpoints.deleteEntity(caseId, dialog.node.id);
            setDialog(null);
            setSelection(null);
            afterMutation("Entidad borrada, junto con sus relaciones.");
          }}
        >
          <p>
            Se borra «{dialog.node.label}» y todas sus relaciones. La API rechaza el borrado si la entidad representa una
            cuenta recolectada.
          </p>
        </ConfirmDialog>
      ) : null}
      {dialog?.kind === "delete-relation" ? (
        <ConfirmDialog
          title="Borrar la relación"
          confirmLabel="Borrar relación"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await endpoints.deleteRelation(caseId, dialog.edge.id);
            setDialog(null);
            setSelection(null);
            afterMutation("Relación borrada.");
          }}
        >
          <p>Se borra la relación «{relationLabel(dialog.edge.type)}». Las entidades que une se conservan.</p>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- piezas del panel

function PathEnd({
  label,
  id,
  nodeById,
  onClear,
}: {
  label: string;
  id: number | null;
  nodeById: Map<number, GraphNode>;
  onClear: () => void;
}) {
  const node = id === null ? undefined : nodeById.get(id);
  return (
    <span className="path-end">
      <span className="dim small">{label}:</span>{" "}
      {node ? (
        <span className="text">
          #{node.id} · {truncate(node.label, 26)}
        </span>
      ) : (
        <span className="dim">elegí un nodo</span>
      )}
      {id !== null ? (
        <button type="button" className="link-btn" onClick={onClear} aria-label={`Quitar ${label.toLowerCase()}`}>
          ×
        </button>
      ) : null}
    </span>
  );
}

function Overview({
  nodes,
  edges,
  proposed,
  hypotheses,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  proposed: number;
  hypotheses: number;
}) {
  return (
    <div className="inspector-body">
      <h3 className="inspector-title">Resumen del grafo</h3>
      <dl className="kv">
        <dt>Entidades</dt>
        <dd className="mono">{fmtInt(nodes.length)}</dd>
        <dt>Relaciones</dt>
        <dd className="mono">{fmtInt(edges.length)}</dd>
        <dt>Pendientes de revisión</dt>
        <dd className="mono">
          <span className="hot">{proposed}</span> entidades
        </dd>
        <dt>Hipótesis MENARD</dt>
        <dd className="mono">{hypotheses}</dd>
      </dl>
      <p className="dim small">
        Elegí un nodo o una relación para ver su procedencia y sus acciones. Para buscar un camino, elegí dos nodos y
        usá «Usar como origen» y «Usar como destino».
      </p>
    </div>
  );
}

function NodeInspector({
  caseId,
  node,
  edges,
  nodeById,
  visibleIds,
  degree,
  writable,
  onSelect,
  onReview,
  onEdit,
  onMerge,
  onRelate,
  onDelete,
  onPathPick,
  onClear,
}: {
  caseId: number;
  node: GraphNode;
  edges: GraphEdge[];
  nodeById: Map<number, GraphNode>;
  visibleIds: Set<number>;
  degree: number;
  writable: boolean;
  onSelect: (id: number) => void;
  onReview: (decision: "accept" | "reject") => void;
  onEdit: () => void;
  onMerge: () => void;
  onRelate: () => void;
  onDelete: () => void;
  onPathPick: (which: "from" | "to") => void;
  onClear: () => void;
}) {
  const style = typeStyle(node.type);
  const source = useAsync<SourceOut | null>(
    () => (node.source_id ? endpoints.getSource(caseId, node.source_id) : Promise.resolve(null)),
    [caseId, node.source_id],
  );
  const findings = useAsync(() => endpoints.listFindings(caseId, { entity_id: node.id, limit: 10 }), [caseId, node.id]);
  const neighbors = edges
    .map((edge) => {
      const otherId = edge.source === node.id ? edge.target : edge.source;
      const other = nodeById.get(otherId);
      return other && visibleIds.has(otherId) ? { edge, other, outgoing: edge.source === node.id } : null;
    })
    .filter((item): item is { edge: GraphEdge; other: GraphNode; outgoing: boolean } => item !== null);
  const isProposed = node.status === "proposed";
  const isRejected = node.status === "rejected";
  const editable = writable;
  const writeHint = writable ? undefined : "Solo con el caso abierto y un rol de escritura";
  const propEntries = Object.entries(node.props ?? {});

  return (
    <div className="inspector-body">
      <div className="inspector-head">
        <span className="type-chip" style={{ borderColor: style.color, color: style.color }}>
          {style.code}
        </span>
        <span className="dim small">{entityTypeLabel(node.type)}</span>
        <StatusBadge status={node.status} />
        <IconButton icon="x" label="Quitar la selección" onClick={onClear} className="inspector-close" />
      </div>
      <h3 className="inspector-title">{node.label}</h3>
      <p className="mono dim small">entidad #{node.id}</p>

      <dl className="kv">
        <dt>Confianza</dt>
        <dd>
          <Bar value={node.confidence} tone={node.confidence >= 0.8 ? "ok" : "amber"} label="Confianza" />{" "}
          <span className="mono">{pct(node.confidence)}</span>
        </dd>
        <dt>Relaciones</dt>
        <dd className="mono">{degree}</dd>
      </dl>

      <h4 className="block-title">Procedencia</h4>
      {node.source_id === null ? (
        <p className="dim small">Esta entidad no tiene una fuente registrada.</p>
      ) : source.loading && !source.data ? (
        <Loading label="Leyendo la fuente…" rows={1} />
      ) : source.error ? (
        <p className="inline-error">{errorMessage(source.error)}</p>
      ) : source.data ? (
        <div className="provenance">
          <div className="provenance-row">
            <Badge tone="neutral">{SOURCE_KIND_LABEL[source.data.kind] ?? source.data.kind}</Badge>
            <Admiralty code={source.data.admiralty} />
            <span className="mono dim small">{shortHashFor(source.data.sha256)}</span>
          </div>
          <p className="provenance-ref" title={source.data.reference}>
            {truncate(source.data.reference || "(sin referencia)", 120)}
          </p>
          <p className="dim small mono">recuperada {fmtDateTime(source.data.retrieved_at)} · fuente #{source.data.id}</p>
        </div>
      ) : null}

      {propEntries.length > 0 ? (
        <>
          <h4 className="block-title">Datos</h4>
          <dl className="kv kv-compact">
            {propEntries.map(([key, value]) => (
              <div key={key} className="kv-row">
                <dt>{key}</dt>
                <dd className="mono" title={propText(value)}>
                  {truncate(propText(value), 80)}
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : null}

      <h4 className="block-title">Vecinos ({neighbors.length})</h4>
      {neighbors.length === 0 ? (
        <p className="dim small">Sin relaciones visibles con los filtros actuales.</p>
      ) : (
        <ul className="neighbor-list">
          {neighbors.map(({ edge, other, outgoing }) => (
            <li key={edge.id}>
              <button type="button" className="neighbor-btn" onClick={() => onSelect(other.id)}>
                <span className="mono dim small">{outgoing ? "→" : "←"}</span>
                <span className="neighbor-rel">{relationLabel(edge.type)}</span>
                <span className="neighbor-label">{truncate(other.label, 34)}</span>
                <StatusBadge status={edge.status} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <h4 className="block-title">Hallazgos en el expediente</h4>
      {findings.loading && !findings.data ? <Loading label="Buscando hallazgos…" rows={1} /> : null}
      {findings.data && findings.data.items.length === 0 ? (
        <p className="dim small">Ningún hallazgo del expediente apunta a esta entidad.</p>
      ) : null}
      {findings.data && findings.data.items.length > 0 ? (
        <ul className="finding-mini">
          {findings.data.items.slice(0, 6).map((finding: FindingOut) => (
            <li key={finding.id}>
              <span className="mono small">{truncate(finding.value, 60)}</span>
              <span className="dim small">{truncate(String(finding.chunk?.page_title ?? finding.chunk?.page_url ?? ""), 60)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {findings.data && findings.data.items.length > 0 ? (
        <Btn size="sm" variant="ghost" onClick={() => navigate(`/casos/${caseId}/expediente`)}>
          Ver en el expediente
        </Btn>
      ) : null}

      <h4 className="block-title">Acciones</h4>
      <div className="action-grid">
        {isProposed ? (
          <>
            <Btn size="sm" variant="ok" icon="check" disabled={!editable} title={writeHint} onClick={() => onReview("accept")}>
              Confirmar
            </Btn>
            <Btn size="sm" variant="danger" icon="ban" disabled={!editable} title={writeHint} onClick={() => onReview("reject")}>
              Rechazar
            </Btn>
          </>
        ) : null}
        {isRejected ? <span className="dim small">Rechazada: no entra en el análisis. Se puede volver a proponer desde el expediente.</span> : null}
        <Btn size="sm" icon="edit" disabled={!editable} title={writeHint} onClick={onEdit}>
          Editar
        </Btn>
        <Btn size="sm" icon="merge" disabled={!editable} title={writeHint} onClick={onMerge}>
          Fusionar…
        </Btn>
        <Btn size="sm" icon="link" disabled={!editable} title={writeHint} onClick={onRelate}>
          Relacionar…
        </Btn>
        <Btn size="sm" icon="path" onClick={() => onPathPick("from")}>
          Usar como origen
        </Btn>
        <Btn size="sm" icon="path" onClick={() => onPathPick("to")}>
          Usar como destino
        </Btn>
        <Btn size="sm" variant="danger" icon="trash" disabled={!editable} title={writeHint} onClick={onDelete}>
          Borrar
        </Btn>
      </div>
    </div>
  );
}

function shortHashFor(sha: string): string {
  return sha ? `sha256 ${sha.slice(0, 12)}…` : "sin hash";
}

function EdgeInspector({
  caseId,
  edge,
  nodeById,
  writable,
  onReview,
  onDelete,
  onSelectNode,
  onClear,
}: {
  caseId: number;
  edge: GraphEdge;
  nodeById: Map<number, GraphNode>;
  writable: boolean;
  onReview: (decision: "accept" | "reject") => void;
  onDelete: () => void;
  onSelectNode: (id: number) => void;
  onClear: () => void;
}) {
  const src = nodeById.get(edge.source);
  const dst = nodeById.get(edge.target);
  const source = useAsync<SourceOut | null>(
    () => (edge.source_id ? endpoints.getSource(caseId, edge.source_id) : Promise.resolve(null)),
    [caseId, edge.source_id],
  );
  const writeHint = writable ? undefined : "Solo con el caso abierto y un rol de escritura";
  const hypothesis = edge.type === "same_operator";
  const meta = (edge.props ?? {}) as Record<string, unknown>;

  return (
    <div className="inspector-body">
      <div className="inspector-head">
        <Badge tone={hypothesis ? "amber" : "neutral"} dashed={hypothesis && edge.status === "proposed"}>
          {hypothesis ? "Hipótesis MENARD" : "Relación"}
        </Badge>
        <StatusBadge status={edge.status} />
        <IconButton icon="x" label="Quitar la selección" onClick={onClear} className="inspector-close" />
      </div>
      <h3 className="inspector-title">{relationLabel(edge.type)}</h3>
      <p className="mono dim small">relación #{edge.id}</p>

      <div className="edge-ends">
        <button type="button" className="neighbor-btn" onClick={() => src && onSelectNode(src.id)} disabled={!src}>
          {src ? truncate(src.label, 36) : `#${edge.source}`}
        </button>
        <span className="dim">→</span>
        <button type="button" className="neighbor-btn" onClick={() => dst && onSelectNode(dst.id)} disabled={!dst}>
          {dst ? truncate(dst.label, 36) : `#${edge.target}`}
        </button>
      </div>

      <dl className="kv">
        <dt>Confianza</dt>
        <dd>
          <Bar value={edge.confidence} tone="amber" label="Confianza de la relación" /> <span className="mono">{pct(edge.confidence)}</span>
        </dd>
        <dt>Tipo</dt>
        <dd className="mono">{edge.type}</dd>
        <dt>Estado</dt>
        <dd>{ITEM_STATUS_LABEL[edge.status] ?? edge.status}</dd>
      </dl>

      {hypothesis ? (
        <p className="notice notice-amber small">
          Hipótesis de mismo operador. Sale de MENARD y no es una identificación: confirmala solo si la evidencia lo sostiene.
          {typeof meta.review_note === "string" && meta.review_note ? ` Nota de revisión: ${meta.review_note}` : ""}
        </p>
      ) : null}

      <h4 className="block-title">Procedencia</h4>
      {edge.source_id === null ? (
        <p className="dim small">Sin fuente registrada.</p>
      ) : source.data ? (
        <div className="provenance">
          <div className="provenance-row">
            <Badge tone="neutral">{SOURCE_KIND_LABEL[source.data.kind] ?? source.data.kind}</Badge>
            <Admiralty code={source.data.admiralty} />
          </div>
          <p className="provenance-ref">{truncate(source.data.reference || "(sin referencia)", 120)}</p>
        </div>
      ) : source.loading ? (
        <Loading label="Leyendo la fuente…" rows={1} />
      ) : null}

      <h4 className="block-title">Acciones</h4>
      <div className="action-grid">
        {edge.status === "proposed" ? (
          <>
            <Btn size="sm" variant="ok" icon="check" disabled={!writable} title={writeHint} onClick={() => onReview("accept")}>
              Confirmar
            </Btn>
            <Btn size="sm" variant="danger" icon="ban" disabled={!writable} title={writeHint} onClick={() => onReview("reject")}>
              Rechazar
            </Btn>
          </>
        ) : null}
        <Btn size="sm" variant="danger" icon="trash" disabled={!writable} title={writeHint} onClick={onDelete}>
          Borrar relación
        </Btn>
      </div>
    </div>
  );
}
