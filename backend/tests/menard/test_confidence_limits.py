from datetime import UTC, datetime, timedelta

from aleph.core.schemas import AccountProfile, AccountRecord, PostRecord
from aleph.menard import compare


def _profile(handle: str, n: int) -> AccountProfile:
    base = datetime(2025, 3, 1, 21, 0, tzinfo=UTC)
    texts = ["che, q onda con esto?? no entiendo nadaaa jajaja", "tmb me paso, re mal todo. xq siempre igual",
             "naaa mentira, posta?? jajajaja q locura", "bueno listo, dsp vemos. abrazo grandeee"]
    posts = [PostRecord(platform_post_id=f"{handle}-{k}", text=f"{texts[k % 4]} {k}",
                        created_at=base + timedelta(hours=7 * k)) for k in range(n)]
    return AccountProfile(account=AccountRecord(platform="x", handle=handle, bio="mismo texto"), posts=posts)


def test_near_identical_pair_never_reports_certainty_and_explains_the_limit():
    res = compare(_profile("clon_uno", 14), _profile("clon_dos", 14))
    assert res.score <= 0.99
    assert res.confidence != "alta"
    if res.score >= 0.5:
        assert "La confianza no llega a alta porque" in res.summary
        assert "14 publicaciones" in res.summary
