import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  /** Vuelve a pedir los datos sin borrar lo que ya se muestra. */
  reload: () => void;
  /** Cambia los datos en pantalla (actualización optimista). */
  setData: (updater: T | undefined | ((current: T | undefined) => T | undefined)) => void;
}

/**
 * Carga datos al montar y cada vez que cambian `deps`. Las respuestas viejas se descartan
 * para que una búsqueda lenta no pise a la nueva.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    loaderRef
      .current()
      .then((result) => {
        if (!alive) return;
        setDataState(result);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setError(err);
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [...deps, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const setData = useCallback((updater: T | undefined | ((current: T | undefined) => T | undefined)) => {
    setDataState((current) => (typeof updater === "function" ? (updater as (c: T | undefined) => T | undefined)(current) : updater));
  }, []);

  return { data, error, loading, reload, setData };
}
