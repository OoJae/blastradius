"""Tests for the ID contract.

The golden values below are pinned deliberately. The loader computes ids at
ingest time and the API recomputes them at query time, in a different process
and potentially a different build; if the hash ever drifted, queries would
silently miss every node instead of failing loudly.
"""

from __future__ import annotations

import pytest
import xxhash

from ingest import ids
from ingest.ids import (
    IdCollisionError,
    IdRegistry,
    MASK63,
    adv_id,
    adv_key,
    hash_key,
    lock_key,
    mnt_id,
    mnt_key,
    pkg_id,
    pkg_key,
    ver_id,
    ver_key,
)

GOLDEN = {
    "pkg:npm/react": 3382316030210051267,
    "pkg:npm/@tanstack/react-router": 3273979506645500504,
    "ver:npm/@tanstack/react-router@1.0.0": 518054545898303898,
    "mnt:npm/tannerlinsley": 2283015513641959383,
    "adv:GHSA-g7cv-rxg3-hmpx": 327051347632589624,
}


@pytest.mark.parametrize(("key", "expected"), GOLDEN.items())
def test_hash_key_matches_pinned_values(key: str, expected: int) -> None:
    assert hash_key(key) == expected


def test_id_helpers_agree_with_their_key_builders() -> None:
    assert pkg_id("react") == hash_key(pkg_key("react"))
    assert ver_id("react", "18.2.0") == hash_key(ver_key("react", "18.2.0"))
    assert mnt_id("alice") == hash_key(mnt_key("alice"))
    assert adv_id("GHSA-xxxx") == hash_key(adv_key("GHSA-xxxx"))


def test_key_formats() -> None:
    assert pkg_key("react") == "pkg:npm/react"
    # Scoped names pass through verbatim; the prefix is what separates namespaces.
    assert pkg_key("@tanstack/react-router") == "pkg:npm/@tanstack/react-router"
    assert ver_key("@tanstack/react-router", "1.0.1") == (
        "ver:npm/@tanstack/react-router@1.0.1"
    )
    assert ver_key("react", "19.0.0-rc.1") == "ver:npm/react@19.0.0-rc.1"
    assert mnt_key("tannerlinsley") == "mnt:npm/tannerlinsley"
    assert adv_key("GHSA-g7cv-rxg3-hmpx") == "adv:GHSA-g7cv-rxg3-hmpx"
    assert lock_key("abc123") == "lock:abc123"


def test_relationship_keys_are_endpoint_derived() -> None:
    # HydraDB will not merge a relationship on its endpoints alone, so the id
    # has to be deterministic or a second load would duplicate every edge.
    key = ids.rel_key("PKG_DEPENDS_ON", pkg_key("app-kit"), pkg_key("react"))
    assert key == "PKG_DEPENDS_ON:pkg:npm/app-kit->pkg:npm/react"
    assert ids.rel_id("PKG_DEPENDS_ON", pkg_key("app-kit"), pkg_key("react")) == hash_key(key)

    # Direction and type both matter: the reverse projection and the forward
    # edge between the same pair must not collide.
    forward = ids.rel_id("PKG_DEPENDS_ON", pkg_key("a"), pkg_key("b"))
    backward = ids.rel_id("PKG_DEPENDS_ON", pkg_key("b"), pkg_key("a"))
    reverse_type = ids.rel_id("PKG_DEPENDED_BY", pkg_key("a"), pkg_key("b"))
    assert len({forward, backward, reverse_type}) == 3


def test_masking_clears_the_top_bit() -> None:
    # This key's raw xxh64 digest has bit 63 set, so it would be a negative
    # signed integer and HydraDB would reject it as a node id.
    key = "pkg:npm/probe-0"
    raw = xxhash.xxh64(key.encode("utf-8")).intdigest()
    assert raw >> 63 == 1, "pinned probe key no longer exercises the top bit"
    assert hash_key(key) == raw & MASK63


def test_ids_stay_in_range_across_a_corpus() -> None:
    for i in range(5000):
        for node_id in (pkg_id(f"pkg-{i}"), ver_id(f"pkg-{i}", f"{i}.0.0")):
            assert 0 <= node_id <= MASK63


def test_unicode_and_punctuation_pass_through() -> None:
    assert mnt_key("björn") == "mnt:npm/björn"
    assert hash_key(mnt_key("björn")) == hash_key("mnt:npm/björn")


def test_registry_is_idempotent_per_key() -> None:
    registry = IdRegistry()
    first = registry.assign(pkg_key("react"))
    second = registry.assign(pkg_key("react"))
    assert first == second == pkg_id("react")
    assert len(registry) == 1
    assert pkg_key("react") in registry
    assert pkg_key("vue") not in registry


def test_registry_raises_on_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a collision rather than searching for a real one: the point is that
    # the loader stops, not that xxhash collides at this scale.
    monkeypatch.setattr(ids, "hash_key", lambda key: 42)
    registry = IdRegistry()
    registry.assign("pkg:npm/one")
    with pytest.raises(IdCollisionError) as excinfo:
        registry.assign("pkg:npm/two")

    error = excinfo.value
    assert error.node_id == 42
    assert {error.existing_key, error.new_key} == {"pkg:npm/one", "pkg:npm/two"}
    assert "pkg:npm/one" in str(error) and "pkg:npm/two" in str(error)
