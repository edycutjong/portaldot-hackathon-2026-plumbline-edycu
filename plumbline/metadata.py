"""Chain profiles and runtime metadata: connect, identify, fetch, store, decode.

A *profile* names one runtime Plumbline measures against and says how the SDK must be built for it.
Every measured row later carries the profile name plus the spec version and metadata hash read here,
so a number can never drift away from the runtime it came from.

Profiles:
  mainnet-1002  Portaldot mainnet, spec 1002, metadata V13. Read-only. Needs the SDK's `legacy` type
                preset; Assets amounts are u64 under the same V13 name `Balance` that Balances uses as
                u128, so Assets calls need a dedicated SDK instance (see `assets_instance`).
  v3            Portaldot V3 testnet (the judged target). Built with no type preset. Identity pinned from the
                live endpoint on 2026-10-20: spec `portaldot-v3`/3002, genesis 0xd4f9acbc…5283bd.
  interim-sdk   Local upstream polkadot-sdk `substrate-node --dev` used while V3 is down.
                A stand-in, not V3. No type preset.
  stored:<file> A metadata blob on disk, decoded offline with the preset of the profile it came from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scalecodec.base import RuntimeConfigurationObject, ScaleBytes
from scalecodec.type_registry import load_type_registry_preset

CODE_KEY = "0x3a636f6465"  # well-known storage key ":code"


class UnknownChainError(RuntimeError):
    """The endpoint answered, but it is not the chain the profile describes."""


class ProfileError(ValueError):
    """The profile name is not one Plumbline knows."""


class ChainUnreachableError(RuntimeError):
    """The profile's endpoint could not be reached."""


@dataclass(frozen=True)
class Profile:
    name: str
    url: str | None
    preset: str | None
    spec_names: tuple[str, ...]
    genesis_hash: str | None = None  # None = not pinned (not yet observed)
    spec_versions: tuple[int, ...] = ()  # () = any spec version
    writes_txs: bool = False
    label: str = ""
    stored_file: str | None = None
    measured_as: str | None = None  # for stored: profiles, the live profile this repo recorded the blob from
    asserted_as: str | None = None  # for stored: profiles, an `@profile` suffix on a blob with no recorded origin


PROFILES: dict[str, Profile] = {
    "mainnet-1002": Profile(
        name="mainnet-1002",
        url="wss://mainnet.portaldot.io",
        preset="legacy",
        spec_names=("portaldot",),
        # observed via chain_getBlockHash(0) on 2026-10-14
        genesis_hash="0x83b5212c69f85f3996f8696bb35bf913af51b176a5f53d0a74fd3a83dc0b8a54",
        spec_versions=(1002,),  # the legacy preset is only known to be right for this spec
        label="Portaldot mainnet, spec 1002 (read-only baseline)",
    ),
    "v3": Profile(
        name="v3",
        url="wss://test-node-v3.portaldot.dev",
        preset=None,
        # observed via state_getRuntimeVersion / chain_getBlockHash(0) on 2026-10-20 (matches the organiser's
        # 2026-10-19 announcement); the tree had expected `portaldotV3`/3000/`tPOTv3` on the retired feso-apps.xyz
        spec_names=("portaldot-v3",),
        genesis_hash="0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd",
        spec_versions=(3002,),
        writes_txs=True,
        label="Portaldot V3 testnet, spec 3002 (the judged target)",
    ),
    "interim-sdk": Profile(
        name="interim-sdk",
        url="ws://127.0.0.1:39944",
        preset=None,
        spec_names=("node",),
        # genesis of `--dev` on polkadot-stable2606-3, observed 2026-10-14
        genesis_hash="0x0c4402ef4ccc34d28b16523e1cf26831716527c61a65d8e0840ab26e1636c8a2",
        spec_versions=(268,),
        writes_txs=True,
        label="interim-sdk: local polkadot-sdk substrate-node --dev (stand-in, not V3)",
    ),
}


# Metadata blobs this repository has recorded (sha256 -> the live profile it was read from).
# A stored blob is labelled from this table. An `@profile` suffix can only choose the preset of an
# unrecorded blob; its label then says the origin is asserted, and evidence producers refuse it.
KNOWN_BLOBS: dict[str, str] = {
    "b87080f9349f936e859748df90ca7837d1e2d15cb02abba347499b8639869053": "mainnet-1002",  # fixtures/metadata_1002.scale
    "9732aff0f6b558576c1551596852448cdf5d1e9ac58705f0ab286d84e73728db": "interim-sdk",   # fixtures/metadata_standin_sdk.scale
    "381014358cc17ba6274f4de8e54846534f0986f311e27ac696cbd8af5696394c": "v3",            # fixtures/metadata_v3.scale
}


