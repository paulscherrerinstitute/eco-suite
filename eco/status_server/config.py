from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServerConfig:
    # Which namespace this instance serves - purely a label used in
    # responses/logging; the actual channel list comes from channel_file.
    namespace: str
    # Path to the alias->channel JSON file, see channel_registry.py.
    channel_file: str
    # Optional extra, alias-less PV inventory to merge in (pickle list or
    # newline-delimited text file) - e.g. a hand-maintained PV dump that
    # goes beyond the namespace's aliased channels (motor-record engineering
    # fields, timing-system channels, ...). See
    # channel_registry.load_flat_pv_list/merge_channels.
    supplementary_pv_list: str | None = None
    # Used to fill data_root_pattern below.
    instrument: str = "bernina"
    channeltypes: tuple = ("CA",)
    host: str = "0.0.0.0"
    port: int = 8090
    connection_timeout: float = 5.0
    # Where to write status.json / monitors.h5, in str.format() syntax with
    # {instrument}, {pgroup}, {run_number} available. Matches the directory
    # convention already used by daq_client.py's append_start_status_to_scan
    # and end_scan_monitors.
    data_root_pattern: str = (
        "/sf/{instrument}/data/{pgroup}/res/run_data/daq/run{run_number:04d}/aux"
    )

    @classmethod
    def from_file(cls, path: str | Path) -> "ServerConfig":
        data = json.loads(Path(path).read_text())
        return cls(**data)

    def data_dir(self, pgroup: str, run_number: int) -> Path:
        return Path(
            self.data_root_pattern.format(
                instrument=self.instrument, pgroup=pgroup, run_number=run_number
            )
        )


@dataclass
class NamespaceServerConfig:
    """Config for the namespace-hosted mode (namespace_store.py /
    namespace_server.py) - imports eco and hosts the real namespace object,
    as opposed to ServerConfig's bare PV-name registry. See
    namespace_store.py's module docstring for the trade-offs before
    choosing this mode."""

    # Dotted module path and attribute name to import the namespace from,
    # e.g. module_name="eco.bernina.bernina", attr_name="namespace".
    module_name: str
    attr_name: str = "namespace"
    # False (init literally everything) gives the most complete status
    # coverage but is slower and depends on more external systems being up;
    # True (init only the curated "required" subset, matching what a normal
    # interactive session does) is faster/more robust but a snapshot then
    # only covers whatever's been initialized.
    init_required_only: bool = False
    instrument: str = "bernina"
    host: str = "0.0.0.0"
    port: int = 8091
    data_root_pattern: str = (
        "/sf/{instrument}/data/{pgroup}/res/run_data/daq/run{run_number:04d}/aux"
    )

    @classmethod
    def from_file(cls, path: str | Path) -> "NamespaceServerConfig":
        data = json.loads(Path(path).read_text())
        return cls(**data)

    def data_dir(self, pgroup: str, run_number: int) -> Path:
        return Path(
            self.data_root_pattern.format(
                instrument=self.instrument, pgroup=pgroup, run_number=run_number
            )
        )
