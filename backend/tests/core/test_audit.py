from sqlalchemy.orm import sessionmaker

from aleph.core import audit
from aleph.core.db import init_db, make_engine
from aleph.core.models import AuditEvent


def _session():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    return sessionmaker(bind=engine)()


def test_chain_verifies_and_detects_tampering():
    s = _session()
    for i in range(5):
        audit.record(s, "case.view", user_id=1, case_id=1, target=f"entity:{i}", detail={"n": i})
    s.commit()
    assert audit.verify_chain(s) == (True, None)

    ev = s.get(AuditEvent, 3)
    ev.detail = {"n": 99}
    s.commit()
    assert audit.verify_chain(s) == (False, 3)


def test_chain_detects_deletion():
    s = _session()
    for i in range(4):
        audit.record(s, "x", detail={"n": i})
    s.commit()
    s.delete(s.get(AuditEvent, 2))
    s.commit()
    assert audit.verify_chain(s) == (False, 3)


def test_concurrent_writers_do_not_fork_chain(tmp_path):
    import threading

    engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    errors = []

    def writer(n):
        try:
            for i in range(10):
                with Session() as s:
                    audit.record(s, "x", detail={"w": n, "i": i})
                    s.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with Session() as s:
        assert audit.verify_chain(s) == (True, None)
        assert s.query(AuditEvent).count() + len(errors) == 60
