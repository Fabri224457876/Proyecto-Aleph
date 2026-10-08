import { useEffect, useState, type FormEvent } from "react";
import * as endpoints from "../api/endpoints";
import type { CaseOut } from "../api/types";
import { Badge, Btn, Empty, ErrorBox, Field, Loading, Modal, Segmented, TlpBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { CASE_STATUS_LABEL, TLP_HINT, TLP_LABEL, TLP_ORDER } from "../lib/labels";
import { errorMessage, fmtDate, plural, truncate } from "../lib/format";
import { Link, navigate, useLocation } from "../lib/router";
import { useToast } from "../lib/toast";
import { useAsync } from "../lib/useAsync";
import { setCaseChip } from "../lib/caseChip";
import { Icon } from "../components/Icon";

const PAGE_SIZE = 25;
type StatusFilter = "all" | "open" | "closed" | "archived";

export function CasesPage() {
  const { canWrite } = useAuth();
  const { search } = useLocation();
  const toast = useToast();
  const params = new URLSearchParams(search);
  const [status, setStatus] = useState<StatusFilter>((params.get("estado") as StatusFilter) || "all");
  const [tlp, setTlp] = useState<string>(params.get("tlp") ?? "");
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [debouncedQuery, setDebouncedQuery] = useState(query);
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    setCaseChip(null);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
      setOffset(0);
    }, 280);
    return () => window.clearTimeout(timer);
  }, [query]);

  const list = useAsync(
    () =>
      endpoints.listCases({
        status: status === "all" ? undefined : status,
        tlp: tlp || undefined,
        q: debouncedQuery || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    [status, tlp, debouncedQuery, offset],
  );

  const total = list.data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Casos</h1>
          <p className="page-sub">
            Cada caso exige su propósito y base legal antes de abrirse. Todo lo que pasa dentro queda auditado.
          </p>
        </div>
        {canWrite ? (
          <Btn variant="primary" icon="plus" onClick={() => setCreating(true)}>
            Nuevo caso
          </Btn>
        ) : null}
      </div>

      <div className="toolbar">
        <Segmented<StatusFilter>
          label="Estado del caso"
          value={status}
          onChange={(value) => {
            setStatus(value);
            setOffset(0);
          }}
          options={[
            { value: "all", label: "Todos" },
            { value: "open", label: "Abiertos" },
            { value: "closed", label: "Cerrados" },
            { value: "archived", label: "Archivados" },
          ]}
        />
        <label className="sr-only" htmlFor="cases-tlp">
          Filtrar por TLP
        </label>
        <select
          id="cases-tlp"
          className="select select-compact"
          value={tlp}
          onChange={(event) => {
            setTlp(event.target.value);
            setOffset(0);
          }}
        >
          <option value="">Todo TLP</option>
          {TLP_ORDER.map((level) => (
            <option key={level} value={level}>
              TLP:{TLP_LABEL[level]}
            </option>
          ))}
        </select>
        <div className="search-box">
          <Icon name="search" />
          <input
            className="input"
            type="search"
            placeholder="Buscar por nombre o descripción"
            aria-label="Buscar casos"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      {list.loading && !list.data ? <Loading label="Cargando casos…" /> : null}
      {list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : null}

      {list.data && list.data.items.length === 0 ? (
        <Empty
          title={debouncedQuery || status !== "all" || tlp ? "Ningún caso coincide con el filtro" : "Todavía no hay casos"}
          action={
            canWrite && !debouncedQuery && status === "all" && !tlp ? (
              <Btn variant="primary" icon="plus" onClick={() => setCreating(true)}>
                Abrir el primer caso
              </Btn>
            ) : undefined
          }
        >
          {canWrite
            ? "Un caso reúne entidades, cuentas, fuentes y hallazgos bajo una misma base legal."
            : "Tu rol puede consultar casos pero no abrirlos. Pedile a un analista que cree uno."}
        </Empty>
      ) : null}

      {list.data && list.data.items.length > 0 ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className="col-id">ID</th>
                  <th>Caso</th>
                  <th className="col-tlp">TLP</th>
                  <th className="col-status">Estado</th>
                  <th className="col-date">Creado</th>
                  <th>Base legal</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((item) => (
                  <CaseRow key={item.id} item={item} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="dim mono">
              {from}–{to} de {plural(total, "caso", "casos")}
            </span>
            <div className="pager-buttons">
              <Btn size="sm" disabled={offset === 0 || list.loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Anterior
              </Btn>
              <Btn size="sm" disabled={offset + PAGE_SIZE >= total || list.loading} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Siguiente
              </Btn>
            </div>
          </div>
        </>
      ) : null}

      {creating ? (
        <NewCaseModal
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            setCreating(false);
            toast.notify(`Caso #${created.id} abierto.`, "ok");
            navigate(`/casos/${created.id}/grafo`);
          }}
        />
      ) : null}
    </div>
  );
}

