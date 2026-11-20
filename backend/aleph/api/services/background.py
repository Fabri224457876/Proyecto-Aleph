"""Trabajos en segundo plano: una sola cola dentro del proceso de la API.

Los motores pesados (TLÖN) no corren dentro del pedido HTTP: el pedido deja el trabajo en la tabla
`jobs` y lo entrega a esta cola; quien lo pidió lo sigue con `GET /api/jobs/{id}`.

Un solo hilo, a propósito: los motores usan CPU a fondo, la cadena de auditoría se serializa dentro
del proceso y la API corre con un solo proceso (ver `deploy/api.Dockerfile`). Dos corridas a la vez
no irían más rápido, solo competirían.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# runner(función, *argumentos): despacha la función a la cola y vuelve enseguida
Runner = Callable[..., Any]

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def thread_runner(fn: Callable[..., Any], *args: Any) -> None:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aleph-job")
        executor = _executor
    # La función atrapa sus propios errores y deja el resultado en el trabajo: nadie lee el Future
    executor.submit(fn, *args)


def job_runner() -> Runner:
    """Dependencia de la API: cómo se despachan los trabajos. Las pruebas la reemplazan por una
    ejecución en línea, para no depender de hilos ni de esperas."""
    return thread_runner
