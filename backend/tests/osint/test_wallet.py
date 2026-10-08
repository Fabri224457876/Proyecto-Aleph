import hashlib

import httpx
import pytest

from aleph.osint.errors import InvalidInputError
from aleph.osint.wallet import (
    analyze_wallet,
    base58check_decode,
    base58check_encode,
    bech32_decode,
    decode_segwit,
    eip55_checksum,
    keccak256,
    validate_bitcoin_address,
    validate_ethereum_address,
)

from .helpers import json_response, mock_client

GENESIS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"  # dirección del bloque génesis de Bitcoin


# ----------------------------------------------------------------- Keccak-256


def test_keccak256_known_vectors():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"hello").hex() == "1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


def test_keccak256_is_not_sha3_256():
    assert keccak256(b"").hex() != hashlib.sha3_256(b"").hexdigest()


def test_keccak256_multi_block_input():
    # Más de una tasa de 136 bytes: fuerza varias absorciones
    data = bytes(range(256)) * 2
    assert len(keccak256(data)) == 32
    assert keccak256(data) != keccak256(data[:-1])


# ----------------------------------------------------------------- Base58Check


def test_base58check_genesis_address_hash160():
    payload = base58check_decode(GENESIS)
    assert payload[0] == 0x00
    assert payload[1:].hex() == "62e907b15cbf27d5425399ebf6f0fb50ebb88f18"


def test_base58check_detects_checksum_errors():
    with pytest.raises(ValueError):
        base58check_decode("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")  # último carácter alterado
    with pytest.raises(ValueError):
        base58check_decode("1A1zP1eP5QGefi2DMPTfTL5SLmv7Div0Na")  # carácter fuera del alfabeto


def test_base58check_round_trip_and_leading_zeros():
    payload = bytes([0x05]) + bytes(range(20))
    encoded = base58check_encode(payload)
    assert encoded.startswith("3")
    assert base58check_decode(encoded) == payload
    assert base58check_encode(bytes(2) + b"\x01") .startswith("11")


# ----------------------------------------------------------------- Bitcoin: legado


def test_genesis_address_is_valid_mainnet_p2pkh():
    info = validate_bitcoin_address(GENESIS)
    assert info.valid is True
    assert (info.network, info.kind) == ("mainnet", "p2pkh")
    assert info.payload_hex == "62e907b15cbf27d5425399ebf6f0fb50ebb88f18"


def test_p2sh_and_testnet_legacy_classification():
    p2sh = base58check_encode(bytes([0x05]) + bytes(range(20)))
    info = validate_bitcoin_address(p2sh)
    assert (info.valid, info.network, info.kind) == (True, "mainnet", "p2sh")
    testnet = base58check_encode(bytes([0x6F]) + bytes(20))
    info = validate_bitcoin_address(testnet)
    assert (info.valid, info.network, info.kind) == (True, "testnet", "p2pkh")


def test_unknown_base58_version_and_bad_length_are_invalid():
    wrong_version = base58check_encode(bytes([0x42]) + bytes(20))
    assert validate_bitcoin_address(wrong_version).valid is False
    too_long = base58check_encode(bytes([0x00]) + bytes(40))
    info = validate_bitcoin_address(too_long)
    assert info.valid is False and "longitud" in info.error


def test_flipped_base58_character_is_invalid():
    info = validate_bitcoin_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")
    assert info.valid is False
    assert "checksum" in info.error


# ----------------------------------------------------------------- Bitcoin: Bech32 / Bech32m (BIP-173, BIP-350)

VALID_SEGWIT = [
    ("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", "0014751e76e8199196d454941c45d1b3a323f1433bd6", "p2wpkh"),
    ("tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7",
     "00201863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262", "p2wsh"),
    ("bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kt5nd6y",
     "5128751e76e8199196d454941c45d1b3a323f1433bd6751e76e8199196d454941c45d1b3a323f1433bd6", "segwit-v1"),
    ("BC1SW50QGDZ25J", "6002751e", "segwit-v16"),
    ("bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs", "5210751e76e8199196d454941c45d1b3a323", "segwit-v2"),
    ("tb1qqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesrxh6hy",
     "0020000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433", "p2wsh"),
    ("tb1pqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesf3hn0c",
     "5120000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433", "p2tr"),
    ("bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
     "512079be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798", "p2tr"),
]