function CaseRow({ item }: { item: CaseOut }) {
  return (
    <tr>
      <td className="mono dim">#{item.id}</td>
      <td>
        <Link to={`/casos/${item.id}/grafo`} className="row-link">
          {item.name}
        </Link>
        {item.description ? <div className="row-sub">{truncate(item.description, 110)}</div> : null}
      </td>
      <td>
        <TlpBadge tlp={item.tlp} />
      </td>
      <td>
        <Badge tone={item.status === "open" ? "ok" : "muted"}>{CASE_STATUS_LABEL[item.status] ?? item.status}</Badge>
      </td>
      <td className="mono dim">{fmtDate(item.created_at)}</td>
      <td className="legal-cell" title={item.legal_basis}>
        {truncate(item.legal_basis, 72)}
      </td>
    </tr>
  );
}

function NewCaseModal({ onClose, onCreated }: { onClose: () => void; onCreated: (created: CaseOut) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [legalBasis, setLegalBasis] = useState("");
  const [tlp, setTlp] = useState("amber");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);

  const nameMissing = !name.trim();
  const legalMissing = !legalBasis.trim();

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched(true);
    if (nameMissing || legalMissing) return;
    setBusy(true);
    setError(null);
    try {
      const created = await endpoints.createCase({
        name: name.trim(),
        description: description.trim(),
        legal_basis: legalBasis.trim(),
        tlp,
      });
      onCreated(created);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Abrir un caso"
      onClose={onClose}
      width={600}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn type="submit" form="new-case-form" variant="primary" disabled={busy}>
            {busy ? "Abriendo…" : "Abrir caso"}
          </Btn>
        </>
      }
    >
      <form id="new-case-form" className="form-stack" onSubmit={onSubmit} noValidate>
        <Field label="Nombre del caso" error={touched && nameMissing ? "Ponele un nombre al caso." : null}>
          {(id) => (
            <input
              id={id}
              className="input"
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoFocus
            />
          )}
        </Field>

        <Field label="Propósito y base legal" error={touched && legalMissing ? "Sin propósito y base legal no se abre el caso." : null}
          hint="Obligatorio. Explicá para qué se investiga y en qué encargo o norma se apoya (Ley 25.326). El backend rechaza el alta sin este dato.">
          {(id) => (
            <textarea
              id={id}
              className="textarea"
              rows={4}
              value={legalBasis}
              onChange={(event) => setLegalBasis(event.target.value)}
              aria-required="true"
            />
          )}
        </Field>

        <Field label="Descripción" hint="Opcional. Contexto breve; no hace falta que sea sensible.">
          {(id) => (
            <textarea id={id} className="textarea" rows={2} value={description} onChange={(event) => setDescription(event.target.value)} />
          )}
        </Field>

        <Field label="Marca TLP" hint={TLP_HINT[tlp]}>
          {(id) => (
            <select id={id} className="select" value={tlp} onChange={(event) => setTlp(event.target.value)}>
              {TLP_ORDER.map((level) => (
                <option key={level} value={level}>
                  TLP:{TLP_LABEL[level]}
                </option>
              ))}
            </select>
          )}
        </Field>

        {error ? (
          <p className="inline-error" role="alert">
            {errorMessage(error)}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
