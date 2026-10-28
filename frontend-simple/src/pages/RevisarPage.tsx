import { useEffect, useMemo, useState } from "react";
import * as ep from "../api/endpoints";
import type { Contradiction } from "../api/contract";
import type { EntityOut, LinkOut, RelationOut, SourceOut } from "../api/types";
import { useCase } from "../components/CaseLayout";
import { Button, Chip, Empty, ErrorBox, Help, Loading, Notice, PageTitle, Panel, type Tone } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";
import { errorMessage, plural, pct } from "../lib/format";
import { entityTypeLabel, relationLabel, ITEM_STATUS_LABEL } from "../lib/labels";
import { navigate, useLocation, Link } from "../lib/router";
import { DecisionView, type QueueItem, type Decision } from "./revisar/DecisionView";

interface Queue {
  links: LinkOut[];
  entities: EntityOut[];
  relations: RelationOut[];
}

async function loadQueue(caseId: number): Promise<Queue> {
  const [links, entities, relations] = await Promise.all([
    ep.listLinks(caseId, { status: "pending", limit: 200 }),
    ep.listEntities(caseId, { status: ["proposed"], limit: 200 }),
    ep.listRelations(caseId, { status: ["proposed"], limit: 200 }),
  ]);
  return { links: links.items, entities: entities.items, relations: relations.items };
}

const SEVERITY_WEIGHT: Record<string, number> = { alta: 100, media: 80, baja: 60 };

/** Importancia: lo que más cambia el caso, primero. Las contradicciones y las identidades pesan más que los datos sueltos. */
function importance(item: QueueItem): number {
  switch (item.kind) {
    case "contradiction":
      return SEVERITY_WEIGHT[item.contradiction.severity] ?? 60;
    case "hypothesis":
      return 50 + item.link.score * 40;
    case "entity":
      return 45;
    case "relation":
      return 35;
  }
}

export function buildQueueItems(queue: Queue, contradictions: Contradiction[], hidden: Set<string>): QueueItem[] {
  const items: QueueItem[] = [
    ...contradictions.map<QueueItem>((c) => ({ kind: "contradiction", key: `c${c.id}`, contradiction: c })),
    ...queue.links.map<QueueItem>((link) => ({ kind: "hypothesis", key: `h${link.id}`, link })),
    ...queue.entities.map<QueueItem>((entity) => ({ kind: "entity", key: `e${entity.id}`, entity })),
    ...queue.relations.map<QueueItem>((relation) => ({ kind: "relation", key: `r${relation.id}`, relation })),
  ];
  return items
    .filter((item) => !hidden.has(item.key))
    .sort((a, b) => importance(b) - importance(a));
}

const KIND_LABEL: Record<QueueItem["kind"], string> = {
  hypothesis: "Hipótesis",
  entity: "Entidad propuesta",
  relation: "Relación propuesta",
  contradiction: "Contradicción",
};

const KIND_TONE: Record<QueueItem["kind"], Tone> = {
  hypothesis: "accent",
  entity: "neutral",
  relation: "neutral",
  contradiction: "warn",
};

function titleOf(item: QueueItem): string {
  switch (item.kind) {
    case "hypothesis":
      return `${item.link.account_a.handle} y ${item.link.account_b.handle}`;
    case "entity":
      return `${entityTypeLabel(item.entity.type)}: ${item.entity.label}`;
    case "relation":
      return `Relación ${relationLabel(item.relation.type)}`;
    case "contradiction":
      return item.contradiction.subject || "Posible contradicción entre fuentes";
  }
}

function whyOf(item: QueueItem): string {
  switch (item.kind) {
    case "hypothesis":
      return `Puntaje de similitud ${item.link.score.toFixed(2).replace(".", ",")}. Es una hipótesis, no una identificación.`;
    case "entity":
      return item.entity.source_id ? "Lo encontró una herramienta o un texto que cargaste." : "Lo propuso el sistema y no tiene fuente registrada.";
    case "relation":
      return "Conecta dos entidades del caso. Hay que confirmar que la relación exista.";
    case "contradiction":
      return item.contradiction.summary;
  }
}

