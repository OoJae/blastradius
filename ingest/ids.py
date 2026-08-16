"""The ID contract. Imported by the loader and the API — one source of truth.

HydraDB node identity is a non-negative integer `id` property: `MERGE` and
`MATCH` both resolve nodes by it, and it cannot be changed once set. Our
entities are named by strings, so every natural key is hashed to a 63-bit
integer here, and the readable key is carried alongside as a string property
`key` (which is also what `algo.MSpaths` seeds against via `sourceProperty`).

Hashing is deterministic across processes and machines, so the loader and the
API can both compute a node's id without a lookup table or a round trip.
"""

from __future__ import annotations

import xxhash

# 63 bits: the top bit is cleared so every id is non-negative, which HydraDB
# requires ("node id cannot be negative").
MASK63 = 0x7FFF_FFFF_FFFF_FFFF

ECOSYSTEM = "npm"


class IdCollisionError(RuntimeError):
    """Two distinct natural keys hashed to the same id.

    At a few million keys this is unlikely (~1e-6), but a silent collision
    would merge two packages into one node and quietly corrupt every traversal
    that touches them, so the loader fails loudly instead.
    """

    def __init__(self, node_id: int, existing_key: str, new_key: str) -> None:
        super().__init__(
            f"id {node_id} is claimed by {existing_key!r} and {new_key!r}; "
            "salt one of the keys to break the tie"
        )
        self.node_id = node_id
        self.existing_key = existing_key
        self.new_key = new_key


def hash_key(key: str) -> int:
    """Hash a natural key to a non-negative 63-bit integer."""
    return xxhash.xxh64(key.encode("utf-8")).intdigest() & MASK63


# --- natural keys ---------------------------------------------------------
# Scoped npm names (@tanstack/react-router) pass through unchanged; the prefix
# is what keeps the namespaces apart, not any escaping of the name itself.


def pkg_key(name: str) -> str:
    return f"pkg:{ECOSYSTEM}/{name}"


def ver_key(name: str, version: str) -> str:
    return f"ver:{ECOSYSTEM}/{name}@{version}"


def mnt_key(username: str) -> str:
    return f"mnt:{ECOSYSTEM}/{username}"


def adv_key(advisory_id: str) -> str:
    """`advisory_id` is a GHSA or OSV id, which is already globally unique."""
    return f"adv:{advisory_id}"


def lock_key(sha256_hex: str) -> str:
    """`sha256_hex` is the digest of an uploaded lockfile."""
    return f"lock:{sha256_hex}"


def rel_key(rel_type: str, src_key: str, dst_key: str) -> str:
    """Natural key for a relationship.

    HydraDB requires a batched relationship MERGE to carry an explicit
    `id: row.<field>`; a relationship cannot be merged on its endpoints alone.
    Deriving that id from the endpoints keeps re-runs idempotent instead of
    duplicating an edge on every load.
    """
    return f"{rel_type}:{src_key}->{dst_key}"


# --- ids ------------------------------------------------------------------


def pkg_id(name: str) -> int:
    return hash_key(pkg_key(name))


def ver_id(name: str, version: str) -> int:
    return hash_key(ver_key(name, version))


def mnt_id(username: str) -> int:
    return hash_key(mnt_key(username))


def adv_id(advisory_id: str) -> int:
    return hash_key(adv_key(advisory_id))


def lock_id(sha256_hex: str) -> int:
    return hash_key(lock_key(sha256_hex))


def rel_id(rel_type: str, src_key: str, dst_key: str) -> int:
    return hash_key(rel_key(rel_type, src_key, dst_key))


class IdRegistry:
    """Tracks key-to-id assignments for one load so collisions surface early."""

    def __init__(self) -> None:
        self._by_id: dict[int, str] = {}

    def assign(self, key: str) -> int:
        node_id = hash_key(key)
        existing = self._by_id.get(node_id)
        if existing is None:
            self._by_id[node_id] = key
        elif existing != key:
            raise IdCollisionError(node_id, existing, key)
        return node_id

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, key: str) -> bool:
        return self._by_id.get(hash_key(key)) == key
