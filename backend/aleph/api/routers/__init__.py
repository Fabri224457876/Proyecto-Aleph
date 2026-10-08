"""Arma el router raíz de la API (se monta en `/api` desde `aleph.main`)."""

from ..routing import make_router
from . import (
    accounts,
    auth,
    cases,
    collect,
    cti,
    exchange,
    funes,
    graph,
    lens,
    menard,
    sources,
    system,
    tools,
)

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
# Integración: recolección, herramientas OSINT, intercambio STIX/MISP, CTI y FUNES
router.include_router(collect.router)
router.include_router(tools.router)
router.include_router(tools.case_router)
router.include_router(exchange.router)
router.include_router(cti.router)
router.include_router(cti.attack)
router.include_router(funes.router)

__all__ = ["router"]
