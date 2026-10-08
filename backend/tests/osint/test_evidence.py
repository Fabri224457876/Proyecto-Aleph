import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aleph.osint.evidence import (
    EVIDENCE_VERSION,
    GENESIS,
    MANIFEST_NAME,
    PAYLOAD_NAME,
    EvidenceError,
    EvidenceIntegrityError,
    add_custody_event,
    load_manifest,
    preserve_evidence,
    verify_evidence,
)

CONTENT = "Comunicado de prensa de prueba (texto sintético).\n".encode()
SOURCE = "https://example.com/comunicado"
FIXED_ID = "0123456789abcdef0123456789abcdef"


def _clock(*times: datetime):
    queue = list(times)

    def now():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return now


T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 7, 9, 5, 0, tzinfo=timezone(timedelta(hours=-3)))  # 12:05 UTC, entre T0 y T2
T2 = datetime(2026, 10, 7, 12, 9, 30, tzinfo=UTC)


def _preserve(tmp_path: Path, **kwargs):
    defaults = {"source_url": SOURCE, "collected_by": "analista-01", "storage_dir": tmp_path,
                "description": "captura de prueba", "filename": "comunicado.txt", "evidence_id": FIXED_ID,
                "now": _clock(T0)}
    defaults.update(kwargs)
    return preserve_evidence(CONTENT, **defaults)


def _item(tmp_path: Path) -> Path:
    return tmp_path / FIXED_ID


def test_preserve_writes_payload_hash_and_first_custody_event(tmp_path):
    manifest = _preserve(tmp_path)
    assert manifest.version == EVIDENCE_VERSION
    assert manifest.file.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert manifest.file.size == len(CONTENT)
    assert manifest.file.name == "comunicado.txt"
    assert (_item(tmp_path) / PAYLOAD_NAME).read_bytes() == CONTENT
    first = manifest.custody[0]
    assert (first.seq, first.by, first.action, first.prev_hash) == (1, "analista-01", "collected", GENESIS)
    assert first.at == "2026-10-07T12:00:00.000000+00:00"
    assert first.file_sha256 == manifest.file.sha256
    assert json.loads((_item(tmp_path) / MANIFEST_NAME).read_text(encoding="utf-8"))["source_url"] == SOURCE


def test_payload_is_read_only_and_cannot_be_overwritten(tmp_path):
    _preserve(tmp_path)
    with pytest.raises(EvidenceError):
        _preserve(tmp_path)  # mismo identificador: no se sobrescribe
    with pytest.raises(PermissionError):
        (_item(tmp_path) / PAYLOAD_NAME).open("r+b")  # en Windows el atributo de solo lectura lo impide


def test_timestamps_are_utc_even_when_clock_is_not(tmp_path):
    manifest = _preserve(tmp_path, now=_clock(T1))
    assert manifest.custody[0].at == "2026-10-07T12:05:00.000000+00:00"


def test_fresh_evidence_verifies_ok(tmp_path):
    _preserve(tmp_path)
    result = verify_evidence(_item(tmp_path))
    assert result.ok is True
    assert result.problems == []
    assert result.events == 1
    assert result.sha256_actual == hashlib.sha256(CONTENT).hexdigest()


def test_tampered_payload_is_detected(tmp_path):
    _preserve(tmp_path)
    payload = _item(tmp_path) / PAYLOAD_NAME
    payload.chmod(0o666)
    payload.write_bytes(CONTENT.replace(b"prueba", b"PRUEBA"))
    result = verify_evidence(_item(tmp_path))
    assert result.ok is False
    assert any("SHA-256 del archivo" in p for p in result.problems)


def test_edited_custody_event_breaks_its_hash(tmp_path):
    _preserve(tmp_path)
    add_custody_event(_item(tmp_path), by="revisor-02", action="reviewed", now=_clock(T2))
    manifest_path = _item(tmp_path) / MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["custody"][1]["by"] = "otro-usuario"  # alterar quién hizo la revisión
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_evidence(_item(tmp_path))
    assert result.ok is False
    assert any("evento 2: el hash no coincide" in p for p in result.problems)


