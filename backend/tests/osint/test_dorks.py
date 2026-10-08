import re
from urllib.parse import parse_qs, urlsplit

import pytest

from aleph.osint.dorks import build_dorks, detect_target_type
from aleph.osint.errors import InvalidInputError


@pytest.mark.parametrize("target,expected", [
    ("example.com", "domain"),
    ("Ana Pérez", "name"),
    ("ana.perez_99", "username"),
    ("@ana_perez", "username"),
    ("ana@example.com", "email"),
])
def test_target_type_detection(target, expected):
    assert detect_target_type(target) == expected


def test_domain_dorks_are_categorized_and_have_encoded_search_urls():
    report = build_dorks("example.com")
    assert report.target_type == "domain"
    categories = {q.category for q in report.queries}
    assert {"subdominios", "documentos", "directorios", "accesos", "menciones"} <= categories
    documents = next(q for q in report.queries if q.category == "documentos")
    assert "site:example.com" in documents.query
    assert set(documents.search_urls) == {"google", "bing", "duckduckgo"}
    google_q = parse_qs(urlsplit(documents.search_urls["google"]).query)["q"][0]
    assert google_q == documents.query  # la URL codifica exactamente la consulta
    assert "https://www.bing.com/search?q=" in documents.search_urls["bing"]


def test_name_queries_strip_quotes_and_collapse_spaces():
    report = build_dorks('  Ana   "Pérez" ')
    assert report.target_type == "name"
    assert all('""' not in q.query for q in report.queries)
    assert any('"Ana Pérez"' in q.query for q in report.queries)


def test_username_and_email_targets():
    user = build_dorks("@ana_perez")
    assert user.target_type == "username"
    assert any('"ana_perez"' in q.query for q in user.queries)
    mail = build_dorks("Ana@Example.COM")
    assert mail.target_type == "email"
    assert all("ana@example.com" in q.query for q in mail.queries)


def test_explicit_target_type_overrides_detection():
    report = build_dorks("example", target_type="username")
    assert report.target_type == "username"


def test_no_queries_aim_at_credentials_or_secrets():
    forbidden = re.compile(r"password|passwd|api[_ -]?key|secret|token|filetype:(env|sql|log|bak|conf|ini)|"
                           r"DB_PASS|credential", re.IGNORECASE)
    for target in ["example.com", "Ana Pérez", "ana_perez", "ana@example.com"]:
        for query in build_dorks(target).queries:
            assert not forbidden.search(query.query), query.query


@pytest.mark.parametrize("bad", ["", "   ", "a" * 300, "tab\there.com", "ab"])
def test_invalid_targets_are_rejected(bad):
    with pytest.raises(InvalidInputError):
        build_dorks(bad)


def test_no_search_is_executed():
    # El módulo no importa clientes HTTP: construir consultas es solo texto
    from aleph.osint import dorks

    assert "httpx" not in vars(dorks)
