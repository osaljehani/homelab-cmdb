"""Canonical image-reference normalization.

The runtime (`docker ps`) and Zot registry scanners spell the same image
differently: registry host present/absent, docker.io `library/` prefix
present/absent, tag sometimes omitted. Canonicalizing to one short,
registry-agnostic form lets a single Image row represent one logical image.
"""


def canonical_ref(ref: str) -> str:
    """Normalize an image reference to a canonical short identity string.

    library/memcached:1.6.29-alpine       -> memcached:1.6.29-alpine
    ghcr.io/osaljehani/portfolio:0.0.3024  -> osaljehani/portfolio:0.0.3024
    homelabcmdb-cmdb                       -> homelabcmdb-cmdb:latest
    """
    if not ref or not ref.strip():
        raise ValueError("empty image ref")
    name = ref.strip().split("@", 1)[0]  # drop any @sha256:... digest

    # Drop a leading registry host (first segment with '.', ':', or 'localhost').
    if "/" in name:
        first, rest = name.split("/", 1)
        if "." in first or ":" in first or first == "localhost":
            name = rest

    # Strip the docker.io official-namespace 'library/' prefix.
    if name.startswith("library/"):
        name = name[len("library/") :]

    # A ':' after the last '/' separates the tag; default missing tag to latest.
    slash = name.rfind("/")
    colon = name.rfind(":")
    if colon > slash:
        repo, tag = name[:colon], name[colon + 1 :]
    else:
        repo, tag = name, ""
    return f"{repo}:{tag or 'latest'}"
