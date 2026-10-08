"""Caja de herramientas OSINT (WP13): utilidades de investigación en fuentes abiertas.

Cada herramienta vive en su propio módulo y expone una función (async si consulta red,
sync si es cálculo puro) que devuelve un modelo pydantic. Los resultados traen, cuando
aplica, `entities` y `relations` (esquemas de `aleph.core.schemas`) para el grafo del caso.
`registry.py` lista las herramientas disponibles.

Solo fuentes públicas y documentadas, de solo lectura. Ninguna herramienta hace escaneo
activo de puertos ni evade controles de acceso.
"""
