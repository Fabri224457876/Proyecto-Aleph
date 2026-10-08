"""Validación de direcciones de Bitcoin y Ethereum, y consulta de saldo y transacciones recientes de Bitcoin.

Validación offline:
- Bitcoin legado (P2PKH, P2SH): Base58Check (BIP-13 y Bitcoin wiki, «Base58Check encoding»).
- Bitcoin SegWit y Taproot: Bech32 (BIP-173) para versión 0 y Bech32m (BIP-350) para versiones 1 a 16,
  con las reglas de longitud de programa de BIP-141 y BIP-350.
- Ethereum: formato 0x + 40 hexadecimales y checksum EIP-55, que usa Keccak-256 (no SHA3-256:
  el relleno de Keccak es 0x01 y el de SHA3 es 0x06). Keccak-256 está implementado aquí en Python puro.

Consulta de red (solo Bitcoin en mainnet): API Esplora de Blockstream, documentada en
https://github.com/Blockstream/esplora/blob/master/API.md
  GET /address/:address       → chain_stats y mempool_stats (funded_txo_sum, spent_txo_sum, tx_count).
  GET /address/:address/txs   → hasta 50 transacciones del mempool y las primeras 25 confirmadas, la más reciente primero.
Para Ethereum no se consulta saldo: el explorador público que se evaluó no documenta sus endpoints
sin clave en la documentación revisada, así que solo se valida la dirección.

Las contrapartes de una transacción son las direcciones de las otras entradas o salidas. Pueden incluir
la dirección de cambio, por lo que la relación «sent_to» tiene confianza 0.8 y no prueba un destinatario final.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from ._http import get_json, open_client
from .errors import InvalidInputError, UpstreamError

BLOCKSTREAM_BASE = "https://blockstream.info/api"
MAX_COUNTERPARTIES = 20
RELATION_CONFIDENCE = 0.8

# ----------------------------------------------------------------- Keccak-256 (Python puro)

_MASK64 = (1 << 64) - 1
_KECCAK_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
# Desplazamientos de rotación r[x][y] (ρ del estándar Keccak), con índice lane = x + 5*y
_KECCAK_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rol64(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f1600(lanes: list[int]) -> None:
    for round_constant in _KECCAK_RC:
        c = [lanes[x] ^ lanes[x + 5] ^ lanes[x + 10] ^ lanes[x + 15] ^ lanes[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol64(c[(x + 1) % 5], 1) for x in range(5)]
        for i in range(25):
            lanes[i] ^= d[i % 5]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol64(lanes[x + 5 * y], _KECCAK_ROT[x][y])
        for y in range(5):
            for x in range(5):
                lanes[x + 5 * y] = b[x + 5 * y] ^ ((~b[((x + 1) % 5) + 5 * y] & _MASK64) & b[((x + 2) % 5) + 5 * y])
        lanes[0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    """Keccak-256 original (el que usa Ethereum). Relleno 0x01…0x80, tasa de 136 bytes."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0x00)
    padded[-1] |= 0x80
    lanes = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            lanes[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f1600(lanes)
    return b"".join(lane.to_bytes(8, "little") for lane in lanes[:4])


# ----------------------------------------------------------------- Base58Check

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {ch: i for i, ch in enumerate(_B58_ALPHABET)}


def base58_decode(text: str) -> bytes:
    if not text:
        raise ValueError("cadena base58 vacía")
    number = 0
    for ch in text:
        if ch not in _B58_INDEX:
            raise ValueError(f"carácter base58 inválido: {ch!r}")
        number = number * 58 + _B58_INDEX[ch]
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading + body


def base58_encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    chars = ""
    while number:
        number, remainder = divmod(number, 58)
        chars = _B58_ALPHABET[remainder] + chars
    leading = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading + chars


def _double_sha256(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def base58check_decode(text: str) -> bytes:
    """Devuelve la carga útil (versión + datos) tras verificar el checksum de 4 bytes."""
    raw = base58_decode(text)
    if len(raw) < 5:
        raise ValueError("cadena demasiado corta para Base58Check")
    payload, checksum = raw[:-4], raw[-4:]
    if _double_sha256(payload)[:4] != checksum:
        raise ValueError("checksum Base58Check inválido")
    return payload


def base58check_encode(payload: bytes) -> str:
    return base58_encode(payload + _double_sha256(payload)[:4])


# ----------------------------------------------------------------- Bech32 / Bech32m

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_INDEX = {ch: i for i, ch in enumerate(_BECH32_CHARSET)}
_BECH32_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


@dataclass(frozen=True)
class Bech32Decoded:
    hrp: str
    data: list[int]  # valores de 5 bits sin el checksum
    encoding: str  # "bech32" o "bech32m"


def _bech32_polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= _BECH32_GEN[i]
    return chk


def bech32_decode(text: str) -> Bech32Decoded:
    """Decodifica Bech32 o Bech32m verificando el checksum. Lanza ValueError si no es válido."""
    if len(text) > 90:
        raise ValueError("cadena bech32 demasiado larga")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in text):
        raise ValueError("caracteres fuera de rango ASCII imprimible")
    if text != text.lower() and text != text.upper():
        raise ValueError("mayúsculas y minúsculas mezcladas")
    value = text.lower()
    pos = value.rfind("1")
    if pos < 1 or pos + 7 > len(value):
        raise ValueError("separador «1» o longitud de datos inválidos")
    hrp = value[:pos]
    if any(ch not in _BECH32_INDEX for ch in value[pos + 1:]):
        raise ValueError("carácter de datos fuera del alfabeto bech32")
    data = [_BECH32_INDEX[ch] for ch in value[pos + 1:]]
    expanded = [ord(ch) >> 5 for ch in hrp] + [0] + [ord(ch) & 31 for ch in hrp]
    constant = _bech32_polymod(expanded + data)
    if constant == _BECH32_CONST:
        encoding = "bech32"
    elif constant == _BECH32M_CONST:
        encoding = "bech32m"
    else:
        raise ValueError("checksum bech32 inválido")
    return Bech32Decoded(hrp=hrp, data=data[:-6], encoding=encoding)


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int] | None:
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or value >> frombits:
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return out