def get_profile(name: str) -> Profile:
    """Resolve a profile name, including `stored:<file>[@<live-profile>]`."""
    if name.startswith("stored:"):
        spec = name[len("stored:"):]
        path, sep, base = spec.rpartition("@")
        if not sep or base not in PROFILES:  # no suffix, or an `@` that is part of the path
            path, base = spec, ""
        if not path:
            raise ProfileError("stored: profile needs a file path, e.g. stored:fixtures/metadata_1002.scale")
        if not Path(path).exists():
            raise ProfileError(f"stored metadata file not found: {path}")
        blob = Path(path).read_bytes()
        known = KNOWN_BLOBS.get(sha256_hex(blob))
        if base and known and base != known:
            raise ProfileError(f"{path} was recorded from {known}, not {base}")
        if known:
            live = PROFILES[known]
            preset, label = live.preset, f"{live.label}; stored blob {path}"
        elif base:  # origin asserted on the command line, not recorded by this repository
            preset = PROFILES[base].preset
            label = f"stored blob {path} (asserted {base}; origin not recorded)"
        else:  # unknown origin: V13 blobs need the legacy preset, V14+ are self-describing
            try:
                preset = "legacy" if metadata_version(blob) < 14 else None
            except ValueError as exc:
                raise ProfileError(f"{path}: {exc}") from None
            label = f"stored blob {path} (origin not recorded)"
        return Profile(name=name, url=None, preset=preset, spec_names=(), label=label,
                       stored_file=path, measured_as=known, asserted_as=None if known else (base or None))
    try:
        return PROFILES[name]
    except KeyError:
        known = ", ".join(sorted(PROFILES)) + ", stored:<file>"
        raise ProfileError(f"unknown profile {name!r}; known: {known}") from None


@dataclass
class RuntimeInfo:
    profile: str
    endpoint: str | None
    spec_name: str
    spec_version: int
    impl_name: str
    tx_version: int
    metadata_version: int
    metadata_sha256: str
    metadata_bytes: int
    genesis_hash: str | None = None
    code_hash: str | None = None
    block_hash: str | None = None  # the block every field of this record was read at
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def connect(profile: Profile, url: str | None = None):
    """Open an SDK connection built the way the profile requires, then verify chain identity.

    Raises UnknownChainError when the endpoint is some other chain (wrong genesis or spec_name):
    measuring the wrong runtime silently is the failure this tool exists to prevent.
    """
    from substrateinterface import SubstrateInterface

    endpoint = url or profile.url
    if not endpoint:
        raise ProfileError(f"profile {profile.name} has no endpoint")
    kwargs = {"url": endpoint}
    if profile.preset:
        kwargs["type_registry_preset"] = profile.preset
    try:
        substrate = SubstrateInterface(**kwargs)
    except Exception as exc:  # the SDK raises several unrelated types for DNS / socket failures
        raise ChainUnreachableError(f"profile {profile.name}: cannot reach {endpoint}: {exc}") from exc
    try:
        try:
            verify_identity(profile, substrate)  # plain RPC reads, before any type decoding
        except UnknownChainError:
            raise
        except Exception as exc:
            raise ChainUnreachableError(f"profile {profile.name}: {endpoint} did not answer RPC: {exc}") from exc
        substrate.init_runtime()
    except Exception:
        substrate.close()
        raise
    return substrate


def recorded_origin(blob: bytes) -> str | None:
    """The live profile this repository recorded the blob from, or None."""
    return KNOWN_BLOBS.get(sha256_hex(blob))


def verify_identity(profile: Profile, substrate) -> tuple[str, str]:
    version = substrate.rpc_request("state_getRuntimeVersion", [])["result"]
    genesis = substrate.rpc_request("chain_getBlockHash", [0])["result"]
    check_identity(profile, version.get("specName", ""), genesis, version.get("specVersion"))
    return version.get("specName", ""), genesis


