"""Mapeo a ATT&CK y búsqueda semántica en memoria."""

import httpx
import numpy as np
import pytest

from aleph.funes.attack_map import Technique, map_techniques
from aleph.funes.client import LLMError
from aleph.funes.embed import SemanticIndex, cosine_similarity, embed_texts, normalize_rows

TECHNIQUES = [
    Technique(id="T1566", name="Phishing"),
    Technique(id="T1566.001", name="Spearphishing Attachment"),
    Technique(id="T1059.001", name="PowerShell"),
    Technique(id="T1486", name="Data Encrypted for Impact"),
    Technique(id="t1078", name="Valid Accounts"),
]
BEHAVIOR = (
    "El actor envió correos con un adjunto de Word malicioso a empleados del área contable. "
    "Al abrirlo se ejecutaba un script de PowerShell que descargaba la carga útil. "
    "Días después cifró los servidores de archivos y dejó una nota de rescate."
)


# --- ATT&CK --------------------------------------------------------------------------------


async def test_candidates_with_quote_and_valid_ids_only(make_client):
    reply = {"tecnicas": [
        {"id": "T1566.001", "cita": "envió correos con un adjunto de Word malicioso",
         "motivo": "Adjunto malicioso por correo dirigido.", "confianza": 0.85},
        {"id": "t1059.001 (PowerShell)", "cita": "se ejecutaba un script de PowerShell", "confianza": "alta"},
        {"id": "T1486", "cita": "cifró los servidores de archivos", "confianza": 1.0},
        {"id": "T9999", "cita": "dejó una nota de rescate", "confianza": 0.9},  # id inexistente
        {"id": "T1490", "cita": "cifró los servidores de archivos"},  # real en ATT&CK, no en la lista
        {"id": "T1078", "cita": "usó credenciales robadas del administrador"},  # cita inventada
        {"id": "T1566", "cita": ""},  # sin cita
        {"id": "Phishing", "cita": "envió correos con un adjunto de Word malicioso"},  # nombre, no id
    ]}
    client, server = make_client([reply])
    result = await map_techniques(BEHAVIOR, TECHNIQUES, client)
    assert [(c.id, c.name) for c in result.candidates] == [
        ("T1486", "Data Encrypted for Impact"), ("T1059.001", "PowerShell"),
        ("T1566.001", "Spearphishing Attachment")]
    assert [c.confidence for c in result.candidates] == [0.9, 0.85, 0.85]  # tope 0,9
    for candidate in result.candidates:
        assert candidate.estado == "propuesta"
        for evidence in candidate.evidence:
            assert BEHAVIOR[evidence.offset:evidence.offset + len(evidence.cita)] == evidence.cita
    reasons = [d["reason"] for d in result.discarded]
    assert reasons.count("id fuera de la lista de técnicas válidas") == 3
    assert reasons.count("la cita no aparece en el texto fuente") == 2
    system = server.system()
    assert "T1566.001 — Spearphishing Attachment" in system and "T1078 — Valid Accounts" in system
    assert "<<<DOCUMENTO" in server.user()


async def test_same_technique_twice_merges_evidence(make_client):
    reply = {"tecnicas": [
        {"id": "T1486", "cita": "cifró los servidores de archivos", "confianza": 0.6},
        {"id": "T1486", "cita": "dejó una nota de rescate", "confianza": 0.8},
        {"id": "T1486", "cita": "cifró los servidores de archivos", "confianza": 0.7},
    ]}
    client, _ = make_client([reply])
    result = await map_techniques(BEHAVIOR, TECHNIQUES, client)
    assert len(result.candidates) == 1
    assert result.candidates[0].confidence == 0.8 and len(result.candidates[0].evidence) == 2


async def test_no_techniques_or_empty_text_makes_no_calls(make_client):
    client, server = make_client()
    assert (await map_techniques(BEHAVIOR, [], client)).candidates == []
    assert (await map_techniques("   ", TECHNIQUES, client)).candidates == []
    assert server.chat_requests == []


async def test_model_failure_is_a_warning(make_client):
    client, _ = make_client(default="no sé", max_retries=0)
    result = await map_techniques(BEHAVIOR, TECHNIQUES, client)
    assert result.candidates == [] and len(result.warnings) == 1


def _big_catalog():
    catalog = [Technique(id=f"T{2000 + i}", name=f"Relleno genérico {i}") for i in range(60)]
    catalog.insert(30, Technique(id="T1059.001", name="PowerShell",
                                 description="ejecutaba un script de PowerShell"))
    return catalog


async def test_large_catalog_is_shortlisted_with_embeddings(make_client):
    client, server = make_client(default={"tecnicas": [
        {"id": "T1059.001", "cita": "se ejecutaba un script de PowerShell"},
        {"id": "T2059", "cita": "dejó una nota de rescate"}]})
    result = await map_techniques(BEHAVIOR, _big_catalog(), client, max_in_prompt=5)
    assert len(server.chat_requests) == 1 and server.embed_requests
    listed = [line for line in server.system().splitlines() if " — " in line and line.startswith("T")]
    assert len(listed) == 5 and "T1059.001 — PowerShell" in listed
    # T2059 es válida en el catálogo pero no estaba en la tanda que vio el modelo: se descarta.
    assert [c.id for c in result.candidates] == ["T1059.001"]
    assert result.discarded[0]["id"] == "T2059"


