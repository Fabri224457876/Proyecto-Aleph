import { useEffect, useRef } from "react";
import type { GraphOut } from "../api/types";
import { relationLabel } from "../lib/labels";

interface Props {
  graph: GraphOut;
  selectedId: number | null;
  pathIds: number[];
  pathEdgeIds: number[];
  onSelect: (id: number) => void;
}

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
          ...graph.nodes.map((n) => ({
            data: { id: String(n.id), label: n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label, status: n.status },
          })),
          ...graph.edges.map((e) => ({
            data: { id: `e${e.id}`, source: String(e.source), target: String(e.target), label: relationLabel(e.type), status: e.status },
          })),
        ],
        layout: { name: "cose", animate: false, padding: 24, nodeRepulsion: () => 9000 },
        minZoom: 0.3,
        maxZoom: 2.5,
        wheelSensitivity: 0.3,
      });
      cyRef.current = cy;

      const applyStyle = () => {
        const accent = cssVar("--accent", "#1f5fa8");
        const text = cssVar("--text", "#17202b");
        const muted = cssVar("--muted", "#535e6c");
        const warn = cssVar("--warn", "#8a5300");
        const surface = cssVar("--surface", "#ffffff");
        const focus = cssVar("--focus", "#0b6bcb");
        cy.style([
          {
            selector: "node",
            style: {
              "background-color": accent,
              label: "data(label)",
              color: text,
              "font-size": 11,
              "text-valign": "bottom",
              "text-margin-y": 4,
              "text-wrap": "ellipsis",
              "text-max-width": "120px",
              width: 22,
              height: 22,
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
              "font-size": 9,
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
  }, [graph]);

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
    </div>
  );
}