def check_identity(profile: Profile, spec_name: str, genesis_hash: str, spec_version: int | None = None) -> None:
    if profile.spec_names and spec_name not in profile.spec_names:
        raise UnknownChainError(
            f"profile {profile.name} expects spec_name in {profile.spec_names}, endpoint reports {spec_name!r}")
    if profile.genesis_hash and genesis_hash.lower() != profile.genesis_hash.lower():
        raise UnknownChainError(
            f"profile {profile.name} expects genesis {profile.genesis_hash}, endpoint reports {genesis_hash}")
    if profile.spec_versions and spec_version is not None and spec_version not in profile.spec_versions:
        raise UnknownChainError(
            f"profile {profile.name} expects spec_version in {profile.spec_versions}, endpoint reports {spec_version} "
            f"(runtime upgraded? the profile's type handling is only known to be right for the listed versions)")


def assets_instance(profile: Profile, url: str | None = None):
    """A separate SDK instance for Assets calls on mainnet-1002 (Assets `Balance` is u64 there).

    The override has to be applied after `init_runtime()`: init re-applies the preset and silently
    drops an override set earlier. It changes `Balance` for the whole instance, so never share this
    instance with Balances calls.
    """
    if profile.name != "mainnet-1002":
        raise ProfileError(f"the Assets u64 override is a mainnet-1002 workaround; profile {profile.name} needs none")
    substrate = connect(profile, url)
    substrate.runtime_config.update_type_registry_types({"Balance": "u64"})
    return substrate


def pinned_head(substrate) -> str:
    """Resolve the finalized head once, so every read of one record comes from the same block."""
    return substrate.rpc_request("chain_getFinalizedHead", [])["result"]


def fetch_metadata_blob(substrate, block_hash: str | None = None) -> bytes:
    """Raw metadata at a block (default: head). Metadata is not always block-invariant: on the
    interim-sdk chain a Revive storage entry's default value changes from block to block, so a
    stored blob from that chain is only reproducible when it is pinned to a block."""
    params = [block_hash] if block_hash else []
    return bytes.fromhex(substrate.rpc_request("state_getMetadata", params)["result"][2:])


def runtime_info(profile: Profile, substrate, blob: bytes, block_hash: str, endpoint: str | None = None) -> RuntimeInfo:
    """Runtime facts read at `block_hash`: the same block the metadata blob must have been read at."""
    version = substrate.rpc_request("state_getRuntimeVersion", [block_hash])["result"]
    genesis = substrate.rpc_request("chain_getBlockHash", [0])["result"]
    code_hash = substrate.rpc_request("state_getStorageHash", [CODE_KEY, block_hash])["result"]
    return RuntimeInfo(
        profile=profile.name,
        endpoint=endpoint or profile.url,
        spec_name=version["specName"],
        spec_version=version["specVersion"],
        impl_name=version.get("implName", ""),
        tx_version=version.get("transactionVersion", 0),
        metadata_version=metadata_version(blob),
        metadata_sha256=sha256_hex(blob),
        metadata_bytes=len(blob),
        genesis_hash=genesis,
        code_hash=code_hash,
        block_hash=block_hash,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def metadata_version(blob: bytes) -> int:
    """Read the version byte after the `meta` magic."""
    if blob[:4] != b"meta":
        raise ValueError("not a SCALE metadata blob (missing 'meta' magic)")
    return blob[4]


def decode_metadata(blob: bytes, preset: str | None = None):
    """Decode a stored metadata blob offline. V13 needs the preset of its profile (legacy for 1002)."""
    config = RuntimeConfigurationObject()
    config.update_type_registry(load_type_registry_preset("core"))
    if preset:
        config.update_type_registry(load_type_registry_preset(preset))
    metadata = config.create_scale_object("MetadataVersioned", data=ScaleBytes(blob))
    metadata.decode()
    if metadata_version(blob) >= 14:
        config.add_portable_registry(metadata)
    return metadata


def call_surface(metadata) -> dict[str, list[tuple[str, str]]]:
    """`Pallet.call_name` -> [(arg name, arg type)] for every dispatchable call in the metadata."""
    surface: dict[str, list[tuple[str, str]]] = {}
    for pallet in metadata.pallets:
        for call in pallet.calls or []:
            surface[f"{pallet.name}.{call.name}"] = [(arg.name, str(arg.type)) for arg in call.args]
    return dict(sorted(surface.items()))


def pallet_names(metadata) -> list[str]:
    return [pallet.name for pallet in metadata.pallets]


def write_evidence(path: Path, info: RuntimeInfo, blob_path: Path) -> dict:
    record = info.to_dict() | {"blob_file": blob_path.as_posix(), "label": get_profile(info.profile).label}
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record
