from aleph.core.schemas import ENTITY_TYPES
from aleph.cti.ioc import defang, extract_iocs, refang


def _by_type(result, kind):
    return [e for e in result.entities if e.type == kind]


def _reasons(result):
    return {(d.kind, d.value): d.reason for d in result.discarded}


def test_ip_publica_y_de_documentacion():
    res = extract_iocs("Conexión a 8.8.8.8 y a 192.0.2.10 desde el panel.")
    ips = {e.label: e for e in _by_type(res, "ip")}
    assert set(ips) == {"8.8.8.8", "192.0.2.10"}
    assert ips["8.8.8.8"].props["scope"] == "public"
    assert ips["8.8.8.8"].props["routable"] is True
    assert ips["192.0.2.10"].props["scope"] == "documentation"
    assert ips["192.0.2.10"].props["routable"] is False


def test_ip_privada_se_marca_y_no_se_descarta_en_silencio():
    res = extract_iocs("Host interno 10.1.2.3 en la VPN")
    (ip,) = _by_type(res, "ip")
    assert ip.label == "10.1.2.3"
    assert ip.props["scope"] == "private"
    assert ip.props["routable"] is False
    assert res.discarded == []


def test_ipv6_con_punto_final_de_oracion():
    res = extract_iocs("Ver 2001:4860:4860::8888. Tambien 2001:db8::1")
    values = {e.label: e.props["scope"] for e in _by_type(res, "ip")}
    assert values == {"2001:4860:4860::8888": "public", "2001:db8::1": "documentation"}


def test_versiones_de_software_se_descartan_con_motivo():
    text = "Actualizar a versión 1.2.3.4, la v5.6.7.8 y Apache/2.4.1.2 hoy."
    res = extract_iocs(text)
    assert _by_type(res, "ip") == []
    reasons = _reasons(res)
    assert reasons[("ip", "1.2.3.4")] == "contexto de versión de software"
    assert "5.6.7.8" in {v for (_, v) in reasons}
    assert ("ip", "2.4.1.2") in reasons


def test_nombres_de_archivo_no_son_dominios():
    res = extract_iocs("Archivos: dropper.exe, setup.zip, script.py, invoice.docx, readme.md")
    assert _by_type(res, "domain") == []
    kinds = {d.kind for d in res.discarded}
    assert kinds == {"domain"}


def test_dominio_con_tld_valido_y_sin_tld_no_se_toma():
    res = extract_iocs("Visitá example.com y sub.example.org. No tomes node.js ni e.g.")
    assert {e.label for e in _by_type(res, "domain")} == {"example.com", "sub.example.org"}


def test_defang_y_refang_de_url_y_correo():
    text = "Campaña: hxxps://evil[.]example[.]com/login.php y contacto user[at]example[.]com"
    res = extract_iocs(text)
    assert res.defanged_input is True
    (url,) = _by_type(res, "url")
    assert url.label == "https://evil.example.com/login.php"
    assert url.props["host_type"] == "domain"
    (email,) = _by_type(res, "email")
    assert email.label == "user@example.com"
    domains = {e.label for e in _by_type(res, "domain")}
    assert domains == {"evil.example.com"}  # el host de la URL, sin duplicar el dominio
    assert _by_type(res, "domain")[0].props["source"] == "url_host"


def test_dominio_con_extension_ambigua_solo_si_viene_defang():
    bare = extract_iocs("pedido: payload.zip")
    assert _by_type(bare, "domain") == []
    defanged = extract_iocs("dominio evil[.]zip activo")
    assert {e.label for e in _by_type(defanged, "domain")} == {"evil.zip"}


def test_defang_inverso_es_estable():
    url = "http://evil.example.com/a?b=1"
    assert defang(url) == "hxxp://evil[.]example[.]com/a?b=1"
    assert refang(defang(url)) == url
    assert defang("user@example.com") == "user[at]example[.]com"
    assert defang("192.0.2.1") == "192[.]0[.]2[.]1"
    assert defang("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2") == "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"


