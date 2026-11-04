import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphOut } from "../api/types";
import { relationLabel } from "../lib/labels";

interface Props {
  graph: GraphOut;
  selectedId: number | null;
  pathIds: number[];
  pathEdgeIds: number[];
  onSelect: (id: number) => void;
}

// Color por familia de tipo: identidad, contacto, infraestructura, organización y lugar, amenaza, finanzas.
const TYPE_COLOR: Record<string, string> = {
  person: "#3b6fb6", alias: "#3b6fb6",
  account: "#1f8a7a", email: "#1f8a7a", phone: "#1f8a7a",
  domain: "#7357b8", ip: "#7357b8", url: "#7357b8",
  organization: "#a67c1f", location: "#a67c1f", event: "#5f6b78",
  hash: "#6b7785", document: "#6b7785", vehicle: "#6b7785",
  malware: "#c0394b", vulnerability: "#c0394b",
  wallet: "#a3478d", bank_account: "#a3478d",
};

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

// Cytoscape se carga cuando se abre la vista de conexiones (no pesa en la carga inicial de la consola).
export function GraphView({ graph, selectedId, pathIds, pathEdgeIds, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<import("cytoscape").Core | null>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  const [showIsolated, setShowIsolated] = useState(false);
  // Las entidades sin vínculos (cuentas sueltas, sobre todo) se ocultan por defecto: tapan la estructura.
  const linked = useMemo(() => new Set(graph.edges.flatMap((e) => [e.source, e.target])), [graph]);
  const isolated = graph.nodes.length - graph.nodes.filter((n) => linked.has(n.id)).length;
  const nodes = useMemo(
    () => graph.nodes.filter((n) => showIsolated || linked.has(n.id) || n.id === selectedId),
    [graph, linked, showIsolated, selectedId],
  );

  // Dibuja el grafo cuando cambian los datos o el tema.
  useEffect(() => {
    let alive = true;
    let observer: MutationObserver | null = null;
    const theme = () => document.documentElement.dataset.theme ?? "light";
    void import("cytoscape").then(({ default: cytoscape }) => {
      if (!alive || !container.current) return;
      const cy = cytoscape({
        container: container.current,
        elements: [
          ...nodes.map((n) => ({
            data: {
              id: String(n.id),
              label: n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label,
              status: n.status,
              color: TYPE_COLOR[n.type] ?? "#5f6b78",
              size: Math.min(44, 22 + 3 * Math.sqrt(n.degree ?? 0)),
            },
          })),
          ...graph.edges.filter((e) => nodes.some((n) => n.id === e.source) && nodes.some((n) => n.id === e.target)).map((e) => ({
            data: { id: `e${e.id}`, source: String(e.source), target: String(e.target), label: relationLabel(e.type), status: e.status },
          })),
        ],
        // Separación generosa para que las etiquetas no se pisen.
        layout: {
          name: "cose", animate: false, padding: 32, nodeRepulsion: () => 26000, nodeOverlap: 40,
          idealEdgeLength: () => 95, edgeElasticity: () => 60, gravity: 0.4, numIter: 1500,
          componentSpacing: 60,
          // Que ocupe el ancho del recuadro y no una columna angosta.
          boundingBox: { x1: 0, y1: 0, w: container.current.clientWidth * 1.4, h: container.current.clientHeight },
        } as import("cytoscape").LayoutOptions,
        minZoom: 0.3,
        maxZoom: 2.5,
      });
      cyRef.current = cy;

      const applyStyle = () => {
        const text = cssVar("--text", "#17202b");
        const muted = cssVar("--muted", "#535e6c");
        const warn = cssVar("--warn", "#8a5300");
        const surface = cssVar("--surface", "#ffffff");
        const focus = cssVar("--focus", "#0b6bcb");
        cy.style([
          {
            selector: "node",
            style: {
              "background-color": "data(color)",
              label: "data(label)",
              color: text,
              "font-size": 13,
              "min-zoomed-font-size": 7,
              "text-valign": "bottom",
              "text-margin-y": 4,
              "text-wrap": "ellipsis",
              "text-max-width": "120px",
              width: "data(size)",
              height: "data(size)",
              "border-width": 2,
              "border-color": surface,
              "text-background-color": surface,
              "text-background-opacity": 0.85,
              "text-background-padding": "2px",
            },
          },
          { selector: "node[status = 'proposed']", style: { "background-opacity": 0.35, "border-width": 2, "border-style": "dashed", "border-color": warn, "border-opacity": 1 } },
          { selector: "node.selected", style: { "border-width": 4, "border-color": focus, "border-style": "solid" } },
          { selector: "node.path", style: { "background-color": warn, "border-width": 3, "border-color": warn } },
          {
            selector: "edge",
            style: {
              width: 1.6,
              "line-color": muted,
              "target-arrow-color": muted,
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
              label: "data(label)",
              "font-size": 10,
              "min-zoomed-font-size": 9,
              color: muted,
              "text-background-color": surface,
              "text-background-opacity": 0.85,
              "text-background-padding": "1px",
            },
          },
          { selector: "edge[status = 'proposed']", style: { "line-style": "dashed", "line-color": warn, "target-arrow-color": warn } },
          { selector: "edge.path", style: { width: 4, "line-color": warn, "target-arrow-color": warn } },
        ]);
        cy.nodes().forEach((node) => {
          node.toggleClass("selected", node.id() === String(selectedId));
          node.toggleClass("path", pathIds.includes(Number(node.id())));
        });
        cy.edges().forEach((edge) => {
          edge.toggleClass("path", pathEdgeIds.includes(Number(edge.id().slice(1))));
        });
      };
      applyStyle();
      cy.on("tap", "node", (event) => selectRef.current(Number(event.target.id())));

      observer = new MutationObserver(() => {
        if (theme()) applyStyle();
      });
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    });
    return () => {
      alive = false;
      observer?.disconnect();
      cyRef.current?.destroy();
      cyRef.current = null;
    };
    // Los resaltados se aplican en el efecto siguiente; reconstruir por datos y tema alcanza.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, nodes]);

  // Resalta selección y camino sin volver a dibujar.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((node) => {
      node.toggleClass("selected", node.id() === String(selectedId));
      node.toggleClass("path", pathIds.includes(Number(node.id())));
    });
    cy.edges().forEach((edge) => {
          edge.toggleClass("path", pathEdgeIds.includes(Number(edge.id().slice(1))));
        });
  }, [selectedId, pathIds, pathEdgeIds]);

  return (
    <div className="graph-wrap" role="region" aria-label="Mapa de conexiones del caso">
      <div ref={container} className="graph-canvas" />
      {isolated > 0 && (
        <button type="button" className="btn btn-small graph-toggle" onClick={() => setShowIsolated((v) => !v)}>
          {showIsolated ? "Ocultar" : "Mostrar"} {isolated} sin conexiones
        </button>
      )}
    </div>
  );
}
