import copy
import json
from datetime import UTC, datetime

import pytest
import stix2
from stix2.exceptions import STIXError

from aleph.core.schemas import EntityRecord, RelationRecord
from aleph.cti.attack import load_index
from aleph.cti.models import CtiCase, TechniqueScore
from aleph.cti.stix import export_case, import_bundle
from aleph.cti.tlp import TLP2_EXTENSION_ID, TLP2_MARKINGS

CREATED = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
GREEN = TLP2_MARKINGS["green"].id
AMBER = TLP2_MARKINGS["amber"].id
CLEAR = TLP2_MARKINGS["clear"].id
SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
BTC = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"


def _entities():
    return [
        EntityRecord(type="person", label="Persona Ejemplo", ref="p1",
                     props={"threat_actor": True}),
        EntityRecord(type="account", label="ejemplo_user", ref="a1",
                     props={"platform": "bluesky", "handle": "ejemplo_user",
                            "platform_uid": "did:plc:ejemplo"}),
        EntityRecord(type="account", label="otro_user", ref="a2",
                     props={"platform": "bluesky", "handle": "otro_user"}),
        EntityRecord(type="domain", label="evil.example.com", ref="d1",
                     props={"value": "evil.example.com", "malicious": True}),
        EntityRecord(type="ip", label="192.0.2.45", ref="i1",
                     props={"value": "192.0.2.45", "version": 4, "scope": "documentation",
                            "routable": False, "malicious": True}),
        EntityRecord(type="ip", label="198.51.100.7", ref="i2",
                     props={"value": "198.51.100.7", "version": 4, "scope": "public",
                            "routable": True, "malicious": True, "tlp": "green"}),
        EntityRecord(type="hash", label=SHA256, ref="h1",
                     props={"algorithm": "SHA-256", "value": SHA256, "malicious": True}),
        EntityRecord(type="malware", label="Emotet", ref="m1", props={}),
        EntityRecord(type="vulnerability", label="CVE-2021-44228", ref="v1",
                     props={"cve_id": "CVE-2021-44228"}),
        EntityRecord(type="organization", label="ACME Corp", ref="o1", props={}),
        EntityRecord(type="location", label="Buenos Aires", ref="l1",
                     props={"country": "AR", "city": "Buenos Aires"}),
        EntityRecord(type="location", label="Puerto ficticio", ref="l2", props={}),
        EntityRecord(type="wallet", label=BTC, ref="w1",
                     props={"currency": "BTC", "address": BTC}),
        EntityRecord(type="event", label="Reunión sintética", ref="ev1", props={}),
        EntityRecord(type="email", label="ops@example.com", ref="e1",
                     props={"value": "ops@example.com"}),
        EntityRecord(type="phone", label="+5491100000000", ref="ph1", props={}),
        EntityRecord(type="document", label="Informe X", ref="doc1", props={}),
        EntityRecord(type="vehicle", label="AB123CD", ref="veh1", props={}),
        EntityRecord(type="url", label="https://evil.example.com/login.php", ref="u1",
                     props={"value": "https://evil.example.com/login.php"}),
    ]


def _relations():
    return [
        RelationRecord(src_ref="a1", dst_ref="a2", type="same_operator", confidence=0.72),
        RelationRecord(src_ref="p1", dst_ref="a1", type="uses"),
        RelationRecord(src_ref="m1", dst_ref="i1", type="communicates_with"),
        RelationRecord(src_ref="p1", dst_ref="o1", type="targets"),
        RelationRecord(src_ref="o1", dst_ref="l1", type="located_at"),
        RelationRecord(src_ref="d1", dst_ref="i2", type="resolves_to"),
    ]


def _case(**overrides):
    base = dict(
        case_id="caso-sintetico-001",
        name="Caso sintético",
        description="Caso de demostración con datos ficticios.",
        tlp="amber",
        created_at=CREATED,
        entities=_entities(),
        relations=_relations(),
        techniques=[TechniqueScore(technique_id="T1059.001", score=80,
                                   comment="PowerShell de la campaña")],
    )
    base.update(overrides)
    return CtiCase(**base)


def _objects(export):
    return json.loads(export.to_json())["objects"]


def _find(objects, kind, **match):
    found = [o for o in objects if o["type"] == kind
             and all(o.get(k) == v for k, v in match.items())]
    assert len(found) == 1, f"{kind} {match}: {len(found)} coincidencias"
    return found[0]


def _assert_no_dangling(objects):
    ids = {o["id"] for o in objects}
    for o in objects:
        if o["type"] == "relationship":
            assert o["source_ref"] in ids and o["target_ref"] in ids
        if o["type"] == "report":
            assert set(o["object_refs"]) <= ids
        for ref in o.get("object_marking_refs", []):
            assert ref in ids