@dataclass(frozen=True)
class SegwitAddress:
    hrp: str
    version: int
    program: bytes
    encoding: str


def decode_segwit(text: str) -> SegwitAddress:
    """Decodifica una dirección SegWit/Taproot (witness v0 con Bech32, v1 a v16 con Bech32m)."""
    decoded = bech32_decode(text)
    if not decoded.data:
        raise ValueError("sin datos de programa")
    version = decoded.data[0]
    if version > 16:
        raise ValueError("versión de testigo fuera de rango (0 a 16)")
    if version == 0 and decoded.encoding != "bech32":
        raise ValueError("la versión 0 debe usar Bech32")
    if version != 0 and decoded.encoding != "bech32m":
        raise ValueError("las versiones 1 a 16 deben usar Bech32m")
    program = _convertbits(decoded.data[1:], 5, 8, False)
    if program is None or not 2 <= len(program) <= 40:
        raise ValueError("programa de testigo con longitud o relleno inválidos")
    if version == 0 and len(program) not in (20, 32):
        raise ValueError("programa de la versión 0 debe medir 20 o 32 bytes")
    return SegwitAddress(hrp=decoded.hrp, version=version, program=bytes(program), encoding=decoded.encoding)


# ----------------------------------------------------------------- Clasificación de direcciones


@dataclass(frozen=True)
class BitcoinAddressInfo:
    address: str
    valid: bool
    network: str = ""  # mainnet | testnet | regtest
    kind: str = ""  # p2pkh | p2sh | p2wpkh | p2wsh | p2tr | segwit-vN
    payload_hex: str = ""  # hash de 20 bytes (legado) o programa (SegWit)
    error: str = ""


_LEGACY_VERSIONS = {0x00: ("mainnet", "p2pkh"), 0x05: ("mainnet", "p2sh"),
                    0x6F: ("testnet", "p2pkh"), 0xC4: ("testnet", "p2sh")}
_HRP_NETWORKS = {"bc": "mainnet", "tb": "testnet", "bcrt": "regtest"}