def test_deleted_middle_event_breaks_the_chain(tmp_path):
    _preserve(tmp_path)
    add_custody_event(_item(tmp_path), by="revisor-02", action="reviewed", now=_clock(T1))
    add_custody_event(_item(tmp_path), by="auditor-03", action="exported", now=_clock(T2))
    manifest_path = _item(tmp_path) / MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    del data["custody"][1]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_evidence(_item(tmp_path))
    assert result.ok is False
    assert any("número de secuencia" in p for p in result.problems)
    assert any("enlace con el evento anterior" in p for p in result.problems)


def test_altered_manifest_file_hash_is_detected(tmp_path):
    _preserve(tmp_path)
    manifest_path = _item(tmp_path) / MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["file"]["sha256"] = "0" * 64  # fingir que el archivo es otro
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_evidence(_item(tmp_path))
    assert result.ok is False
    assert any("difiere del manifiesto" in p for p in result.problems)


def test_custody_events_chain_and_verify(tmp_path):
    _preserve(tmp_path)
    manifest = add_custody_event(_item(tmp_path), by="revisor-02", action="reviewed",
                                 detail="verificado contra la captura", now=_clock(T1))
    manifest = add_custody_event(_item(tmp_path), by="auditor-03", action="exported", now=_clock(T2))
    assert [e.seq for e in manifest.custody] == [1, 2, 3]
    assert manifest.custody[1].prev_hash == manifest.custody[0].hash
    assert manifest.custody[2].prev_hash == manifest.custody[1].hash
    assert verify_evidence(_item(tmp_path)).ok is True
    assert load_manifest(_item(tmp_path)).custody[2].by == "auditor-03"


def test_custody_refuses_to_extend_a_tampered_evidence(tmp_path):
    _preserve(tmp_path)
    payload = _item(tmp_path) / PAYLOAD_NAME
    payload.chmod(0o666)
    payload.write_bytes(b"otro contenido")
    with pytest.raises(EvidenceIntegrityError):
        add_custody_event(_item(tmp_path), by="revisor", action="reviewed")


def test_timestamp_going_backwards_is_detected(tmp_path):
    _preserve(tmp_path)
    add_custody_event(_item(tmp_path), by="revisor", action="reviewed", now=_clock(T2))
    manifest_path = _item(tmp_path) / MANIFEST_NAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["custody"][1]["at"] = "2026-10-07T11:00:00.000000+00:00"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_evidence(_item(tmp_path))
    assert result.ok is False
    assert any("evento 2: el hash no coincide" in p for p in result.problems)  # el sello forma parte del hash
    assert any("evento 2: el sello de tiempo retrocede" in p for p in result.problems)


def test_missing_payload_or_manifest_is_reported(tmp_path):
    _preserve(tmp_path)
    (_item(tmp_path) / PAYLOAD_NAME).chmod(0o666)  # en Windows, el atributo de solo lectura impide borrar
    (_item(tmp_path) / PAYLOAD_NAME).unlink()
    assert "falta el archivo" in " ".join(verify_evidence(_item(tmp_path)).problems)
    (_item(tmp_path) / MANIFEST_NAME).unlink()
    result = verify_evidence(_item(tmp_path))
    assert result.ok is False
    assert result.problems


def test_invalid_arguments_are_rejected(tmp_path):
    with pytest.raises(EvidenceError):
        preserve_evidence(b"", source_url=SOURCE, collected_by="x", storage_dir=tmp_path)
    with pytest.raises(EvidenceError):
        preserve_evidence(CONTENT, source_url="", collected_by="x", storage_dir=tmp_path)
    with pytest.raises(EvidenceError):
        preserve_evidence(CONTENT, source_url=SOURCE, collected_by="x", storage_dir=tmp_path,
                          evidence_id="../../escape")
    with pytest.raises(EvidenceError):
        preserve_evidence(CONTENT, source_url=SOURCE, collected_by="x", storage_dir=tmp_path,
                          now=lambda: datetime(2026, 1, 1))  # noqa: DTZ001 - sin zona horaria, a propósito
    assert not any(p.name == "escape" for p in tmp_path.parent.iterdir())


def test_filename_is_sanitized(tmp_path):
    manifest = _preserve(tmp_path, filename="../../etc/passwd;rm -rf")
    assert "/" not in manifest.file.name and ";" not in manifest.file.name
    assert (_item(tmp_path) / PAYLOAD_NAME).exists()
