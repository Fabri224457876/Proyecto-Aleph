"""Arma el router raíz de la API (se monta en `/api` desde `aleph.main`)."""

from ..routing import make_router
from . import accounts, auth, cases, graph, lens, menard, sources, system

router = make_router()
router.include_router(auth.router)
router.include_router(cases.router)
router.include_router(graph.router)
router.include_router(sources.router)
router.include_router(accounts.router)
router.include_router(lens.router)
router.include_router(menard.router)
router.include_router(system.jobs)
router.include_router(system.audit)
router.include_router(system.connectors)

__all__ = ["router"]
