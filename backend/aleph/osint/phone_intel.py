"""Análisis de un número de teléfono: normalización a E.164, país y, para Argentina, tipo de línea y
área por característica. Solo analiza el número: no consulta ningún servicio de terceros.

Tablas embebidas (no hay librería de números en el proyecto):
- Prefijos de país (indicativo internacional): tabla parcial de los países más frecuentes. Si el
  indicativo no figura, el país queda vacío y se avisa. No es el plan de numeración ITU completo.
- Códigos de área de Argentina: tabla parcial de las características principales, redactada a partir de
  conocimiento de la numeración, no contrastada con el listado oficial del ENACOM. Una característica
  que no figura queda como «área no determinada».

Reglas argentinas:
- Número nacional significativo de 10 dígitos (característica + abonado), sin troncal 0 ni prefijo 9.
- Celular en formato internacional: +54 9 … (el 9 va después del 54).
- Celular en formato nacional: 0 + característica + 15 + abonado (el 15 va después de la característica).
- Sin marcador de celular se asume línea fija, indicándolo como inferido.
- Numeración 0800: servicio gratuito.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from .errors import InvalidInputError

MAX_DIGITS = 15  # E.164
MIN_DIGITS = 8

# (indicativo, ISO-3166 alfa-2 o "", nombre en español). Tabla parcial, ordenada por indicativo.
COUNTRY_CODES: tuple[tuple[str, str, str], ...] = (
    ("1", "", "Norteamérica (plan NANP: Estados Unidos, Canadá y otros)"),
    ("7", "RU", "Rusia (o Kazajistán)"),
    ("20", "EG", "Egipto"), ("27", "ZA", "Sudáfrica"), ("30", "GR", "Grecia"), ("31", "NL", "Países Bajos"),
    ("32", "BE", "Bélgica"), ("33", "FR", "Francia"), ("34", "ES", "España"), ("36", "HU", "Hungría"),
    ("39", "IT", "Italia"), ("40", "RO", "Rumania"), ("41", "CH", "Suiza"), ("43", "AT", "Austria"),
    ("44", "GB", "Reino Unido"), ("45", "DK", "Dinamarca"), ("46", "SE", "Suecia"), ("47", "NO", "Noruega"),
    ("48", "PL", "Polonia"), ("49", "DE", "Alemania"), ("51", "PE", "Perú"), ("52", "MX", "México"),
    ("53", "CU", "Cuba"), ("54", "AR", "Argentina"), ("55", "BR", "Brasil"), ("56", "CL", "Chile"),
    ("57", "CO", "Colombia"), ("58", "VE", "Venezuela"), ("60", "MY", "Malasia"), ("61", "AU", "Australia"),
    ("62", "ID", "Indonesia"), ("63", "PH", "Filipinas"), ("64", "NZ", "Nueva Zelanda"),
    ("65", "SG", "Singapur"), ("66", "TH", "Tailandia"), ("81", "JP", "Japón"), ("82", "KR", "Corea del Sur"),
    ("84", "VN", "Vietnam"), ("86", "CN", "China"), ("90", "TR", "Turquía"), ("91", "IN", "India"),
    ("92", "PK", "Pakistán"), ("93", "AF", "Afganistán"), ("94", "LK", "Sri Lanka"), ("95", "MM", "Myanmar"),
    ("98", "IR", "Irán"), ("212", "MA", "Marruecos"), ("213", "DZ", "Argelia"), ("216", "TN", "Túnez"),
    ("218", "LY", "Libia"), ("234", "NG", "Nigeria"), ("254", "KE", "Kenia"),
    ("351", "PT", "Portugal"), ("352", "LU", "Luxemburgo"), ("353", "IE", "Irlanda"), ("354", "IS", "Islandia"),
    ("355", "AL", "Albania"), ("356", "MT", "Malta"), ("357", "CY", "Chipre"), ("358", "FI", "Finlandia"),
    ("359", "BG", "Bulgaria"), ("370", "LT", "Lituania"), ("371", "LV", "Letonia"), ("372", "EE", "Estonia"),
    ("373", "MD", "Moldavia"), ("374", "AM", "Armenia"), ("375", "BY", "Bielorrusia"), ("376", "AD", "Andorra"),
    ("377", "MC", "Mónaco"), ("378", "SM", "San Marino"), ("380", "UA", "Ucrania"), ("381", "RS", "Serbia"),
    ("382", "ME", "Montenegro"), ("385", "HR", "Croacia"), ("386", "SI", "Eslovenia"),
    ("387", "BA", "Bosnia y Herzegovina"), ("389", "MK", "Macedonia del Norte"), ("420", "CZ", "Chequia"),
    ("421", "SK", "Eslovaquia"), ("423", "LI", "Liechtenstein"), ("500", "FK", "Islas Malvinas"),
    ("501", "BZ", "Belice"), ("502", "GT", "Guatemala"), ("503", "SV", "El Salvador"), ("504", "HN", "Honduras"),
    ("505", "NI", "Nicaragua"), ("506", "CR", "Costa Rica"), ("507", "PA", "Panamá"), ("509", "HT", "Haití"),
    ("591", "BO", "Bolivia"), ("592", "GY", "Guyana"), ("593", "EC", "Ecuador"), ("595", "PY", "Paraguay"),
    ("597", "SR", "Surinam"), ("598", "UY", "Uruguay"), ("670", "TL", "Timor Oriental"),
    ("673", "BN", "Brunéi"), ("675", "PG", "Papúa Nueva Guinea"), ("679", "FJ", "Fiyi"),
    ("852", "HK", "Hong Kong"), ("853", "MO", "Macao"), ("855", "KH", "Camboya"), ("856", "LA", "Laos"),
    ("880", "BD", "Bangladés"), ("886", "TW", "Taiwán"), ("960", "MV", "Maldivas"), ("961", "LB", "Líbano"),
    ("962", "JO", "Jordania"), ("963", "SY", "Siria"), ("964", "IQ", "Irak"), ("965", "KW", "Kuwait"),
    ("966", "SA", "Arabia Saudita"), ("968", "OM", "Omán"), ("970", "PS", "Palestina"),
    ("971", "AE", "Emiratos Árabes Unidos"), ("972", "IL", "Israel"), ("973", "BH", "Baréin"),
    ("974", "QA", "Catar"), ("975", "BT", "Bután"), ("976", "MN", "Mongolia"), ("977", "NP", "Nepal"),
    ("992", "TJ", "Tayikistán"), ("993", "TM", "Turkmenistán"), ("994", "AZ", "Azerbaiyán"),
    ("995", "GE", "Georgia"), ("996", "KG", "Kirguistán"), ("998", "UZ", "Uzbekistán"),
)

# Códigos de área argentinos principales (característica → localidad o zona). Tabla parcial.
AR_AREA_CODES: dict[str, str] = {
    "11": "Ciudad Autónoma de Buenos Aires y Gran Buenos Aires (AMBA)",
    "221": "La Plata (Buenos Aires)",
    "223": "Mar del Plata (Buenos Aires)",
    "261": "Mendoza",
    "264": "San Juan",
    "266": "San Luis",
    "291": "Bahía Blanca (Buenos Aires)",
    "294": "San Carlos de Bariloche (Río Negro)",
    "297": "Comodoro Rivadavia (Chubut)",
    "299": "Neuquén",
    "341": "Rosario (Santa Fe)",
    "342": "Santa Fe",
    "343": "Paraná (Entre Ríos)",
    "351": "Córdoba",
    "362": "Resistencia (Chaco)",
    "370": "Formosa",
    "376": "Posadas (Misiones)",
    "379": "Corrientes",
    "380": "La Rioja",
    "381": "San Miguel de Tucumán",
    "383": "San Fernando del Valle de Catamarca",
    "385": "Santiago del Estero",
    "387": "Salta",
    "388": "San Salvador de Jujuy",
    "2901": "Ushuaia (Tierra del Fuego)",
    "2920": "Viedma (Río Negro)",
    "2954": "Santa Rosa (La Pampa)",
    "2966": "Río Gallegos (Santa Cruz)",
}
_COUNTRY_BY_CODE = {code: (iso, name) for code, iso, name in COUNTRY_CODES}


class PhoneReport(BaseModel):
    input: str
    valid: bool
    e164: str = ""
    country_code: str = ""
    country_iso: str = ""
    country_name: str = ""
    national_number: str = ""  # sin indicativo, sin troncal y sin marcador de celular
    line_type: str = ""  # móvil | fijo | fijo (inferido) | gratuito (0800) | indeterminado
    area_code: str = ""
    area_name: str = ""
    error: str = ""
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _Parsed:
    digits: str  # forma E.164 sin el «+»
    country_code: str
    national: str
    mobile: bool
    line_type: str


def _match_country(digits: str) -> str:
    for length in (3, 2, 1):
        candidate = digits[:length]
        if candidate in _COUNTRY_BY_CODE:
            return candidate
    return ""


def _area_of(national: str) -> str:
    matches = [code for code in AR_AREA_CODES if national.startswith(code)]
    return max(matches, key=len) if matches else ""


def _strip_mobile_15(rest: str) -> tuple[str, bool]:
    """Quita el 15 de celular nacional tras la característica, si es el caso."""
    if len(rest) == 10:
        return rest, False
    if len(rest) == 12:
        for length in (2, 3, 4):
            if rest[length:length + 2] == "15":
                area = rest[:length]
                if area in AR_AREA_CODES or length == 2:
                    return area + rest[length + 2:], True
        for length in (2, 3, 4):
            if rest[length:length + 2] == "15":
                return rest[:length] + rest[length + 2:], True
    raise InvalidInputError("longitud de número argentino inválida (se esperan 10 dígitos sin marcadores)")


def _parse_argentina(rest: str, international: bool) -> _Parsed:
    mobile = False
    if international and rest.startswith("9") or not international and len(rest) == 11 and rest.startswith("9"):
        mobile, rest = True, rest[1:]
    rest = rest.removeprefix("0")
    national, marker = _strip_mobile_15(rest)
    mobile = mobile or marker
    if national.startswith("800"):
        line_type = "gratuito (0800)"
    elif mobile:
        line_type = "móvil"
    else:
        line_type = "fijo (inferido: sin marcador de celular)"
    e164 = "54" + ("9" if mobile and not national.startswith("800") else "") + national
    return _Parsed(digits=e164, country_code="54", national=national, mobile=mobile, line_type=line_type)


def analyze_phone(number: str, *, default_region: str = "AR") -> PhoneReport:
    """Normaliza a E.164 y describe país, tipo de línea y área. Función sync: solo cálculo local."""
    raw = (number or "").strip()
    if not raw or len(raw) > 40:
        raise InvalidInputError("número vacío o demasiado largo")
    if not re.fullmatch(r"[+()\-.\s\d]+", raw):
        return PhoneReport(input=raw, valid=False, error="el número contiene caracteres no válidos")
    region = (default_region or "").upper()
    compact = re.sub(r"[^\d+]", "", raw)
    warnings: list[str] = []
    try:
        if compact.startswith(("+", "00")):
            digits = compact[1:] if compact.startswith("+") else compact[2:]
            if not digits.isdigit() or not MIN_DIGITS <= len(digits) <= MAX_DIGITS:
                raise InvalidInputError("número internacional con longitud inválida")
            country = _match_country(digits)
            if country and _COUNTRY_BY_CODE[country][0] == "AR":
                parsed = _parse_argentina(digits[len(country):], international=True)
            else:
                parsed = _Parsed(digits=digits, country_code=country, national=digits[len(country):],
                                 mobile=False, line_type="indeterminado")
                if not country:
                    warnings.append("indicativo de país no incluido en la tabla embebida")
        else:
            if not compact.isdigit():
                raise InvalidInputError("número nacional con caracteres no válidos")
            if region != "AR":
                raise InvalidInputError("sin código de país no se puede normalizar fuera de Argentina: "
                                        "indique el número con +código de país")
            national_input = compact.removeprefix("0")
            parsed = _parse_argentina(national_input, international=False)
    except InvalidInputError as exc:
        return PhoneReport(input=raw, valid=False, error=str(exc), warnings=warnings)

    iso, country_name = _COUNTRY_BY_CODE.get(parsed.country_code, ("", ""))
    area = _area_of(parsed.national) if parsed.country_code == "54" else ""
    area_name = AR_AREA_CODES.get(area, "")
    if parsed.country_code == "54" and not area:
        warnings.append("característica no incluida en la tabla embebida de códigos de área")
    e164 = "+" + parsed.digits
    entities = [EntityRecord(
        type="phone", label=e164, ref="phone", confidence=1.0,
        props={"country_code": parsed.country_code, "country_iso": iso, "line_type": parsed.line_type,
               "area_code": area},
    )]
    relations: list[RelationRecord] = []
    if parsed.country_code:
        entities.append(EntityRecord(type="location", label=country_name or parsed.country_code,
                                     ref="country", confidence=1.0,
                                     props={"tipo": "país", "iso": iso}))
    if area:
        entities.append(EntityRecord(type="location", label=area_name, ref="area", confidence=0.8,
                                     props={"tipo": "área telefónica", "area_code": area, "country": "AR"}))
        relations.append(RelationRecord(src_ref="phone", dst_ref="area", type="located_in", confidence=0.8))
    elif parsed.country_code:
        relations.append(RelationRecord(src_ref="phone", dst_ref="country", type="located_in", confidence=1.0))
    return PhoneReport(
        input=raw, valid=True, e164=e164, country_code=parsed.country_code, country_iso=iso,
        country_name=country_name, national_number=parsed.national, line_type=parsed.line_type,
        area_code=area, area_name=area_name, entities=entities, relations=relations, warnings=warnings,
    )
