"""Sembrador del caso de demostración (ficticio) de Aleph.

    python -m aleph.demo.seed                          # ALEPH_DATABASE_URL y ALEPH_DATA_DIR
    python -m aleph.demo.seed --database-url sqlite:///./data/demo/aleph-demo.db --data-dir ./data/demo

Qué crea, en una sola transacción (todo o nada):
- Usuarios: el admin si no hay ninguno, y los de demo `analista-demo` y `auditor-demo`.
- Un caso ficticio con 16 personas sintéticas (aleph.menard.synth), sus cuentas y publicaciones.
- Una corrida de MENARD y dos decisiones del analista (una confirmación y un descarte). Son simuladas:
  para que la demo sea reproducible, se toman con la verdad de terreno del generador.
- La infraestructura inventada de la campaña, confirmada; y propuestas pendientes de las reglas de
  FUNES, del extractor de IOCs y de una persona atribuida como hipótesis.
- Hallazgos repartidos en los incisos del expediente, una contradicción espacio-temporal entre dos
  fuentes y técnicas ATT&CK (tres confirmadas y una propuesta).

Idempotente: si el caso ya existe, no hace nada. Las contraseñas se toman de --password o de
ALEPH_DEMO_PASSWORD (y la del admin de ALEPH_ADMIN_PASSWORD). Solo se imprimen las que genera el script.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aleph.api import auditlog, security
from aleph.api.bootstrap import BootstrapError, create_admin
from aleph.api.errors import ServiceError
from aleph.api.schemas import _password
from aleph.api.services import cti as cti_service
from aleph.api.services import funes as funes_service
from aleph.api.services.ingest import ingest_collection
from aleph.api.services.lens import create_finding, ensure_default_sections
from aleph.api.services.menard import review_link, run_menard
from aleph.api.services.proposals import make_source, persist_graph, record_job
from aleph.core.config import get_settings
from aleph.core.db import init_db, make_engine
from aleph.core.models import (
    Account,
    AccountLink,
    AuditEvent,
    Case,
    CaseSection,
    Entity,
    Finding,
    Post,
    Relation,
    User,
)
from aleph.core.schemas import (
    CollectionResult,
    Datachunk,
    EntityRecord,
    FindingCreate,
    RelationRecord,
)
from aleph.funes.contradictions import explain_contradictions
from aleph.funes.ner import extract_rule_entities
from aleph.menard.synth import generate_world

from . import story

ANALYST_REVIEW_NOTE = "Coinciden el horario de publicación y el uso de muletillas (decisión simulada de la demo)."
ANALYST_REJECT_NOTE = (
    "Descartada: el estilo y el horario no coinciden al revisar las publicaciones "
    "(decisión simulada de la demo)."
)


class SeedError(RuntimeError):
    pass


@dataclass
class SeedReport:
    created: bool
    case_id: int | None = None
    case_name: str = story.DEMO_CASE_NAME
    entities: int = 0
    entities_proposed: int = 0
    accounts: int = 0
    posts: int = 0
    relations: int = 0
    links: dict[str, int] = field(default_factory=dict)
    findings: int = 0
    sections: int = 0
    contradictions: int | None = None  # None si el caso ya existía: no se recalcula
    techniques_confirmed: int = 0
    techniques_proposed: int = 0
    audit_events: int = 0
    users_created: list[str] = field(default_factory=list)
    generated_passwords: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    steps: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------- usuarios


def ensure_users(session: Session, *, admin_username: str, admin_password: str | None,
                 demo_password: str | None) -> tuple[dict[str, User], dict[str, str], list[str]]:
    """Admin (si no hay ninguno), analista y auditor de demo. Devuelve (usuarios, contraseñas generadas, creados)."""
    generated: dict[str, str] = {}
    created: list[str] = []
    admin = session.execute(
        select(User).where(User.role == "admin", User.active.is_(True)).order_by(User.id)
    ).scalars().first()
    if admin is None:
        password = admin_password or secrets.token_urlsafe(12)
        if not admin_password:
            generated[admin_username] = password
        try:
            admin = create_admin(session, admin_username, _password(password))
        except (BootstrapError, ValueError) as exc:
            raise SeedError(f"No se pudo crear el administrador: {exc}") from exc
        created.append(admin.username)
    shared = demo_password or secrets.token_urlsafe(12)
    try:
        _password(shared)
    except ValueError as exc:
        raise SeedError(f"La contraseña de demo no es válida: {exc}") from exc
    users: dict[str, User] = {"admin": admin}
    for username, role in ((story.DEMO_ANALYST, "analyst"), (story.DEMO_AUDITOR, "auditor")):
        user = session.execute(select(User).where(func.lower(User.username) == username)).scalars().first()
        if user is None:
            user = User(username=username, password_hash=security.hash_password(shared), role=role)
            session.add(user)
            session.flush()
            auditlog.record(session, "user.create", user_id=admin.id, target=f"user:{username}",
                            detail={"id": user.id, "role": role, "origen": "demo"})
            created.append(username)
            if demo_password is None:
                generated.setdefault("__demo__", shared)
        users[role] = user
    return users, generated, created


# ---------------------------------------------------------------- pasos del caso


def _ingest_world(session: Session, case: Case, analyst: User, data_dir, seed: int, personas: int):
    world = generate_world(seed=seed, n_personas=personas)
    collection = CollectionResult(
        connector=story.DEMO_CONNECTOR,
        reference=f"synthetic://aleph.menard.synth?seed={seed}&personas={personas}",
        retrieved_at=story.DEMO_RETRIEVED_AT, profiles=world.profiles,
    )
    summary = ingest_collection(session, case.id, collection, analyst.id, data_dir=data_dir)
    return world, summary


def _account_key(account: Account) -> str:
    return f"{account.platform}:{account.handle.lower()}"


def _analyst_decisions(session: Session, case_id: int, world, analyst: User) -> list[tuple[str, str]]:
    """Confirma la mejor hipótesis verdadera y descarta la mejor falsa. Devuelve las cuentas del operador."""
    accounts = {a.id: a for a in session.execute(select(Account).where(Account.case_id == case_id)).scalars()}
    links = session.execute(
        select(AccountLink).where(AccountLink.case_id == case_id, AccountLink.review_status == "pending")
        .order_by(AccountLink.score.desc(), AccountLink.id)
    ).scalars().all()
    confirm = reject = None
    for link in links:
        a, b = accounts[link.account_a_id], accounts[link.account_b_id]
        same = world.persona_of.get(_account_key(a)) == world.persona_of.get(_account_key(b))
        if same and confirm is None:
            confirm = link
        elif not same and reject is None and link.score >= 0.5:
            reject = link
        if confirm is not None and reject is not None:
            break
    operator: list[tuple[str, str]] = []
    if confirm is not None:
        review_link(session, case_id, confirm.id, analyst.id, "confirm", ANALYST_REVIEW_NOTE)
        a, b = accounts[confirm.account_a_id], accounts[confirm.account_b_id]
        operator = [(a.platform, a.handle), (b.platform, b.handle)]
    if reject is not None:
        review_link(session, case_id, reject.id, analyst.id, "reject", ANALYST_REJECT_NOTE)
    if not operator:
        operator = [(a.platform, a.handle) for a in list(accounts.values())[:2]]
    return operator


def _build_entities(rows: list[tuple[str, str, str, dict, float]]) -> tuple[list[EntityRecord], dict[str, str]]:
    refs: dict[str, str] = {}
    entities = []
    for ref, kind, label, props, confidence in rows:
        entities.append(EntityRecord(type=kind, label=label, props=props, confidence=confidence, ref=ref))
        refs[ref] = label
    return entities, refs


def _infrastructure(session: Session, case: Case, analyst: User, data_dir) -> None:
    source = make_source(
        session, case.id, analyst.id, kind="manual", connector="demo-analisis",
        reference="Análisis manual de la campaña (ficticio)",
        payload=("\n".join(label for _, _, label, _, _ in story.INFRA_ENTITIES)).encode("utf-8"),
        data_dir=data_dir, suffix=".txt",
    )
    entities, _ = _build_entities(story.INFRA_ENTITIES)
    relations = [RelationRecord(src_ref=s, dst_ref=d, type=t, props=p, confidence=c)
                 for s, d, t, p, c in story.INFRA_RELATIONS]
    persist_graph(session, case.id, entities, relations, source_id=source.id, status="confirmed")


def _pending(session: Session, case: Case, analyst: User, data_dir) -> None:
    """Propuestas pendientes: reglas de FUNES sobre el mensaje, y una persona atribuida como hipótesis."""
    rule_entities, _dates = extract_rule_entities(story.CAMPAIGN_MESSAGE)
    funes_source = make_source(
        session, case.id, analyst.id, kind="funes", connector="funes-reglas",
        reference="Mensaje de la campaña (ficticio), reglas de FUNES", payload=story.CAMPAIGN_MESSAGE.encode("utf-8"),
        data_dir=data_dir, suffix=".txt",
    )
    persist_graph(session, case.id, rule_entities, [], source_id=funes_source.id, status="proposed")

    event_ref = EntityRecord(type="event", label=story.CAMPAIGN, props={}, ref="camp")
    pending_entities = [EntityRecord(type=k, label=lbl, props=p, confidence=c, ref=r)
                        for r, k, lbl, p, c in story.PENDING_ENTITIES]
    pending_relations = [RelationRecord(src_ref=s, dst_ref=d, type=t, props=p, confidence=c)
                         for s, d, t, p, c in story.PENDING_RELATIONS]
    persist_graph(session, case.id, [event_ref, *pending_entities], pending_relations,
                  source_id=funes_source.id, status="proposed")


def _ioc_community_report(session: Session, case: Case, analyst: User, data_dir) -> None:
    cti_service.extract_iocs_to_graph(session, case, story.COMMUNITY_REPORT, analyst.id, data_dir)


def _findings(session: Session, case: Case, analyst: User, operator: list[tuple[str, str]],
              contradictions, claims) -> int:
    ensure_default_sections(session, case.id)
    sections = {s.name: s.id for s in session.execute(
        select(CaseSection).where(CaseSection.case_id == case.id)).scalars()}
    captured = story.DEMO_RETRIEVED_AT
    count = 0
    for item in story.findings_plan(operator):
        chunk = Datachunk(
            kind=item["kind"], value=item["value"], quote=item["quote"], context=item["quote"],
            page_url=item["page_url"], page_title=item["section"],
            platform=item.get("platform", "generic"), author_handle=item["value"].lstrip("@")
            if item["kind"] == "account" else "",
            detected_by="manual", captured_at=captured,
        )
        create_finding(session, case.id, FindingCreate(
            section_id=sections.get(item["section"]), chunk=chunk, note=item.get("note", "")), analyst.id)
        count += 1
    for claim in claims:
        create_finding(session, case.id, FindingCreate(
            section_id=sections.get(story.SECTION_ACTIVITY),
            chunk=Datachunk(kind="text", value=claim.quote, quote=claim.quote, context=claim.quote,
                            page_url=f"https://registro-ficticio.example/{claim.id}",
                            page_title="Afirmación para contradicciones", platform="generic",
                            detected_by="manual", captured_at=captured),
            note=f"Afirmación: {claim.predicate} = {claim.value}. Fuente: {claim.source}.",
        ), analyst.id)
        count += 1
    for contradiction in contradictions:
        create_finding(session, case.id, FindingCreate(
            section_id=sections.get(story.SECTION_ACTIVITY),
            chunk=Datachunk(kind="text", value=contradiction.summary, quote=contradiction.summary,
                            context=contradiction.summary, page_url="https://motor-funes.example/contradicciones",
                            page_title=f"Contradicción {contradiction.id}", platform="generic",
                            detected_by="funes", captured_at=captured),
            note=f"Severidad {contradiction.severity}. Lecturas posibles: "
                 + "; ".join(r.kind for r in contradiction.readings) + ".",
        ), analyst.id)
        count += 1
    return count


def _contradictions(claims):
    found = funes_service.detect(claims, max_speed_kmh=900.0)
    return asyncio.run(explain_contradictions(found, claims, None))


def _techniques(session: Session, case: Case, analyst: User) -> tuple[int, int]:
    for technique_id, score, comment in story.CONFIRMED_TECHNIQUES:
        cti_service.link_technique(session, case, technique_id, score=score, comment=comment, user_id=analyst.id)
    for technique_id, score, comment in story.PROPOSED_TECHNIQUES:
        cti_service.link_technique(session, case, technique_id, score=score, comment=comment,
                                   user_id=analyst.id, status=cti_service.STATUS_PROPOSED)
    return len(story.CONFIRMED_TECHNIQUES), len(story.PROPOSED_TECHNIQUES)


# ---------------------------------------------------------------- orquestación


def _count(session: Session, model, *where) -> int:
    return session.scalar(select(func.count(model.id)).where(*where)) or 0


def _report(session: Session, case: Case | None, *, created: bool, seconds: float, steps: dict[str, float],
            generated: dict[str, str], users_created: list[str], contradictions: int | None = None,
            tech: tuple[int, int] = (0, 0)) -> SeedReport:
    if case is None:
        return SeedReport(created=False, seconds=seconds, steps=steps)
    links = dict(session.execute(
        select(AccountLink.review_status, func.count(AccountLink.id))
        .where(AccountLink.case_id == case.id).group_by(AccountLink.review_status)).all())
    attack_states = [f.chunk or {} for f in session.execute(
        select(Finding).where(Finding.case_id == case.id, Finding.kind == "attack")).scalars()]
    return SeedReport(
        created=created, case_id=case.id, case_name=case.name,
        entities=_count(session, Entity, Entity.case_id == case.id),
        entities_proposed=_count(session, Entity, Entity.case_id == case.id, Entity.status == "proposed"),
        accounts=_count(session, Account, Account.case_id == case.id),
        posts=session.scalar(select(func.count(Post.id)).join(Account, Post.account_id == Account.id)
                             .where(Account.case_id == case.id)) or 0,
        relations=_count(session, Relation, Relation.case_id == case.id),
        links={k: int(v) for k, v in links.items()},
        findings=_count(session, Finding, Finding.case_id == case.id),
        sections=_count(session, CaseSection, CaseSection.case_id == case.id),
        contradictions=contradictions,
        techniques_confirmed=tech[0] if created else sum(1 for s in attack_states if s.get("estado", "confirmada") == "confirmada"),
        techniques_proposed=tech[1] if created else sum(1 for s in attack_states if s.get("estado") == "propuesta"),
        audit_events=_count(session, AuditEvent, AuditEvent.case_id == case.id),
        users_created=users_created, generated_passwords=generated, seconds=seconds, steps=steps,
    )


def seed(session: Session, *, data_dir: str | Path, demo_password: str | None = None,
         admin_username: str = story.DEMO_ADMIN, admin_password: str | None = None,
         personas: int = story.DEMO_PERSONAS, seed: int = story.DEMO_SEED) -> SeedReport:
    """Siembra el caso de demo. Hace commit al final: si algo falla, no queda nada a medias."""
    started = time.perf_counter()
    steps: dict[str, float] = {}
    existing = session.execute(select(Case).where(Case.name == story.DEMO_CASE_NAME).order_by(Case.id)).scalars().first()
    if existing is not None:
        return _report(session, existing, created=False, seconds=time.perf_counter() - started, steps=steps,
                       generated={}, users_created=[])

    t = time.perf_counter()
    users, generated, users_created = ensure_users(
        session, admin_username=admin_username, admin_password=admin_password, demo_password=demo_password)
    analyst, auditor = users["analyst"], users["auditor"]
    case = Case(name=story.DEMO_CASE_NAME, description=story.DEMO_DESCRIPTION, legal_basis=story.DEMO_LEGAL_BASIS,
                tlp="amber", created_by=analyst.id)
    session.add(case)
    session.flush()
    sections, _ = ensure_default_sections(session, case.id)
    auditlog.record(session, "case.create", user_id=analyst.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"name": case.name, "tlp": case.tlp, "legal_basis": case.legal_basis,
                            "sections": [s.name for s in sections], "origen": "demo"})
    steps["usuarios y caso"] = time.perf_counter() - t

    t = time.perf_counter()
    world, _ingest = _ingest_world(session, case, analyst, data_dir, seed, personas)
    steps["ingesta sintética"] = time.perf_counter() - t

    t = time.perf_counter()
    menard = run_menard(session, case.id, analyst.id)
    record_job(session, case.id, "menard", params={"origen": "demo", "seed": seed, "personas": personas},
               result={"accounts_analyzed": menard.accounts_analyzed, "pairs_returned": menard.pairs_returned,
                       "links_created": menard.links_created}, user_id=analyst.id)
    operator = _analyst_decisions(session, case.id, world, analyst)
    steps["MENARD y revisión"] = time.perf_counter() - t

    t = time.perf_counter()
    _infrastructure(session, case, analyst, data_dir)
    _pending(session, case, analyst, data_dir)
    _ioc_community_report(session, case, analyst, data_dir)
    steps["infraestructura y propuestas"] = time.perf_counter() - t

    t = time.perf_counter()
    claims = story.contradiction_claims()
    contradictions = _contradictions(claims)
    _findings(session, case, analyst, operator, contradictions, claims)
    tech = _techniques(session, case, analyst)
    steps["expediente y ATT&CK"] = time.perf_counter() - t

    auditlog.record(session, "demo.seed", user_id=analyst.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"seed": seed, "personas": personas, "auditor": auditor.username, "origen": "demo"})
    session.commit()
    return _report(session, case, created=True, seconds=time.perf_counter() - started, steps=steps,
                   generated=generated, users_created=users_created, contradictions=len(contradictions),
                   tech=tech)


# ---------------------------------------------------------------- CLI


def _print(report: SeedReport) -> None:
    if not report.created:
        print(f"El caso de demostración ya existe (id {report.case_id}). No se modificó nada.")
        return
    print(f"Caso de demostración creado (id {report.case_id}): {report.case_name}")
    print(f"  entidades: {report.entities} ({report.entities_proposed} propuestas pendientes)")
    print(f"  cuentas: {report.accounts} · publicaciones: {report.posts} · relaciones: {report.relations}")
    print(f"  hipótesis de MENARD: {sum(report.links.values())} "
          f"(pendientes {report.links.get('pending', 0)}, confirmadas {report.links.get('confirmed', 0)}, "
          f"descartadas {report.links.get('rejected', 0)})")
    print(f"  hallazgos: {report.findings} en {report.sections} incisos · contradicciones: {report.contradictions}")
    print(f"  técnicas ATT&CK: {report.techniques_confirmed} confirmadas, {report.techniques_proposed} propuesta(s)")
    print(f"  eventos de auditoría del caso: {report.audit_events}")
    for step, seconds in report.steps.items():
        print(f"  · {step}: {seconds:.2f} s")
    print(f"Tiempo total: {report.seconds:.2f} s")
    if report.generated_passwords:
        print()
        print("Contraseñas generadas por el script (no se guardan en ningún lado):")
        for name, password in report.generated_passwords.items():
            label = "usuarios analista-demo y auditor-demo" if name == "__demo__" else f"admin «{name}»"
            print(f"  {label}: {password}")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="python -m aleph.demo.seed",
                                     description="Crea el caso de demostración ficticio de Aleph.")
    parser.add_argument("--database-url", default=settings.database_url, help="Por defecto, ALEPH_DATABASE_URL")
    parser.add_argument("--data-dir", default=settings.data_dir, help="Por defecto, ALEPH_DATA_DIR")
    parser.add_argument("--password", default=os.environ.get("ALEPH_DEMO_PASSWORD"),
                        help="Contraseña de analista-demo y auditor-demo (o ALEPH_DEMO_PASSWORD)")
    parser.add_argument("--admin-username", default=os.environ.get("ALEPH_ADMIN_USERNAME") or story.DEMO_ADMIN)
    parser.add_argument("--admin-password", default=os.environ.get("ALEPH_ADMIN_PASSWORD"),
                        help="Solo si no hay admin en la base (o ALEPH_ADMIN_PASSWORD)")
    parser.add_argument("--personas", type=int, default=story.DEMO_PERSONAS)
    parser.add_argument("--seed", type=int, default=story.DEMO_SEED)
    args = parser.parse_args(argv)

    url = args.database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        db_path = url.removeprefix("sqlite:///")
        if db_path and not db_path.startswith(":"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(url)
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            report = seed(session, data_dir=args.data_dir, demo_password=args.password,
                          admin_username=args.admin_username, admin_password=args.admin_password,
                          personas=args.personas, seed=args.seed)
    except (SeedError, ServiceError) as exc:
        print(f"Error: {exc.message if isinstance(exc, ServiceError) else exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