def test_bundle_parsea_con_stix2_y_tiene_informe_del_caso():
    export = export_case(_case())
    assert export.requires_allow_custom is True  # hay wallet, teléfono, documento...
    bundle = stix2.parse(export.to_json(), allow_custom=True)
    assert bundle.type == "bundle"
    report = _find(_objects(export), "report")
    assert report["name"] == "Caso sintético"
    assert report["description"] == "Caso de demostración con datos ficticios."
    assert report["object_marking_refs"] == [AMBER]
    # STIX 2.1 exige milisegundos solo en created/modified; published los admite opcionales.
    assert report["created"] == "2026-10-07T12:00:00.000Z"
    assert report["modified"] == "2026-10-07T12:00:00.000Z"
    assert report["published"] == "2026-10-07T12:00:00Z"


def test_sin_objetos_personalizados_parsea_sin_allow_custom():
    case = _case(entities=[e for e in _entities() if e.type in ("domain", "ip", "malware")],
                 relations=[RelationRecord(src_ref="m1", dst_ref="i1",
                                           type="communicates_with")])
    export = export_case(case)
    assert export.requires_allow_custom is False
    stix2.parse(export.to_json())  # sin allow_custom


def test_referencias_a_objetos_personalizados_requieren_allow_custom_al_leer():
    export = export_case(_case())
    with pytest.raises(STIXError):
        stix2.parse(export.to_json())


def test_exportacion_determinista_y_sin_importar_el_orden_de_entrada():
    case = _case()
    first = export_case(case).to_json()
    assert export_case(case).to_json() == first
    shuffled = copy.deepcopy(case)
    shuffled.entities.reverse()
    shuffled.relations.reverse()
    assert export_case(shuffled).to_json() == first


def test_marcas_tlp2_oficiales_y_extension():
    objects = _objects(export_case(_case()))
    markings = [o for o in objects if o["type"] == "marking-definition"]
    assert {m["id"] for m in markings} == {AMBER, GREEN}
    for m in markings:
        assert "definition_type" not in m  # TLP 2.0 no usa definition_type
        level = m["extensions"][TLP2_EXTENSION_ID]["tlp_2_0"]
        assert m["id"] == TLP2_MARKINGS[level].id
        assert m["created"] == "2022-10-01T00:00:00.000Z"
    ext = _find(objects, "extension-definition")
    assert ext["id"] == TLP2_EXTENSION_ID
    assert ext["extension_types"] == ["property-extension"]
    assert _find(objects, "ipv4-addr", value="198.51.100.7")["object_marking_refs"] == [GREEN]


def test_filtro_tlp_no_exporta_objetos_mas_restrictivos_ni_deja_referencias_colgadas():
    export = export_case(_case(), max_tlp="green")
    objects = _objects(export)
    _assert_no_dangling(objects)
    assert not any(o["type"] == "report" for o in objects)  # el caso es amber
    assert all(ref in (GREEN, CLEAR) for o in objects for ref in o.get("object_marking_refs", []))
    values = {o.get("value") for o in objects if o["type"] == "ipv4-addr"}
    assert values == {"198.51.100.7"}
    assert any("supera el máximo" in s for s in export.skipped)
    assert not any(o["type"] == "relationship" and o["relationship_type"] == "located-at"
                   for o in objects)


def test_tipos_de_relacion_del_vocabulario_stix_y_fallback_related_to():
    objects = _objects(export_case(_case()))
    ip = _find(objects, "ipv4-addr", value="192.0.2.45")
    malware = _find(objects, "malware", name="Emotet")
    comm = next(o for o in objects if o["type"] == "relationship"
                and o["source_ref"] == malware["id"] and o["target_ref"] == ip["id"])
    assert comm["relationship_type"] == "communicates-with"

    location = _find(objects, "location", country="AR")
    org = _find(objects, "identity", name="ACME Corp")
    located = next(o for o in objects if o["type"] == "relationship"
                   and o["target_ref"] == location["id"])
    assert located["relationship_type"] == "located-at"
    assert located["source_ref"] == org["id"]

    domain = _find(objects, "domain-name", value="evil.example.com")
    resolves = next(o for o in objects if o["type"] == "relationship"
                    and o["source_ref"] == domain["id"])
    assert resolves["relationship_type"] == "related-to"
    assert resolves["labels"] == ["aleph-rel:resolves_to"]


def test_same_operator_es_hipotesis_no_identidad_confirmada():
    objects = _objects(export_case(_case()))
    accounts = [o for o in objects if o["type"] == "user-account"]
    assert sorted(a["account_login"] for a in accounts) == ["ejemplo_user", "otro_user"]
    rel = next(o for o in objects if o["type"] == "relationship"
               and "aleph-rel:same_operator" in o.get("labels", []))
    assert rel["relationship_type"] == "related-to"
    assert rel["confidence"] == 72
    assert "Hipótesis de mismo operador" in rel["description"]
    assert not any(o["type"] == "identity" and "ejemplo" in o.get("name", "").lower()
                   for o in objects)