INVALID_SEGWIT = [
    "tc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq5zuyut",  # HRP no válido
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",  # Bech32 en vez de Bech32m
    "tb1z0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqglt7rf",  # Bech32 en vez de Bech32m
    "BC1S0XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ54WELL",  # checksum
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh",  # Bech32m en versión 0
    "tb1q0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq24jc47",  # Bech32m en versión 0
    "bc1p38j9r5y49hruaue7wxjce0updqjuyyx0kh56v8s25huc6995vvpql3jow4",  # carácter inválido en el checksum
    "BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R",  # versión 17
    "bc1pw5dgrnzv",  # programa de 1 byte
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v8n0nx0muaewav253zgeav",  # programa de 41 bytes
    "BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P",  # versión 0 con programa de 16 bytes
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47Zagq",  # mayúsculas mezcladas
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf",  # relleno de más de 4 bits
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vpggkg4j",  # relleno no nulo
    "bc1gmk9yu",  # sin datos
]


@pytest.mark.parametrize("address,program_script,kind", VALID_SEGWIT)
def test_bip_valid_segwit_addresses(address, program_script, kind):
    info = validate_bitcoin_address(address)
    assert info.valid is True, info.error
    assert info.kind == kind
    # El scriptPubKey de la especificación es OP_n + push(programa): el programa es su resto
    assert program_script.endswith(info.payload_hex)


@pytest.mark.parametrize("address", INVALID_SEGWIT)
def test_bip_invalid_segwit_addresses_are_rejected(address):
    assert validate_bitcoin_address(address).valid is False


def test_generic_bech32m_strings_from_bip350_decode():
    for text, encoding in [("A1LQFN3A", "bech32m"), ("a1lqfn3a", "bech32m"),
                           ("abcdef1l7aum6echk45nj3s0wdvt2fg8x9yrzpqzd3ryx", "bech32m"),
                           ("split1checkupstagehandshakeupstreamerranterredcaperredlc445v", "bech32m"),
                           ("?1v759aa", "bech32m")]:
        assert bech32_decode(text).encoding == encoding
    with pytest.raises(ValueError):
        bech32_decode("y1b0jsk6g")  # carácter fuera del alfabeto
    with pytest.raises(ValueError):
        bech32_decode("M1VUXWEZ" + "x")  # mayúsculas mezcladas


def test_segwit_v0_requires_bech32_and_v1_requires_bech32m():
    seg = decode_segwit("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")
    assert (seg.version, seg.encoding, len(seg.program)) == (0, "bech32", 20)
    with pytest.raises(ValueError):
        decode_segwit("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh")


def test_bitcoin_prefix_networks():
    assert validate_bitcoin_address("tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7").network == "testnet"


# ----------------------------------------------------------------- Ethereum (EIP-55)

EIP55_VALID = [
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
]


@pytest.mark.parametrize("address", EIP55_VALID)
def test_eip55_reference_addresses_are_valid(address):
    info = validate_ethereum_address(address)
    assert info.valid is True
    assert info.checksum_status == "EIP-55 válido"
    assert info.normalized == address


def test_eip55_mixed_case_with_wrong_checksum_is_invalid():
    info = validate_ethereum_address("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD")  # última letra alterada
    assert info.valid is False
    assert info.checksum_status == "EIP-55 inválido"
    assert info.normalized == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


def test_lowercase_address_has_no_checksum_to_verify():
    info = validate_ethereum_address("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed")
    assert info.valid is True
    assert info.checksum_status.startswith("sin checksum")
    assert info.normalized == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


