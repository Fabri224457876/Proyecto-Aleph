import type cytoscape from "cytoscape";
import type { EntityType } from "../../api/types";

// Colores literales: Cytoscape dibuja en canvas y no lee variables CSS.
// Cytoscape acepta un solo nombre de familia por estilo (no listas con comas).
const MONO = "Cascadia Mono";

export const COLOR = {
  bg: "#070b0e",
  panel: "#0c1317",
  line: "#1c2a32",
  text: "#c9d6de",
  dim: "#7f8d98",
  amber: "#ffc247",
  ok: "#5bd69a",
  bad: "#d9707a",
  edge: "#4c6470",
  edgeProposed: "#6b8794",
  edgeRejected: "#6b4550",
};

interface TypeStyle {
  code: string;
  shape: cytoscape.Css.NodeShape;
  color: string;
}

// Forma y color por tipo. Los colores agrupan por familia: identidad, contacto, infraestructura,
// organización y lugar, evidencia, amenaza y finanzas. El ámbar queda reservado para la atención del analista.
export const TYPE_STYLE: Record<EntityType, TypeStyle> = {
  person: { code: "PER", shape: "ellipse", color: "#8fb4d9" },
  alias: { code: "ALI", shape: "rhomboid", color: "#8fb4d9" },
  account: { code: "CTA", shape: "round-rectangle", color: "#7fd1c1" },
  email: { code: "MAIL", shape: "rectangle", color: "#7fd1c1" },
  phone: { code: "TEL", shape: "round-pentagon", color: "#7fd1c1" },
  domain: { code: "DOM", shape: "hexagon", color: "#b9a6e8" },
  ip: { code: "IP", shape: "diamond", color: "#b9a6e8" },
  url: { code: "URL", shape: "round-tag", color: "#b9a6e8" },
  organization: { code: "ORG", shape: "concave-hexagon", color: "#d9c28a" },
  location: { code: "LUG", shape: "triangle", color: "#d9c28a" },
  event: { code: "EVT", shape: "star", color: "#c3ccd4" },
  hash: { code: "HASH", shape: "pentagon", color: "#9aa8b4" },
  document: { code: "DOC", shape: "barrel", color: "#9aa8b4" },
  vehicle: { code: "VEH", shape: "round-heptagon", color: "#9aa8b4" },
  malware: { code: "MAL", shape: "octagon", color: "#e27c86" },
  vulnerability: { code: "VUL", shape: "round-octagon", color: "#e27c86" },
  wallet: { code: "WAL", shape: "heptagon", color: "#d7a7c9" },
  bank_account: { code: "CTB", shape: "tag", color: "#d7a7c9" },
};

export function typeStyle(type: string): TypeStyle {
  return (TYPE_STYLE as Record<string, TypeStyle>)[type] ?? { code: type.slice(0, 4).toUpperCase(), shape: "ellipse", color: "#7f8d98" };
}

/** Texto para buscar en el grafo: sin acentos y en minúsculas. */
export function normalizeText(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export const GRAPH_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: "node",
    style: {
      "background-color": COLOR.panel,
      "background-opacity": 1,
      "border-width": 1.6,
      "border-color": COLOR.dim,
      width: 32,
      height: 32,
      label: "data(short)",
      "font-family": MONO,
      "font-size": 11,
      color: COLOR.text,
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 6,
      "text-wrap": "ellipsis",
      "text-max-width": "150px",
      "text-background-color": COLOR.bg,
      "text-background-opacity": 0.8,
      "text-background-padding": "2px",
      "text-background-shape": "roundrectangle",
      "overlay-opacity": 0,
    },
  },
  ...Object.entries(TYPE_STYLE).map(([type, style]) => ({
    selector: `node[type = "${type}"]`,
    style: {
      shape: style.shape,
      "background-color": style.color,
      "background-opacity": 0.2,
      "border-color": style.color,
    },
  })),
  {
    selector: 'node[status = "proposed"]',
    style: { "border-style": "dashed", "background-opacity": 0.05 },
  },
  {
    selector: 'node[status = "rejected"]',
    style: { "border-style": "dotted", "border-color": COLOR.bad, opacity: 0.4 },
  },
  {
    selector: "node.sel",
    style: { "border-width": 3, "border-color": COLOR.amber, "background-opacity": 0.45, "border-style": "solid" },
  },
  {
    selector: "node.path",
    style: { "border-color": COLOR.amber, "border-width": 2.6, "background-color": COLOR.amber, "background-opacity": 0.25, "border-style": "solid" },
  },
  {
    selector: "edge",
    style: {
      width: 1.2,
      "line-color": COLOR.edge,
      "target-arrow-color": COLOR.edge,
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.8,
      "curve-style": "bezier",
      label: "data(relLabel)",
      "font-family": MONO,
      "font-size": 8,
      color: COLOR.dim,
      "text-rotation": "autorotate",
      "text-background-color": COLOR.bg,
      "text-background-opacity": 0.85,
      "text-background-padding": "1.5px",
      "min-zoomed-font-size": 8,
    },
  },
  {
    selector: 'edge[status = "proposed"]',
    style: {
      "line-style": "dashed",
      "line-dash-pattern": [6, 4],
      "line-color": COLOR.edgeProposed,
      "target-arrow-color": COLOR.edgeProposed,
    },
  },
  {
    selector: 'edge[status = "rejected"]',
    style: {
      "line-style": "dotted",
      "line-color": COLOR.edgeRejected,
      "target-arrow-color": COLOR.edgeRejected,
      opacity: 0.55,
    },
  },
  {
    selector: 'edge[type = "same_operator"]',
    style: {
      "line-color": COLOR.amber,
      "target-arrow-color": COLOR.amber,
      width: 2.6,
      color: COLOR.amber,
      "font-size": 9,
    },
  },
  {
    selector: 'edge[type = "same_operator"][status = "proposed"]',
    style: { "line-style": "dashed", "line-dash-pattern": [8, 4] },
  },
  {
    selector: "edge.sel",
    style: { "line-color": COLOR.text, "target-arrow-color": COLOR.text, width: 2.2 },
  },
  {
    selector: "edge.path",
    style: { "line-color": COLOR.amber, "target-arrow-color": COLOR.amber, width: 3.2, opacity: 1, "line-style": "solid" },
  },
  {
    selector: ".dim",
    style: { opacity: 0.12 },
  },
];