def test_indicadores_solo_para_maliciosos_y_ruteables():
    export = export_case(_case())
    patterns = {o["pattern"] for o in _objects(export) if o["type"] == "indicator"}
    assert patterns == {
        "[domain-name:value = 'evil.example.com']",
        "[ipv4-addr:value = '198.51.100.7']",
        f"[file:hashes.'SHA-256' = '{SHA256}']",
    }
    assert any("no ruteable" in s for s in export.skipped)


def test_ida_y_vuelta_exportar_importar_conserva_entidades_relaciones_y_caso():
    case = _case()
    imported = import_bundle(export_case(case).to_json())

    assert {(e.type, e.label) for e in imported.entities} == {
        (e.type, e.label) for e in case.entities}

    label_of = {e.ref: (e.type, e.label) for e in imported.entities}
    original = {e.ref: (e.type, e.label) for e in case.entities}
    got = {(r.type, label_of[r.src_ref], label_of[r.dst_ref]) for r in imported.relations}
    want = {(r.type, original[r.src_ref], original[r.dst_ref]) for r in case.relations}
    assert got == want

    assert imported.case is not None
    assert (imported.case.name, imported.case.description) == (case.name, case.description)
    assert imported.case.tlp == "amber"
    assert imported.techniques == [TechniqueScore(technique_id="T1059.001", score=80,
                                                  comment="PowerShell de la campaña")]
    domain = next(e for e in imported.entities if e.type == "domain")
    assert domain.props["malicious"] is True and domain.props["tlp"] == "amber"
    green_ip = next(e for e in imported.entities if e.label == "198.51.100.7")
    assert green_ip.props["tlp"] == "green"


def test_import_de_bundle_con_tlp1_y_relacion_related_to():
    white = "marking-definition--613f2e26-407d-48c7-9eca-b8e91df99dc9"
    person = "identity--0b8a1a2e-1f3c-4d5e-8a9b-0c1d2e3f4a5b"
    ip = "ipv4-addr--7c9d0e1f-2a3b-4c5d-8e9f-0a1b2c3d4e5f"
    rel = "relationship--1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
    bundle = {
        "type": "bundle", "id": "bundle--2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
        "objects": [
            {"type": "marking-definition", "spec_version": "2.1", "id": white,
             "created": "2017-01-20T00:00:00.000Z", "definition_type": "tlp",
             "definition": {"tlp": "white"}, "name": "TLP:WHITE"},
            {"type": "identity", "spec_version": "2.1", "id": person,
             "created": "2024-01-01T00:00:00.000Z", "modified": "2024-01-01T00:00:00.000Z",
             "name": "Ana Ejemplo", "identity_class": "individual",
             "object_marking_refs": [white]},
            {"type": "ipv4-addr", "spec_version": "2.1", "id": ip, "value": "192.0.2.9"},
            {"type": "relationship", "spec_version": "2.1", "id": rel,
             "created": "2024-01-01T00:00:00.000Z", "modified": "2024-01-01T00:00:00.000Z",
             "relationship_type": "related-to", "source_ref": person, "target_ref": ip,
             "confidence": 60},
        ],
    }
    imported = import_bundle(json.dumps(bundle))
    (ana,) = [e for e in imported.entities if e.type == "person"]
    assert ana.props["tlp"] == "clear"  # TLP:WHITE de TLP 1.0 equivale a CLEAR
    (relation,) = imported.relations
    assert relation.type == "related_to"
    assert relation.confidence == pytest.approx(0.6)


def test_tecnicas_usan_el_id_oficial_de_attack():
    objects = _objects(export_case(_case()))
    technique = load_index().get("T1059.001")
    pattern = _find(objects, "attack-pattern", name="PowerShell")
    assert pattern["id"] == technique.stix_id
    assert pattern["external_references"][0]["external_id"] == "T1059.001"
    assert pattern["labels"] == ["aleph-score:80"]
    assert pattern["description"] == "PowerShell de la campaña"


def test_relacion_con_referencia_inexistente_es_error():
    bad = _case(relations=[RelationRecord(src_ref="nope", dst_ref="a1", type="uses")])
    with pytest.raises(ValueError, match="inexistente"):
        export_case(bad)


def test_wallet_es_sco_personalizado_declarado():
    objects = _objects(export_case(_case()))
    wallet = _find(objects, "x-aleph-wallet", address=BTC)
    assert wallet["id"].startswith("x-aleph-wallet--")
    assert wallet["currency"] == "BTC"
    report = _find(objects, "report")
    assert wallet["id"] in report["object_refs"]


def test_caso_sin_objetos_no_produce_bundle_invalido():
    export = export_case(_case(entities=[], relations=[], techniques=[]))
    assert export.bundle is None  # un bundle sin "objects" no es STIX válido
    assert export.skipped == ("informe del caso: no hay objetos para referenciar",)
    with pytest.raises(ValueError, match="No hay objetos"):
        export.to_json()


def test_filtro_tlp_que_deja_todo_afuera_no_produce_bundle():
    export = export_case(_case(), max_tlp="clear")
    assert export.bundle is None
    assert any("supera el máximo" in s for s in export.skipped)