function confidenceChip(item: QueueItem) {
  if (item.kind === "hypothesis") {
    const tone: Tone = item.link.confidence === "alta" ? "accent" : "neutral";
    return <Chip tone={tone} title="Confianza: qué tan sólido es el conjunto de indicios. No es una certeza.">Confianza {item.link.confidence}</Chip>;
  }
  if (item.kind === "entity") return <Chip>{ITEM_STATUS_LABEL[item.entity.status] ?? item.entity.status}</Chip>;
  if (item.kind === "relation") return <Chip>{pct(item.relation.confidence)} de confianza</Chip>;
  return <Chip tone={item.contradiction.severity === "alta" ? "bad" : "warn"}>Severidad {item.contradiction.severity}</Chip>;
}

export function RevisarPage() {
  const ctx = useCase();
  const toast = useToast();
  const location = useLocation();
  const queue = useAsync(() => loadQueue(ctx.caseId), [ctx.caseId]);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [contradictionsNote, setContradictionsNote] = useState("");
  const [searchingContradictions, setSearchingContradictions] = useState(false);
  const [contradictionError, setContradictionError] = useState<unknown>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<"todo" | QueueItem["kind"]>("todo");
  const [menardBusy, setMenardBusy] = useState(false);
  const [menardNote, setMenardNote] = useState<string | null>(null);

  const items = useMemo(
    () => (queue.data ? buildQueueItems(queue.data, contradictions, hidden) : []),
    [queue.data, contradictions, hidden],
  );
  const visible = filter === "todo" ? items : items.filter((item) => item.kind === filter);

  const openKey = new URLSearchParams(location.search).get("item");
  const openIndex = openKey ? items.findIndex((item) => item.key === openKey) : -1;

  function open(key: string | null) {
    navigate(key ? `/casos/${ctx.caseId}/revisar?item=${key}` : `/casos/${ctx.caseId}/revisar`);
  }

  async function searchContradictions() {
    setSearchingContradictions(true);
    setContradictionError(null);
    try {
      const sources: SourceOut[] = (await ep.listSources(ctx.caseId, { limit: 200 })).items;
      const withText = sources.filter((s) => s.has_raw).map((s) => s.id).slice(0, 20);
      if (!withText.length) {
        setContradictionsNote("No hay fuentes con texto en el caso, así que no hay nada que comparar.");
        setContradictions([]);
      } else {
        const out = await ep.findContradictions(ctx.caseId, withText);
        setContradictions(out.contradictions);
        setContradictionsNote(
          out.contradictions.length
            ? `Encontré ${plural(out.contradictions.length, "posible contradicción", "posibles contradicciones")}.`
            : "No encontré contradicciones entre las fuentes con texto.",
        );
      }
    } catch (error) {
      setContradictionError(error);
    } finally {
      setSearchingContradictions(false);
    }
  }

  async function runMenard() {
    setMenardBusy(true);
    setMenardNote(null);
    try {
      const out = await ep.runMenard(ctx.caseId, {});
      setMenardNote(
        `Se analizaron ${plural(out.accounts_analyzed, "cuenta", "cuentas")}. Hipótesis nuevas: ${out.links_created ?? 0}. Actualizadas: ${out.links_updated ?? 0}.`,
      );
      queue.reload();
      ctx.reloadPending();
    } catch (error) {
      setMenardNote(errorMessage(error));
    } finally {
      setMenardBusy(false);
    }
  }

  function afterDecision(key: string, message: string) {
    setHidden((prev) => new Set(prev).add(key));
    toast.notify(message, "ok");
    ctx.reloadPending();
    const remaining = items.filter((item) => item.key !== key);
    const current = items.findIndex((item) => item.key === key);
    const next = remaining[current] ?? remaining[current - 1] ?? null;
    open(next ? next.key : null);
  }

  function goTo(delta: number) {
    if (openIndex < 0) return;
    const target = items[openIndex + delta];
    if (target) open(target.key);
  }

  useEffect(() => {
    // Si el enlace apunta a un ítem que ya no está pendiente, volvemos a la lista.
    if (openKey && queue.data && openIndex < 0 && !contradictions.length) open(null);
  }, [openKey, queue.data, openIndex]); // eslint-disable-line react-hooks/exhaustive-deps

  if (queue.error) return <ErrorBox error={queue.error} onRetry={queue.reload} />;
  if (!queue.data) return <Loading what="Buscando lo que espera tu decisión" />;

  if (openKey && openIndex >= 0) {
    const item = items[openIndex];
    return (
      <DecisionView
        item={item}
        position={openIndex + 1}
        total={items.length}
        onPrev={openIndex > 0 ? () => goTo(-1) : undefined}
        onNext={openIndex < items.length - 1 ? () => goTo(1) : undefined}
        onClose={() => open(null)}
        onDecided={(decision: Decision) => afterDecision(item.key, decision.message)}
        onDiscardLocal={(message) => afterDecision(item.key, message)}
      />
    );
  }

  const counts = {
    hypothesis: queue.data.links.length,
    entity: queue.data.entities.length,
    relation: queue.data.relations.length,
    contradiction: contradictions.length,
  };
  const total = items.length;
  const tabs: Array<{ key: "todo" | QueueItem["kind"]; label: string; n: number }> = [
    { key: "todo", label: "Todo", n: total },
    { key: "hypothesis", label: "Hipótesis", n: counts.hypothesis },
    { key: "entity", label: "Entidades", n: counts.entity },
    { key: "relation", label: "Relaciones", n: counts.relation },
    { key: "contradiction", label: "Contradicciones", n: counts.contradiction },
  ];

  return (
    <div className="page">
      <PageTitle lead="Todo lo que espera una decisión tuya, por importancia. Nada cambia en el caso hasta que decidas.">
        Revisar
      </PageTitle>

      <Panel
        title={total ? plural(total, "cosa espera tu decisión", "cosas esperan tu decisión") : "Nada espera tu decisión"}
        actions={
          <>
            <Button
              variant="secondary"
              disabled={!ctx.canWrite || menardBusy}
              onClick={runMenard}
              aria-describedby="menard-help"
            >
              {menardBusy ? "Buscando…" : "Buscar cuentas que podrían ser del mismo operador"}
            </Button>
            <Help text="MENARD compara estilo, horarios y red de cuentas, y propone hipótesis. Cada hipótesis queda para tu revisión." />
            <Button variant="secondary" disabled={!ctx.canWrite || searchingContradictions} onClick={searchContradictions}>
              {searchingContradictions ? "Buscando…" : "Buscar contradicciones entre fuentes"}
            </Button>
          </>
        }
      >
        <p id="menard-help" className="small muted">
          Las contradicciones usan el modelo de IA local sobre las fuentes con texto. Si el modelo no responde, la búsqueda queda sin resultados y te lo digo.
        </p>
        {menardNote && (
          <Notice tone="info">
            <p>{menardNote}</p>
          </Notice>
        )}
        {contradictionsNote && !contradictionError && (
          <Notice tone="info">
            <p>{contradictionsNote}</p>
          </Notice>
        )}
        {contradictionError ? (
          <Notice tone="bad" title="No se pudieron buscar contradicciones">
            <p>{errorMessage(contradictionError)}</p>
          </Notice>
        ) : null}
      </Panel>

      <nav className="tabs" aria-label="Filtrar por tipo">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={filter === tab.key}
            onClick={() => setFilter(tab.key)}
          >
            {tab.label} ({tab.n})
          </button>
        ))}
      </nav>

      {visible.length === 0 ? (
        <Empty title={total ? "Nada en esta categoría." : "Nada espera tu decisión."}>
          {total ? (
            <p>Cambiá el filtro para ver el resto.</p>
          ) : (
            <p>
              Cuando reúnas datos o MENARD proponga hipótesis, van a aparecer acá.{" "}
              <Link to={`/casos/${ctx.caseId}/reunir`}>Ir a Reunir</Link>
            </p>
          )}
        </Empty>
      ) : (
        <ol className="list" aria-label="Pendientes">
          {visible.map((item) => (
            <li key={item.key}>
              <div className="item">
                <div className="item-main">
                  <div className="row small" style={{ marginBottom: 4 }}>
                    <Chip tone={KIND_TONE[item.kind]}>{KIND_LABEL[item.kind]}</Chip>
                    {confidenceChip(item)}
                  </div>
                  <p className="item-title">{titleOf(item)}</p>
                  <p className="item-meta">{whyOf(item)}</p>
                </div>
                <div className="item-actions">
                  <Button variant="primary" disabled={!ctx.canWrite} onClick={() => open(item.key)}>
                    Revisar
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {total > 0 && (
        <p className="small muted">
          Ordenado por importancia: primero lo que más cambia el caso. Podés decidir en orden, con los atajos del paso siguiente.
        </p>
      )}
    </div>
  );
}