def validate_bitcoin_address(address: str) -> BitcoinAddressInfo:
    text = (address or "").strip()
    if text.lower().startswith(("bc1", "tb1", "bcrt1")):
        try:
            seg = decode_segwit(text)
        except ValueError as exc:
            return BitcoinAddressInfo(address=text, valid=False, error=str(exc))
        network = _HRP_NETWORKS.get(seg.hrp)
        if network is None:
            return BitcoinAddressInfo(address=text, valid=False, error=f"prefijo desconocido: {seg.hrp}")
        if seg.version == 0:
            kind = "p2wpkh" if len(seg.program) == 20 else "p2wsh"
        elif seg.version == 1 and len(seg.program) == 32:
            kind = "p2tr"
        else:
            kind = f"segwit-v{seg.version}"
        return BitcoinAddressInfo(address=text, valid=True, network=network, kind=kind,
                                  payload_hex=seg.program.hex())
    try:
        payload = base58check_decode(text)
    except ValueError as exc:
        return BitcoinAddressInfo(address=text, valid=False, error=str(exc))
    if len(payload) != 21:
        return BitcoinAddressInfo(address=text, valid=False, error="longitud de carga útil inválida")
    if payload[0] not in _LEGACY_VERSIONS:
        return BitcoinAddressInfo(address=text, valid=False, error="versión Base58 no reconocida")
    network, kind = _LEGACY_VERSIONS[payload[0]]
    return BitcoinAddressInfo(address=text, valid=True, network=network, kind=kind, payload_hex=payload[1:].hex())


@dataclass(frozen=True)
class EthereumAddressInfo:
    address: str
    valid: bool
    checksum_status: str
    normalized: str = ""
    error: str = ""


_ETH_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def eip55_checksum(address_lower_hex: str) -> str:
    """Dirección con checksum EIP-55 a partir de los 40 hexadecimales en minúsculas (sin 0x)."""
    digest = keccak256(address_lower_hex.encode("ascii")).hex()
    chars = [
        ch.upper() if ch in "abcdef" and int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(address_lower_hex)
    ]
    return "0x" + "".join(chars)


def validate_ethereum_address(address: str) -> EthereumAddressInfo:
    text = (address or "").strip()
    if not _ETH_RE.fullmatch(text):
        return EthereumAddressInfo(address=text, valid=False, checksum_status="formato inválido",
                                   error="se esperan 0x seguido de 40 caracteres hexadecimales")
    body = text[2:]
    if body == body.lower() or body == body.upper():
        return EthereumAddressInfo(address=text, valid=True,
                                   checksum_status="sin checksum (solo minúsculas o solo mayúsculas)",
                                   normalized=eip55_checksum(body.lower()))
    expected = eip55_checksum(body.lower())
    if expected[2:] == body:
        return EthereumAddressInfo(address=text, valid=True, checksum_status="EIP-55 válido", normalized=expected)
    return EthereumAddressInfo(address=text, valid=False, checksum_status="EIP-55 inválido",
                               normalized=expected, error="el checksum mixto no coincide con EIP-55")


# ----------------------------------------------------------------- Modelos y consulta


class WalletTx(BaseModel):
    txid: str
    confirmed: bool
    timestamp: datetime | None = None  # UTC, solo si está confirmada
    direction: str  # entrada | salida | neutra
    net_sats: int  # positivo si la dirección recibe más de lo que envía
    counterparties: list[str] = Field(default_factory=list)


class WalletReport(BaseModel):
    address: str
    chain: str  # bitcoin | ethereum
    valid: bool
    normalized: str = ""
    network: str = ""
    address_type: str = ""
    checksum_status: str = ""
    error: str = ""
    balance_confirmed_sats: int | None = None
    balance_pending_sats: int | None = None
    tx_count: int | None = None
    recent_txs: list[WalletTx] = Field(default_factory=list)
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


