"""Sembrador de la demostración: idempotencia, determinismo, contraseñas y línea de comandos."""

import re

from sqlalchemy.orm import sessionmaker

from aleph.core import audit
from aleph.core.db import init_db, make_engine
from aleph.demo import seed as demo_seed

PW = "clave-demo-para-seed-123"
APW = "clave-admin-para-seed-123"


def _database(url: str):
    engine = make_engine(url)
    init_db(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _fingerprint(report) -> tuple:
    """Lo que se puede recontar desde la base. Las contradicciones no se guardan: no se recalculan."""
    return (report.entities, report.entities_proposed, report.accounts, report.posts, report.relations,
            tuple(sorted(report.links.items())), report.findings, report.sections,
            report.techniques_confirmed, report.techniques_proposed)


def test_sembrar_dos_veces_no_duplica_nada(tmp_path):
    engine, factory = _database("sqlite:///:memory:")
    with factory() as session:
        first = demo_seed.seed(session, data_dir=tmp_path, demo_password=PW, admin_password=APW)
    with factory() as session:
        second = demo_seed.seed(session, data_dir=tmp_path, demo_password=PW, admin_password=APW)
    assert first.created is True and second.created is False
    assert second.case_id == first.case_id and _fingerprint(second) == _fingerprint(first)
    assert {"analista-demo", "auditor-demo"} <= set(first.users_created)
    assert first.generated_passwords == {}  # ninguna contraseña la generó el script
    assert first.entities > 45 and first.accounts == 33 and first.posts > 1000
    assert first.contradictions == 1 and second.contradictions is None
    assert first.techniques_confirmed == 3 and first.techniques_proposed == 1
    with factory() as session:
        assert audit.verify_chain(session) == (True, None)
    engine.dispose()


def test_el_caso_es_ficticio_en_nombre_y_descripcion(tmp_path):
    engine, factory = _database("sqlite:///:memory:")
    with factory() as session:
        report = demo_seed.seed(session, data_dir=tmp_path, demo_password=PW, admin_password=APW, personas=10)
    assert report.case_name.startswith("[FICTICIO]")
    from sqlalchemy import select

    from aleph.core.models import Case

    with factory() as session:
        case = session.execute(select(Case).where(Case.id == report.case_id)).scalars().one()
        assert "100 % ficticios" in case.description and "No corresponde a personas" in case.description
    engine.dispose()


def test_determinismo_entre_bases_limpias(tmp_path):
    prints = []
    for name in ("primera", "segunda"):
        engine, factory = _database("sqlite:///:memory:")
        with factory() as session:
            prints.append(_fingerprint(demo_seed.seed(session, data_dir=tmp_path / name, demo_password=PW,
                                                      admin_password=APW, personas=10)))
        engine.dispose()
    assert prints[0] == prints[1]


def test_contrasenas_generadas_solo_cuando_el_script_las_genera(tmp_path, monkeypatch):
    monkeypatch.delenv("ALEPH_DEMO_PASSWORD", raising=False)
    monkeypatch.delenv("ALEPH_ADMIN_PASSWORD", raising=False)
    engine, factory = _database("sqlite:///:memory:")
    with factory() as session:
        report = demo_seed.seed(session, data_dir=tmp_path, personas=10)
    assert set(report.generated_passwords) == {"admin", "__demo__"}
    assert all(len(value) >= 16 for value in report.generated_passwords.values())
    engine.dispose()


def test_cli_no_imprime_las_contrasenas_que_se_pasaron(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ALEPH_DEMO_PASSWORD", raising=False)
    monkeypatch.delenv("ALEPH_ADMIN_PASSWORD", raising=False)
    argv = ["--database-url", f"sqlite:///{(tmp_path / 'demo.db').as_posix()}", "--data-dir",
            str(tmp_path / "data"), "--password", PW, "--admin-password", APW, "--personas", "10"]
    assert demo_seed.main(argv) == 0
    out = capsys.readouterr().out
    assert "Caso de demostración creado" in out and "Tiempo total" in out
    assert PW not in out and APW not in out and "Contraseñas generadas" not in out
    assert demo_seed.main(argv) == 0
    assert "ya existe" in capsys.readouterr().out


def test_cli_imprime_solo_las_generadas(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ALEPH_DEMO_PASSWORD", raising=False)
    monkeypatch.delenv("ALEPH_ADMIN_PASSWORD", raising=False)
    argv = ["--database-url", f"sqlite:///{(tmp_path / 'demo.db').as_posix()}", "--data-dir",
            str(tmp_path / "data"), "--personas", "10"]
    assert demo_seed.main(argv) == 0
    out = capsys.readouterr().out
    assert "Contraseñas generadas por el script" in out
    generated = re.findall(r": ([A-Za-z0-9_\-]{16,})$", out, flags=re.MULTILINE)
    assert len(generated) == 2  # admin y usuarios de demo, una contraseña cada uno


def test_cli_rechaza_una_contrasena_demasiado_corta(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ALEPH_DEMO_PASSWORD", raising=False)
    argv = ["--database-url", f"sqlite:///{(tmp_path / 'demo.db').as_posix()}", "--data-dir",
            str(tmp_path / "data"), "--password", "corta", "--admin-password", APW]
    assert demo_seed.main(argv) == 1
    assert "no es válida" in capsys.readouterr().err


def test_estado_cuenta_administradores_para_el_arranque(tmp_path):
    """Los scripts de arranque usan este conteo para decidir si corren el bootstrap."""
    from aleph.api.bootstrap import create_admin
    from aleph.demo.estado import count_admins

    url = f"sqlite:///{(tmp_path / 'estado.db').as_posix()}"
    assert count_admins(url) == 0  # base nueva: sin tabla de usuarios
    engine, factory = _database(url)
    with factory() as session:
        create_admin(session, "admin", APW)
        session.commit()
    engine.dispose()
    assert count_admins(url) == 1
