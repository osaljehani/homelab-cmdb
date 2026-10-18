import pytest

from cmdb.domain.refs import canonical_ref

_DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("memcached:1.6.29-alpine", "memcached:1.6.29-alpine"),
        ("library/memcached:1.6.29-alpine", "memcached:1.6.29-alpine"),
        ("gitea/gitea:latest", "gitea/gitea:latest"),
        ("cloudflare/cloudflared:latest", "cloudflare/cloudflared:latest"),
        ("ghcr.io/example-org/webapp:1.2.3", "example-org/webapp:1.2.3"),
        (
            "192.0.2.10:5000/example-org/webapp:1.2.3",
            "example-org/webapp:1.2.3",
        ),
        ("localhost:5000/foo:bar", "foo:bar"),
        ("toolbox-ai:local", "toolbox-ai:local"),
        ("homelabcmdb-cmdb", "homelabcmdb-cmdb:latest"),
        ("nginx", "nginx:latest"),
        (f"repo@{_DIGEST}", "repo:latest"),
        (f"repo:tag@{_DIGEST}", "repo:tag"),
        ("  nginx:1.27  ", "nginx:1.27"),
    ],
)
def test_canonical_ref(raw, expected):
    assert canonical_ref(raw) == expected


def test_canonical_ref_empty_raises():
    with pytest.raises(ValueError):
        canonical_ref("")
