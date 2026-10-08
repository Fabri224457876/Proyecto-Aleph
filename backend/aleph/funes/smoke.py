"""Prueba de humo de punta a punta contra los endpoints reales del cluster.

    python -m aleph.funes.smoke                 extracción de entidades (reglas + LLM)
    python -m aleph.funes.smoke --embed         además prueba el endpoint de embeddings
    python -m aleph.funes.smoke --no-llm        solo reglas, sin red
    python -m aleph.funes.smoke --file nota.txt usa un texto propio

Lee la configuración de las variables ALEPH_* (ver core/config.py). Sale con código 0 si la
extracción con LLM devolvió al menos una entidad validada contra el texto, 1 si no.
El texto de ejemplo es sintético: no describe a personas reales.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from aleph.core.config import get_settings
from aleph.funes.client import FunesClient, LLMError
from aleph.funes.embed import SemanticIndex
from aleph.funes.ner import extract_entities

SAMPLE = """\
Parte de novedades (texto ficticio de prueba). El 12 de marzo de 2024 a las 14:30, personal de la
comisaría de Villa Ficticia identificó a Ramiro Ezequiel Quiroga Ledesma, DNI 12.345.678,
CUIT 20-12345678-6, con domicilio en Av. Corrientes 1234, piso 3, Ciudad de Buenos Aires.
QUIROGA LEDESMA, Ramiro, a quien en el barrio conocen como "el Tano", sería integrante de la
Cooperativa de Trabajo El Aleph Limitada, que preside Marta Inés Albornoz. Se desplazaba en un
Fiat Cronos patente AB123CD junto a R. Quiroga hijo. Teléfono de contacto: 011 15 4321-5678;
correo: contacto@cooperativa-ejemplo.com.ar. Más datos en https://ejemplo.org/parte/42.
"""


def _print_result(result) -> None:
    print(f"\nEntidades propuestas: {len(result.entities)}")
    for entity in result.entities:
        props = entity.props
        print(f"  {entity.ref:>4}  {entity.type:<13} {entity.label:<45} "
              f"conf={entity.confidence:.2f}  {props['metodo']:<5} offset={props['offset']}")
    print(f"\nRelaciones propuestas: {len(result.relations)}")
    for relation in result.relations:
        print(f"  {relation.src_ref} --{relation.type}--> {relation.dst_ref}  "
              f"conf={relation.confidence:.2f}  «{relation.props['cita'][:70]}»")
    print(f"\nFechas: {[d.iso for d in result.dates]}")
    if result.discarded:
        print(f"\nDescartado por la validación de citas: {len(result.discarded)}")
        for item in result.discarded:
            print(f"  [{item.kind}] {item.reason}: {item.data}")
    for warning in result.warnings:
        print(f"\nAVISO: {warning}")


async def run(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else SAMPLE
    settings = get_settings()
    if args.no_llm:
        _print_result(await extract_entities(text, None, use_llm=False))
        return 0
    print(f"LLM: {settings.llm_model} en {settings.llm_base_url} "
          f"(clave {'configurada' if settings.llm_api_key else 'vacía'})")
    try:
        client = FunesClient.from_settings(settings, timeout=args.timeout)
    except LLMError as exc:
        print(f"ERROR de configuración: {exc}")
        return 1
    async with client:
        started = time.perf_counter()
        result = await extract_entities(text, client)
        print(f"Extracción en {time.perf_counter() - started:.1f} s")
        _print_result(result)
        ok = any(e.props.get("metodo") == "llm" or "llm" in e.props.get("metodos", [])
                 for e in result.entities)
        if not ok:
            print("\nFALLO: el LLM no aportó ninguna entidad validada.")
        if args.embed:
            print(f"\nEmbeddings: {settings.embed_model} en {settings.embed_base_url}")
            try:
                index = SemanticIndex()
                sentences = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
                await index.add_texts(client, [f"s{i}" for i in range(len(sentences))], sentences)
                hits = await index.search(client, "¿en qué vehículo se movía?", k=2)
                for hit in hits:
                    print(f"  {hit.score:.3f}  {sentences[int(hit.id[1:])][:90]}")
                ok = ok and bool(hits)
            except LLMError as exc:
                print(f"FALLO en embeddings: {exc}")
                ok = False
    print("\nOK" if ok else "\nFALLO")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Prueba de humo de FUNES")
    parser.add_argument("--file", help="archivo de texto UTF-8 a analizar")
    parser.add_argument("--no-llm", action="store_true", help="solo reglas, sin red")
    parser.add_argument("--embed", action="store_true", help="probar también embeddings")
    parser.add_argument("--timeout", type=float, default=300.0, help="segundos por llamada")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