def _sats(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _tx_view(tx: dict[str, Any], address: str) -> WalletTx:
    status = tx.get("status") if isinstance(tx.get("status"), dict) else {}
    confirmed = bool(status.get("confirmed"))
    block_time = status.get("block_time")
    moment = datetime.fromtimestamp(block_time, UTC) if confirmed and isinstance(block_time, int) else None
    received = sum(_sats(out.get("value")) for out in tx.get("vout") or []
                   if isinstance(out, dict) and out.get("scriptpubkey_address") == address)
    sent = 0
    input_addresses: list[str] = []
    for vin in tx.get("vin") or []:
        prevout = vin.get("prevout") if isinstance(vin, dict) else None
        if not isinstance(prevout, dict):
            continue
        if prevout.get("scriptpubkey_address") == address:
            sent += _sats(prevout.get("value"))
        elif prevout.get("scriptpubkey_address"):
            input_addresses.append(str(prevout["scriptpubkey_address"]))
    output_addresses = [str(out["scriptpubkey_address"]) for out in tx.get("vout") or []
                        if isinstance(out, dict) and out.get("scriptpubkey_address")
                        and out.get("scriptpubkey_address") != address]
    net = received - sent
    if net > 0:
        direction, others = "entrada", input_addresses
    elif net < 0:
        direction, others = "salida", output_addresses
    else:
        direction, others = "neutra", []
    counterparties = list(dict.fromkeys(a for a in others if a != address))[:MAX_COUNTERPARTIES]
    return WalletTx(txid=str(tx.get("txid", "")), confirmed=confirmed, timestamp=moment,
                    direction=direction, net_sats=net, counterparties=counterparties)


def _graph(report: WalletReport, txs: list[WalletTx]) -> tuple[list[EntityRecord], list[RelationRecord]]:
    entities = [EntityRecord(
        type="wallet", label=report.normalized or report.address, ref="wallet", confidence=1.0,
        props={"chain": report.chain, "network": report.network, "address_type": report.address_type,
               "balance_confirmed_sats": report.balance_confirmed_sats,
               "balance_pending_sats": report.balance_pending_sats, "tx_count": report.tx_count},
    )]
    relations: list[RelationRecord] = []
    refs: dict[str, str] = {}
    for tx in txs:
        for other in tx.counterparties:
            if other not in refs:
                refs[other] = f"cp{len(refs)}"
                entities.append(EntityRecord(type="wallet", label=other, ref=refs[other], confidence=1.0,
                                             props={"chain": "bitcoin"}))
            src, dst = (refs[other], "wallet") if tx.direction == "entrada" else ("wallet", refs[other])
            relations.append(RelationRecord(
                src_ref=src, dst_ref=dst, type="sent_to", confidence=RELATION_CONFIDENCE,
                props={"txid": tx.txid, "sats": abs(tx.net_sats), "confirmed": tx.confirmed,
                       "timestamp": tx.timestamp.isoformat() if tx.timestamp else ""},
            ))
    return entities, relations


async def analyze_wallet(
    address: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_txs: int = 25,
) -> WalletReport:
    """Valida una dirección y, si es Bitcoin en mainnet, consulta saldo y transacciones recientes."""
    if not 1 <= max_txs <= 50:
        raise InvalidInputError("max_txs debe estar entre 1 y 50")
    text = (address or "").strip()
    if not text:
        raise InvalidInputError("dirección vacía")

    if text.lower().startswith("0x"):
        eth = validate_ethereum_address(text)
        report = WalletReport(
            address=text, chain="ethereum", valid=eth.valid, normalized=eth.normalized,
            checksum_status=eth.checksum_status, error=eth.error, address_type="cuenta (EOA o contrato)",
            warnings=[("sin consulta de saldo ni transacciones para Ethereum: no hay un explorador público "
                       "documentado y sin clave en esta versión")],
        )
        if eth.valid:
            report.entities, report.relations = _graph(report, [])
        return report

    btc = validate_bitcoin_address(text)
    # Bech32 es insensible a mayúsculas; el explorador devuelve la forma en minúsculas
    canonical = text.lower() if text.lower().startswith(("bc1", "tb1", "bcrt1")) else text
    report = WalletReport(address=text, chain="bitcoin", valid=btc.valid, normalized=canonical,
                          network=btc.network, address_type=btc.kind, error=btc.error)
    if not btc.valid:
        return report
    if btc.network != "mainnet":
        report.warnings.append(f"red {btc.network}: no se consulta saldo (el explorador configurado es mainnet)")
        report.entities, report.relations = _graph(report, [])
        return report

    async with open_client(client) as http:
        info = await get_json(http, f"{BLOCKSTREAM_BASE}/address/{canonical}")
        txs_raw = await get_json(http, f"{BLOCKSTREAM_BASE}/address/{canonical}/txs")
    if not isinstance(info, dict) or not isinstance(txs_raw, list):
        raise UpstreamError("respuesta inesperada del explorador Blockstream", url=BLOCKSTREAM_BASE)
    chain = info.get("chain_stats") if isinstance(info.get("chain_stats"), dict) else {}
    mempool = info.get("mempool_stats") if isinstance(info.get("mempool_stats"), dict) else {}
    report.balance_confirmed_sats = _sats(chain.get("funded_txo_sum")) - _sats(chain.get("spent_txo_sum"))
    report.balance_pending_sats = _sats(mempool.get("funded_txo_sum")) - _sats(mempool.get("spent_txo_sum"))
    report.tx_count = _sats(chain.get("tx_count")) + _sats(mempool.get("tx_count"))
    report.recent_txs = [_tx_view(tx, canonical) for tx in txs_raw[:max_txs] if isinstance(tx, dict)]
    report.raw = {"address_info": info, "txs": txs_raw[:max_txs]}
    report.entities, report.relations = _graph(report, report.recent_txs)
    return report