async def test_large_catalog_falls_back_to_batches_without_embeddings(make_client):
    def reply(body):
        system = body["messages"][0]["content"]
        if "T1059.001 — PowerShell" in system:
            return {"tecnicas": [{"id": "T1059.001", "cita": "se ejecutaba un script de PowerShell"}]}
        return {"tecnicas": []}

    client, server = make_client(default=reply)
    server.embed_handler = lambda body: httpx.Response(500, text="caído")
    client.max_retries = 0
    result = await map_techniques(BEHAVIOR, _big_catalog(), client, max_in_prompt=20)
    assert len(server.chat_requests) == 4  # 61 técnicas en tandas de 20
    assert [c.id for c in result.candidates] == ["T1059.001"]
    assert "por tandas" in result.warnings[0]


# --- Embeddings --------------------------------------------------------------------------------


def test_normalize_and_cosine():
    matrix = normalize_rows(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert matrix.tolist() == [[pytest.approx(0.6), pytest.approx(0.8)], [0.0, 0.0]]
    sims = cosine_similarity(np.array([[1.0, 0.0]]), np.array([[2.0, 0.0], [0.0, 5.0], [-1.0, 0.0]]))
    assert sims.tolist() == [[pytest.approx(1.0), pytest.approx(0.0), pytest.approx(-1.0)]]


async def test_embed_texts_batches_and_keeps_order(make_client):
    client, server = make_client()
    texts = [f"texto número {i}" for i in range(7)] + ["", "   "]
    matrix = await embed_texts(client, texts, batch_size=3)
    assert matrix.shape == (9, 64) and matrix.dtype == np.float32
    assert [len(r["input"]) for r in server.embed_requests] == [3, 3, 1]
    assert np.allclose(np.linalg.norm(matrix[:7], axis=1), 1.0)
    assert not matrix[7:].any()  # los vacíos no se mandan y quedan en cero
    single = await embed_texts(client, ["texto número 4"])
    assert np.allclose(single[0], matrix[4])


async def test_embed_texts_edge_cases(make_client):
    client, server = make_client()
    assert (await embed_texts(client, [])).shape == (0, 0)
    assert (await embed_texts(client, ["", " "])).shape == (2, 0) and server.embed_requests == []
    await embed_texts(client, ["x" * 50_000], max_chars=100)
    assert len(server.embed_requests[0]["input"][0]) == 100
    with pytest.raises(ValueError):
        await embed_texts(client, ["a"], batch_size=0)
    server.embed_handler = lambda body: httpx.Response(200, json={"data": [
        {"index": i, "embedding": [1.0] * (i + 1)} for i in range(len(body["input"]))]})
    with pytest.raises(LLMError, match="dimensiones distintas"):
        await embed_texts(client, ["a", "b"])


async def test_semantic_search_ranks_by_similarity(make_client):
    client, _ = make_client()
    docs = {
        "d1": "el auto gris tenía la patente adulterada",
        "d2": "transferencias bancarias a una cuenta de la cooperativa",
        "d3": "reunión en el club del barrio con dirigentes",
        "d4": "",
    }
    index = SemanticIndex()
    await index.add_texts(client, list(docs), list(docs.values()),
                          [{"texto": t} for t in docs.values()])
    assert len(index) == 4
    hits = await index.search(client, "patente del auto", k=2)
    assert hits[0].id == "d1" and hits[0].payload["texto"] == docs["d1"]
    assert hits[0].score > hits[1].score and len(hits) == 2
    bank = await index.search(client, "cuenta de la cooperativa", k=10)
    assert bank[0].id == "d2" and len(bank) == 4 and bank[-1].score <= bank[0].score
    assert [h.id for h in await index.search(client, "cuenta cooperativa", k=10, min_score=0.3)] == ["d2"]
    assert await index.search(client, "   ") == []


def test_index_vector_api():
    index = SemanticIndex()
    assert index.search_vector(np.array([1.0, 0.0])) == []
    index.add(["a", "b"], np.array([[1.0, 0.0], [0.0, 2.0]]))
    index.add(["c"], np.array([[1.0, 1.0]]), [{"n": 3}])
    hits = index.search_vector(np.array([1.0, 0.1]), k=3)
    assert [h.id for h in hits] == ["a", "c", "b"] and hits[1].payload == {"n": 3}
    assert hits[0].score == pytest.approx(0.995, abs=1e-3)
    assert index.search_vector(np.array([1.0, 0.0]), k=0) == []
    with pytest.raises(ValueError):
        index.add(["d"], np.array([[1.0, 2.0, 3.0]]))
    with pytest.raises(ValueError):
        index.add(["d", "e"], np.array([[1.0, 2.0]]))
    with pytest.raises(ValueError):
        index.search_vector(np.array([1.0, 2.0, 3.0]))
