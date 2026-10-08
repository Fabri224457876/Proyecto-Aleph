import json

import pydantic
import pytest

from aleph.cti.attack import load_index, navigator_layer
from aleph.cti.build_attack_index import build_index, main
from aleph.cti.models import TechniqueScore


def test_indice_embebido_version_y_conteos():
    idx = load_index()
    assert idx.version == "19.2"
    assert idx.domain == "enterprise-attack"
    assert idx.counts == {"tactics": 15, "techniques": 233, "subtechniques": 476,
                          "deprecated": 12}
    assert len(idx) == 709
    assert len(idx.tactics()) == 15


def test_busqueda_por_id_exacto_y_normalizado():
    idx = load_index()
    tech = idx.get("t1059.001")
    assert tech is not None
    assert tech.name == "PowerShell"
    assert tech.parent == "T1059"
    assert tech.stix_id.startswith("attack-pattern--")
    assert idx.get("T9999") is None


def test_busqueda_por_prefijo_de_id_incluye_subtecnicas():
    ids = [t.id for t in load_index().search("T1059")]
    assert ids[0] == "T1059"
    assert "T1059.001" in ids


def test_busqueda_por_texto_prioriza_coincidencia_exacta():
    results = load_index().search("powershell")
    assert results[0].id == "T1059.001"


def test_busqueda_por_tactica_y_subtecnicas_del_padre():
    idx = load_index()
    assert "privilege-escalation" in idx.get("T1055.011").tactics
    subs = idx.subtechniques("T1059")
    assert subs and all(s.parent == "T1059" for s in subs)
    assert idx.tactic("execution").id == "TA0002"


def test_capa_navigator_estructura_4_5():
    entries = [
        TechniqueScore(technique_id="T1059.001", score=80, comment="PowerShell visto"),
        TechniqueScore(technique_id="T1566.001", score=None, comment=""),
    ]
    layer = navigator_layer(entries, name="Caso sintético", description="demo")
    assert layer["name"] == "Caso sintético"
    assert layer["domain"] == "enterprise-attack"
    assert layer["versions"] == {"attack": "19.2", "navigator": "4.9.0", "layer": "4.5"}
    assert layer["gradient"]["minValue"] < layer["gradient"]["maxValue"]
    assert len(layer["gradient"]["colors"]) >= 2
    first, second = layer["techniques"]
    assert first["techniqueID"] == "T1059.001"
    assert first["score"] == 80
    assert first["comment"] == "PowerShell visto"
    assert first["enabled"] is True
    assert "score" not in second  # sin puntaje: "unscored" en Navigator
    json.dumps(layer)  # serializable tal cual


def test_capa_rechaza_ids_inexistentes_sin_inventar():
    with pytest.raises(ValueError, match="T9999"):
        navigator_layer([TechniqueScore(technique_id="T9999")], name="x")


def test_capa_rechaza_tecnicas_repetidas_y_nombre_vacio():
    dup = [TechniqueScore(technique_id="T1059"), TechniqueScore(technique_id="t1059")]
    with pytest.raises(ValueError, match="repetida"):
        navigator_layer(dup, name="x")
    with pytest.raises(ValueError):
        navigator_layer([], name="   ")


def test_puntaje_fuera_de_rango_no_se_acepta():
    with pytest.raises(pydantic.ValidationError):
        TechniqueScore(technique_id="T1059", score=120)
    with pytest.raises(pydantic.ValidationError):
        TechniqueScore(technique_id="1059")


PERSISTENCE = {"kill_chain_name": "mitre-attack", "phase_name": "persistence"}


def _synthetic_bundle():
    def ref(ext_id, url=""):
        return {"source_name": "mitre-attack", "external_id": ext_id, "url": url}

    return {
        "type": "bundle",
        "id": "bundle--00000000-0000-4000-8000-000000000001",
        "objects": [
            {"type": "x-mitre-collection", "name": "Enterprise ATT&CK",
             "x_mitre_version": "1.0", "x_mitre_domains": ["enterprise-attack"]},
            {"type": "x-mitre-tactic", "name": "Execution", "x_mitre_shortname": "execution",
             "external_references": [ref("TA0002", "https://attack.mitre.org/tactics/TA0002")]},
            {"type": "x-mitre-tactic", "name": "Persistence", "x_mitre_shortname": "persistence",
             "external_references": [ref("TA0003", "https://attack.mitre.org/tactics/TA0003")]},
            {"type": "attack-pattern", "id": "attack-pattern--aaaa0000-0000-4000-8000-000000000001",
             "name": "Command and Scripting Interpreter",
             "x_mitre_domains": ["enterprise-attack"],
             "external_references": [ref("T1059", "https://attack.mitre.org/techniques/T1059")],
             "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "execution"}]},
            {"type": "attack-pattern", "id": "attack-pattern--aaaa0000-0000-4000-8000-000000000002",
             "name": "PowerShell", "x_mitre_is_subtechnique": True,
             "x_mitre_domains": ["enterprise-attack"],
             "external_references": [ref("T1059.001", "https://attack.mitre.org/techniques/T1059/001")],
             "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "execution"}]},
            {"type": "attack-pattern", "id": "attack-pattern--aaaa0000-0000-4000-8000-000000000003",
             "name": "Vieja técnica", "revoked": True, "x_mitre_domains": ["enterprise-attack"],
             "external_references": [ref("T1001", "https://attack.mitre.org/techniques/T1001")],
             "kill_chain_phases": [PERSISTENCE]},
            {"type": "attack-pattern", "id": "attack-pattern--aaaa0000-0000-4000-8000-000000000004",
             "name": "Técnica deprecada", "x_mitre_deprecated": True,
             "x_mitre_domains": ["enterprise-attack"],
             "external_references": [ref("T1002", "https://attack.mitre.org/techniques/T1002")],
             "kill_chain_phases": [PERSISTENCE]},
        ],
    }


def test_build_index_sobre_bundle_sintetico_excluye_revocadas():
    index = build_index(_synthetic_bundle(), source="prueba")
    assert index["attack_version"] == "1.0"
    assert index["counts"] == {"tactics": 2, "techniques": 2, "subtechniques": 1,
                               "deprecated": 1}
    ids = [t["id"] for t in index["techniques"]]
    assert ids == ["T1002", "T1059", "T1059.001"]  # T1001 (revocada) no entra
    sub = next(t for t in index["techniques"] if t["id"] == "T1059.001")
    assert sub["parent"] == "T1059"
    assert sub["tactics"] == ["execution"]


def test_build_index_rechaza_bundle_sin_coleccion_enterprise():
    bundle = _synthetic_bundle()
    bundle["objects"] = [o for o in bundle["objects"] if o["type"] != "x-mitre-collection"]
    with pytest.raises(ValueError, match="colección Enterprise"):
        build_index(bundle, source="prueba")


def test_script_de_regeneracion_escribe_el_indice(tmp_path):
    src = tmp_path / "enterprise-attack.json"
    src.write_text(json.dumps(_synthetic_bundle()), encoding="utf-8")
    out = tmp_path / "salida" / "attack.json"
    assert main(["--input", str(src), "--output", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["attack_version"] == "1.0"
    assert written["counts"]["techniques"] == 2
