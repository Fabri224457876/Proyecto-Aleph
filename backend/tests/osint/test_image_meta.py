import hashlib
from datetime import UTC, datetime
from urllib.parse import unquote

import pytest

from aleph.osint.errors import InvalidInputError
from aleph.osint.image_meta import analyze_image, reverse_image_links

from .helpers import make_jpeg


def test_exif_camera_software_and_dates():
    result = analyze_image(make_jpeg(), filename="foto.jpg")
    assert result.format == "JPEG"
    assert (result.width, result.height) == (64, 48)
    assert result.camera_make == "Canon"
    assert result.camera_model == "EOS R5"
    assert result.software == "Adobe Photoshop 25.0"
    assert result.taken_at == "2024:05:01 11:58:30"
    # Sin OffsetTimeOriginal el EXIF no indica zona: no se inventa una
    assert result.taken_at_iso == "2024-05-01T11:58:30"
    assert any("zona horaria" in w for w in result.warnings)
    assert result.raw["Make"] == "Canon"


def test_gps_is_converted_to_signed_decimal_degrees():
    result = analyze_image(make_jpeg())
    assert result.gps is not None
    # 34°36'12.34" S y 58°22'56.78" W
    assert result.gps.lat == pytest.approx(-(34 + 36 / 60 + 12.34 / 3600), abs=1e-9)
    assert result.gps.lon == pytest.approx(-(58 + 22 / 60 + 56.78 / 3600), abs=1e-9)
    assert result.gps.lat == pytest.approx(-34.603428, abs=1e-6)
    assert result.gps.timestamp_utc == datetime(2024, 5, 1, 14, 30, 0, tzinfo=UTC).isoformat()


def test_gps_produces_location_entity_and_relation():
    result = analyze_image(make_jpeg(), filename="foto.jpg")
    locations = [e for e in result.entities if e.type == "location"]
    assert len(locations) == 1
    assert locations[0].props["source"] == "EXIF GPS"
    assert locations[0].confidence < 1.0  # el EXIF se puede falsificar
    assert any(r.type == "taken_at" and r.dst_ref == locations[0].ref for r in result.relations)
    document = next(e for e in result.entities if e.type == "document")
    assert document.label == "foto.jpg"


def test_sha256_and_perceptual_hash():
    data = make_jpeg()
    result = analyze_image(data)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.size_bytes == len(data)
    assert len(result.phash) == 16 and all(c in "0123456789abcdef" for c in result.phash)
    assert analyze_image(data).phash == result.phash  # determinista
    hash_entity = next(e for e in result.entities if e.type == "hash")
    assert hash_entity.label == result.sha256


def test_image_without_gps_has_no_location():
    result = analyze_image(make_jpeg(with_gps=False))
    assert result.gps is None
    assert not [e for e in result.entities if e.type == "location"]
    assert result.camera_make == "Canon"


def test_image_without_exif_still_hashes():
    result = analyze_image(make_jpeg(with_exif=False))
    assert result.camera_make == ""
    assert result.taken_at == ""
    assert result.gps is None
    assert result.phash


def test_rejects_non_image_bytes():
    with pytest.raises(InvalidInputError):
        analyze_image(b"esto no es una imagen")


def test_rejects_empty_input():
    with pytest.raises(InvalidInputError):
        analyze_image(b"")


def test_size_limit_is_enforced(monkeypatch):
    from aleph.osint import image_meta

    monkeypatch.setattr(image_meta, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(InvalidInputError):
        analyze_image(make_jpeg())


def test_reverse_search_links_are_only_built_and_encoded():
    url = "https://example.com/img/foto%201.jpg?x=a&y=b"
    links = reverse_image_links(url)
    assert [link.engine for link in links] == ["google_lens", "yandex", "tineye", "bing"]
    for link in links:
        assert link.url.startswith("https://")
        # El parámetro de la imagen va codificado: el & de la URL original no puede partir la consulta
        assert "&y=b" not in link.url
    google = next(link for link in links if link.engine == "google_lens")
    assert google.url.startswith("https://lens.google.com/uploadbyurl?url=")
    assert unquote(google.url.split("url=", 1)[1]) == url


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///C:/x.jpg", "ftp://example.com/a.jpg", "", "https://example.com/a b.jpg"])
def test_reverse_search_rejects_unsafe_urls(bad):
    with pytest.raises(InvalidInputError):
        reverse_image_links(bad)
