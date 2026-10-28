import { useEffect } from "react";
import * as ep from "../api/endpoints";
import { ErrorBox, Loading } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { navigate } from "../lib/router";

/** Abre el caso en el momento que le corresponde: Reunir si está vacío, Entender si ya tiene contenido. */
export function CaseIndex({ caseId }: { caseId: number }) {
  const state = useAsync(async () => {
    const [entities, findings] = await Promise.all([
      ep.listEntities(caseId, { limit: 1 }),
      ep.listFindings(caseId, { limit: 1 }),
    ]);
    return entities.total + findings.total;
  }, [caseId]);

  useEffect(() => {
    if (state.data === undefined) return;
    navigate(`/casos/${caseId}/${state.data > 0 ? "entender" : "reunir"}`, { replace: true });
  }, [state.data, caseId]);

  if (state.error) return <ErrorBox error={state.error} onRetry={state.reload} />;
  return <Loading what="Abriendo el caso" />;
}