@pytest.mark.parametrize("bad", ["0x123", "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "0xZZ" + "a" * 38])
def test_malformed_ethereum_addresses(bad):
    assert validate_ethereum_address(bad).valid is False


def test_eip55_checksum_uses_keccak_of_lowercase_hex():
    body = "5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
    assert eip55_checksum(body) == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


# ----------------------------------------------------------------- análisis con explorador simulado


async def test_bitcoin_analysis_balances_and_counterparty_relations():
    chain_info = {
        "address": GENESIS,
        "chain_stats": {"funded_txo_count": 2, "funded_txo_sum": 5000, "spent_txo_count": 1,
                        "spent_txo_sum": 2000, "tx_count": 2},
        "mempool_stats": {"funded_txo_count": 0, "funded_txo_sum": 300, "spent_txo_count": 0,
                          "spent_txo_sum": 0, "tx_count": 1},
    }
    txs = [
        {  # recibido: GENESIS cobra 5000 de 3PagadorEjemplo
            "txid": "aa11", "status": {"confirmed": True, "block_time": 1700000000},
            "vin": [{"prevout": {"scriptpubkey_address": "3PagadorEjemplo", "value": 5200}}],
            "vout": [{"scriptpubkey_address": GENESIS, "value": 5000},
                     {"scriptpubkey_address": "3PagadorEjemplo", "value": 150}],
        },
        {  # enviado: GENESIS paga 1900 a bc1qdestinoejemplo y recupera 50 de cambio
            "txid": "bb22", "status": {"confirmed": True, "block_time": 1700100000},
            "vin": [{"prevout": {"scriptpubkey_address": GENESIS, "value": 2000}}],
            "vout": [{"scriptpubkey_address": "bc1qdestinoejemplo", "value": 1900},
                     {"scriptpubkey_address": GENESIS, "value": 50}],
        },
        {  # pendiente en el mempool: recibido 300
            "txid": "cc33", "status": {"confirmed": False},
            "vin": [{"prevout": None}],
            "vout": [{"scriptpubkey_address": GENESIS, "value": 300}],
        },
    ]

    def handler(request: httpx.Request):
        if request.url.path.endswith("/txs"):
            return json_response(txs)
        return json_response(chain_info)

    async with mock_client(handler) as client:
        report = await analyze_wallet(GENESIS, client=client)

    assert report.valid is True and report.network == "mainnet" and report.address_type == "p2pkh"
    assert report.balance_confirmed_sats == 3000
    assert report.balance_pending_sats == 300
    assert report.tx_count == 3
    directions = {tx.txid: (tx.direction, tx.net_sats) for tx in report.recent_txs}
    assert directions == {"aa11": ("entrada", 5000), "bb22": ("salida", -1950), "cc33": ("entrada", 300)}
    assert report.recent_txs[0].timestamp is not None
    assert report.recent_txs[2].timestamp is None  # sin confirmar: sin fecha de bloque

    wallet = next(e for e in report.entities if e.type == "wallet" and e.ref == "wallet")
    assert wallet.props["balance_confirmed_sats"] == 3000
    relation_pairs = {(r.type, r.props["txid"]) for r in report.relations}
    assert ("sent_to", "aa11") in relation_pairs and ("sent_to", "bb22") in relation_pairs
    incoming = next(r for r in report.relations if r.props["txid"] == "aa11")
    outgoing = next(r for r in report.relations if r.props["txid"] == "bb22")
    counter_refs = {e.ref: e.label for e in report.entities if e.type == "wallet" and e.ref != "wallet"}
    assert counter_refs[incoming.src_ref] == "3PagadorEjemplo" and incoming.dst_ref == "wallet"
    assert outgoing.src_ref == "wallet" and counter_refs[outgoing.dst_ref] == "bc1qdestinoejemplo"


async def test_bitcoin_lookup_uses_canonical_lowercase_for_bech32():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        return json_response({"chain_stats": {}, "mempool_stats": {}} if not request.url.path.endswith("/txs") else [])

    async with mock_client(handler) as client:
        report = await analyze_wallet("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", client=client)
    assert report.valid is True
    assert seen == ["/api/address/bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
                    "/api/address/bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4/txs"]
    assert report.normalized == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


async def test_testnet_and_invalid_addresses_make_no_requests():
    def handler(request: httpx.Request):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("no debía consultarse la red")

    async with mock_client(handler) as client:
        testnet = await analyze_wallet("tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7", client=client)
        invalid = await analyze_wallet("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb", client=client)
    assert testnet.valid is True and testnet.balance_confirmed_sats is None
    assert any("testnet" in w for w in testnet.warnings)
    assert invalid.valid is False and invalid.error


async def test_ethereum_is_validated_without_lookup_and_says_so():
    def handler(request: httpx.Request):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("no debía consultarse la red")

    async with mock_client(handler) as client:
        report = await analyze_wallet("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", client=client)
    assert report.chain == "ethereum"
    assert report.valid is True
    assert report.normalized == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    assert report.balance_confirmed_sats is None
    assert any("Ethereum" in w for w in report.warnings)
    assert report.entities[0].type == "wallet"


async def test_empty_or_bad_max_txs_rejected():
    async with mock_client(lambda r: json_response({})) as client:
        with pytest.raises(InvalidInputError):
            await analyze_wallet("", client=client)
        with pytest.raises(InvalidInputError):
            await analyze_wallet(GENESIS, client=client, max_txs=0)