def test_hashes_por_longitud_y_no_dentro_de_palabras():
    md5 = "d41d8cd98f00b204e9800998ecf8427e"
    sha1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
    sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    res = extract_iocs(f"MD5 {md5}; SHA1 {sha1}; SHA256 {sha256}")
    algos = {e.label: e.props["algorithm"] for e in _by_type(res, "hash")}
    assert algos == {md5: "MD5", sha1: "SHA-1", sha256: "SHA-256"}
    inside_word = extract_iocs("x" + sha256 + "y")
    assert _by_type(inside_word, "hash") == []


def test_numero_de_32_digitos_no_es_hash():
    res = extract_iocs("ID 12345678901234567890123456789012 en el log")
    assert _by_type(res, "hash") == []
    assert ("hash", "12345678901234567890123456789012") in _reasons(res)


def test_cve_se_normaliza():
    res = extract_iocs("explotación de cve-2021-44228 confirmada")
    (vuln,) = _by_type(res, "vulnerability")
    assert vuln.label == "CVE-2021-44228"
    assert vuln.props["cve_id"] == "CVE-2021-44228"


def test_bitcoin_legacy_segwit_y_taproot_con_checksum():
    text = (
        "Pago a 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2, 3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy, "
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4 y "
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
    )
    res = extract_iocs(text)
    wallets = {e.label: e.props["format"] for e in _by_type(res, "wallet")}
    assert wallets == {
        "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2": "p2pkh",
        "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy": "p2sh",
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4": "p2wpkh",
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0": "p2tr",
    }
    assert all(e.props["currency"] == "BTC" for e in _by_type(res, "wallet"))


def test_bitcoin_con_checksum_invalido_se_descarta():
    res = extract_iocs("Pago a 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3")
    assert _by_type(res, "wallet") == []
    assert any(d.kind == "wallet" and "checksum" in d.reason for d in res.discarded)


def test_ethereum_mayusculas_mixtas_no_verificado():
    # Ejemplo de la EIP-55: mayúsculas y minúsculas mezcladas.
    mixed = extract_iocs("ETH 0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")
    (w,) = _by_type(mixed, "wallet")
    assert w.props["currency"] == "ETH"
    assert w.props["eip55"].startswith("no verificado")
    assert w.label == "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
    lower = extract_iocs("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed")
    assert _by_type(lower, "wallet")[0].props["eip55"] == "no aplica"


def test_tecnicas_attack_solo_si_existen_en_el_indice():
    res = extract_iocs("Se observó T1059.001 y también T9999 en el informe")
    assert [t.technique_id for t in res.techniques] == ["T1059.001"]
    tech = res.techniques[0]
    assert tech.name == "PowerShell"
    assert "execution" in tech.tactics
    assert tech.is_subtechnique is True
    assert ("attack", "T9999") in _reasons(res)


def test_tecnica_dentro_de_url_tambien_se_reconoce():
    res = extract_iocs("ref https://attack.mitre.org/techniques/T1566/001/ aqui")
    assert [t.technique_id for t in res.techniques] == ["T1566.001"]


def test_ocurrencias_y_refs_deterministas():
    res = extract_iocs("8.8.8.8 otra vez 8.8.8.8 y 8.8.8.8.")
    (ip,) = _by_type(res, "ip")
    assert ip.ref == "ip:8.8.8.8"
    assert ip.props["occurrences"] == 3


def test_tipos_emitidos_pertenecen_a_entity_types():
    text = (
        "1.1.1.1 2001:db8::5 evil.example.net https://a.example.org/x user@example.com "
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 CVE-2020-0001 "
        "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
    )
    res = extract_iocs(text)
    assert {e.type for e in res.entities} <= set(ENTITY_TYPES)
    assert {"ip", "domain", "url", "email", "hash", "vulnerability", "wallet"} <= {
        e.type for e in res.entities}
