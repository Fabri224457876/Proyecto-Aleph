import json
from datetime import UTC, datetime

from aleph.core.schemas import EntityRecord
from aleph.cti.misp import export_misp
from aleph.cti.models import CtiCase, TechniqueScore

CREATED = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
MD5 = "d41d8cd98f00b204e9800998ecf8427e"
ETH = "0x52908400098527886e0f7030069857d2e4169ee7"


def _case(entities, **overrides):
    base = dict(case_id="caso-misp-001", name="Caso MISP", description="Descripción",
                tlp="amber", created_at=CREATED, entities=entities, relations=[],
                techniques=[TechniqueScore(technique_id="T1059.001", score=70)])
    base.update(overrides)
    return CtiCase(**base)


def _attrs(export):
    return {a["value"]: a for a in export.document["Event"]["Attribute"]}


def test_evento_estructura_basica_y_json_valido():
    case = _case([EntityRecord(type="domain", label="evil.example.com", ref="d1",
                               props={"malicious": True})])
    export = export_misp(case)
    event = export.document["Event"]
    assert event["info"] == "Caso MISP"
    assert event["date"] == "2026-10-07"
    assert event["published"] is False
    assert event["distribution"] == "0"
    json.loads(export.to_json())  # JSON válido


def test_tipos_y_categorias_segun_describe_types():
    case = _case([
        EntityRecord(type="ip", label="203.0.113.5", ref="i1",
                     props={"value": "203.0.113.5"}),
        EntityRecord(type="domain", label="example.org", ref="d1", props={}),
        EntityRecord(type="url", label="https://example.org/a", ref="u1", props={}),
        EntityRecord(type="hash", label=MD5, ref="h1",
                     props={"algorithm": "MD5", "value": MD5}),
        EntityRecord(type="hash", label=SHA256, ref="h2",
                     props={"algorithm": "SHA-256", "value": SHA256}),
        EntityRecord(type="vulnerability", label="CVE-2021-44228", ref="v1", props={}),
        EntityRecord(type="wallet", label="1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", ref="w1",
                     props={"currency": "BTC", "address": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"}),
        EntityRecord(type="wallet", label=ETH, ref="w2",
                     props={"currency": "ETH", "address": ETH}),
        EntityRecord(type="email", label="ops@example.com", ref="e1", props={}),
    ])
    attrs = _attrs(export_misp(case))
    assert (attrs["203.0.113.5"]["type"], attrs["203.0.113.5"]["category"]) == (
        "ip-dst", "Network activity")
    assert (attrs["example.org"]["type"], attrs["example.org"]["category"]) == (
        "domain", "Network activity")
    assert attrs["https://example.org/a"]["type"] == "url"
    assert attrs[MD5]["type"] == "md5" and attrs[MD5]["category"] == "Payload delivery"
    assert attrs[SHA256]["type"] == "sha256"
    assert (attrs["CVE-2021-44228"]["type"], attrs["CVE-2021-44228"]["category"]) == (
        "vulnerability", "External analysis")
    assert (attrs["1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"]["type"],
            attrs["1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"]["category"]) == ("btc", "Financial fraud")
    eth = attrs[ETH]
    assert eth["type"] == "other" and "ETH" in eth["comment"]  # MISP no tiene tipo ETH
    assert (attrs["ops@example.com"]["type"], attrs["ops@example.com"]["category"]) == (
        "email", "Social network")


def test_to_ids_solo_con_indicadores_maliciosos():
    case = _case([
        EntityRecord(type="domain", label="evil.example.org", ref="d1",
                     props={"malicious": True}),
        EntityRecord(type="domain", label="sin-confirmar.example.org", ref="d2", props={}),
    ])
    attrs = _attrs(export_misp(case))
    assert attrs["evil.example.org"]["to_ids"] is True
    assert attrs["sin-confirmar.example.org"]["to_ids"] is False


def test_tags_tlp_por_atributo_y_galaxia_attack():
    case = _case([
        EntityRecord(type="domain", label="example.org", ref="d1",
                     props={"tlp": "green"}),
        EntityRecord(type="domain", label="example.net", ref="d2", props={}),
    ])
    export = export_misp(case)
    attrs = _attrs(export)
    assert attrs["example.org"]["Tag"] == [{"name": "tlp:green"}]
    assert attrs["example.net"]["Tag"] == [{"name": "tlp:amber"}]
    event_tags = [t["name"] for t in export.document["Event"]["Tag"]]
    assert event_tags[0] == "tlp:amber"  # el evento lleva el más restrictivo de su contenido
    assert 'misp-galaxy:mitre-attack-pattern="PowerShell - T1059.001"' in event_tags


def test_filtro_tlp_quita_atributos_y_oculta_metadatos_del_caso():
    case = _case([
        EntityRecord(type="domain", label="green.example.org", ref="d1",
                     props={"tlp": "green"}),
        EntityRecord(type="domain", label="amber.example.org", ref="d2", props={}),
    ])
    export = export_misp(case, max_tlp="green")
    values = set(_attrs(export))
    assert values == {"green.example.org"}
    assert export.document["Event"]["info"] == "Caso con metadatos ocultos por TLP"
    assert [t["name"] for t in export.document["Event"]["Tag"]] == ["tlp:green"]
    assert not any(a["type"] == "comment" for a in export.document["Event"]["Attribute"])
    assert any("supera el máximo" in s for s in export.skipped)


def test_uuids_deterministas():
    case = _case([EntityRecord(type="domain", label="example.org", ref="d1", props={})])
    first = export_misp(case).to_json()
    assert export_misp(case).to_json() == first
