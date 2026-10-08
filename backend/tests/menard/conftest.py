from datetime import datetime, timedelta, timezone

import pytest

from aleph.core.schemas import AccountProfile, AccountRecord, PostRecord
from aleph.menard.engine import analyze_matrix
from aleph.menard.synth import generate_world

T0 = datetime(2025, 3, 1, 22, 0, tzinfo=timezone.utc)


def make_profile(handle, texts, platform="twitter", step_minutes=180, start=T0, **account):
    posts = [
        PostRecord(platform_post_id=f"{handle}-{i}", text=t,
                   created_at=start + timedelta(minutes=step_minutes * i) if start else None)
        for i, t in enumerate(texts)
    ]
    return AccountProfile(account=AccountRecord(platform=platform, handle=handle, **account), posts=posts)


@pytest.fixture(scope="session")
def world():
    return generate_world(seed=5, n_personas=45)


@pytest.fixture(scope="session")
def analysis(world):
    return analyze_matrix(world.profiles)
