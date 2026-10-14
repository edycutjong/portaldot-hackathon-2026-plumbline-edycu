"""Chain profiles and runtime metadata: connect, identify, fetch, store, decode.

A *profile* names one runtime Plumbline measures against and says how the SDK must be built for it.
Every measured row later carries the profile name plus the spec version and metadata hash read here,
so a number can never drift away from the runtime it came from.

Profiles:
  mainnet-1002  Portaldot mainnet, spec 1002, metadata V13. Read-only. Needs the SDK's `legacy` type
                preset; Assets amounts are u64 under the same V13 name `Balance` that Balances uses as
                u128, so Assets calls need a dedicated SDK instance (see `assets_instance`).
  v3            Portaldot V3 testnet (the judged target). Built with no type preset. Its identity is
                not pinned yet: the endpoint did not resolve when this profile was written.
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


@dataclass(frozen=True)
class Profile:
    name: str
    url: str | None
    preset: str | None
    spec_names: tuple[str, ...]
    genesis_hash: str | None = None  # None = not pinned (dev chains, or not yet observed)
    writes_txs: bool = False
    label: str = ""
    stored_file: str | None = None


PROFILES: dict[str, Profile] = {
    "mainnet-1002": Profile(
        name="mainnet-1002",
        url="wss://mainnet.portaldot.io",
        preset="legacy",
        spec_names=("portaldot",),
        # observed via chain_getBlockHash(0) on 2026-10-14
        genesis_hash="0x83b5212c69f85f3996f8696bb35bf913af51b176a5f53d0a74fd3a83dc0b8a54",
        label="Portaldot mainnet, spec 1002 (read-only baseline)",
    ),
    "v3": Profile(
        name="v3",
        url="wss://testnetv3-node.feso-apps.xyz",
        preset=None,
        # third-party measured, not yet confirmed by this tool; the genesis hash is pinned on first contact
        spec_names=("portaldotV3",),
        writes_txs=True,
        label="Portaldot V3 testnet",
    ),
    "interim-sdk": Profile(
        name="interim-sdk",
        url="ws://127.0.0.1:39944",
        preset=None,
        spec_names=("node",),
        writes_txs=True,
        label="interim-sdk: local polkadot-sdk substrate-node --dev (stand-in, not V3)",
    ),
}


def get_profile(name: str) -> Profile:
    """Resolve a profile name, including `stored:<file>[@<base-profile>]`."""
    if name.startswith("stored:"):
        spec = name[len("stored:"):]
        path, _, base = spec.partition("@")
        if not path:
            raise ProfileError("stored: profile needs a file path, e.g. stored:fixtures/metadata_1002.scale")
        if not Path(path).exists():
            raise ProfileError(f"stored metadata file not found: {path}")
        if base:
            preset = get_profile(base).preset
        else:  # no base profile named: V13 blobs need the legacy preset, V14+ are self-describing
            preset = "legacy" if metadata_version(Path(path).read_bytes()) < 14 else None
        return Profile(name=name, url=None, preset=preset, spec_names=(), label=f"stored blob {path}",
                       stored_file=path)
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
    block_hash: str | None = None  # the block the metadata was read at (None = chain head at fetch time)
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
    substrate = SubstrateInterface(**kwargs)
    substrate.init_runtime()
    verify_identity(profile, substrate)
    return substrate


def verify_identity(profile: Profile, substrate) -> tuple[str, str]:
    version = substrate.rpc_request("state_getRuntimeVersion", [])["result"]
    genesis = substrate.rpc_request("chain_getBlockHash", [0])["result"]
    check_identity(profile, version.get("specName", ""), genesis)
    return version.get("specName", ""), genesis


def check_identity(profile: Profile, spec_name: str, genesis_hash: str) -> None:
    if profile.spec_names and spec_name not in profile.spec_names:
        raise UnknownChainError(
            f"profile {profile.name} expects spec_name in {profile.spec_names}, endpoint reports {spec_name!r}")
    if profile.genesis_hash and genesis_hash.lower() != profile.genesis_hash.lower():
        raise UnknownChainError(
            f"profile {profile.name} expects genesis {profile.genesis_hash}, endpoint reports {genesis_hash}")


def assets_instance(profile: Profile, url: str | None = None):
    """A separate SDK instance for Assets calls on mainnet-1002 (Assets `Balance` is u64 there).

    The override has to be applied after `init_runtime()`: init re-applies the preset and silently
    drops an override set earlier. It changes `Balance` for the whole instance, so never share this
    instance with Balances calls.
    """
    substrate = connect(profile, url)
    if profile.name == "mainnet-1002":
        substrate.runtime_config.update_type_registry_types({"Balance": "u64"})
    return substrate


def fetch_metadata_blob(substrate, block_hash: str | None = None) -> bytes:
    """Raw metadata at a block (default: head). Metadata is not always block-invariant: on the
    interim-sdk chain a Revive storage entry's default value changes from block to block, so a
    stored blob from that chain is only reproducible when it is pinned to a block."""
    params = [block_hash] if block_hash else []
    return bytes.fromhex(substrate.rpc_request("state_getMetadata", params)["result"][2:])


def runtime_info(profile: Profile, substrate, blob: bytes, endpoint: str | None = None,
                 block_hash: str | None = None) -> RuntimeInfo:
    at = [block_hash] if block_hash else []
    version = substrate.rpc_request("state_getRuntimeVersion", at)["result"]
    genesis = substrate.rpc_request("chain_getBlockHash", [0])["result"]
    code_hash = substrate.rpc_request("state_getStorageHash", [CODE_KEY] + at)["result"]
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
