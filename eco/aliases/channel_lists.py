"""Compare the DAQ's recorded-channel lists against the live namespace.

``channels_JF`` / ``channels_BS`` / ``channels_BSCAM`` (each an
``AdjustableFS``-backed list of channel names, see ``eco.bernina.bernina_daq``)
are what ``sf_daq_broker`` is actually told to write for a run -- see
``eco.acquisition.daq_client.Daq.retrieve``. They are maintained by hand and
can drift from the namespace: a device gets added/renamed/removed in eco
without the corresponding entry being added/renamed/removed from these
files, silently changing what a run records.

The namespace itself already knows every channel it *could* record, via
each initialized component's ``.alias`` (``eco.aliases.Alias``, tagged with
a ``channeltype`` of ``"JF"``/``"BS"``/``"BSCAM"``/``"CA"`` depending on
where it comes from -- ``eco.detector.jungfrau.Jungfrau``,
``eco.bs.detector``, ``eco.detector.detectors_psi``'s camera classes). This
module compares the two: what the initialized namespace has that a
recorded list is missing, and what a recorded list has that no initialized
namespace component currently backs.

Only channels_JF/channels_BS/channels_BSCAM are covered for now -
channels_CA has no "compare against the live namespace" story yet.

Shared between ``eco.acquisition.daq_client.Daq.compare_channels()`` (local
namespace) and ``eco.status_server``'s ``/channels/compare`` endpoint
(remote, already-initialized namespace), so the two stay in lockstep
without duplicating the diff logic.
"""

CHANNEL_LIST_CHANNELTYPES = {
    "channels_JF": "JF",
    "channels_BS": "BS",
    "channels_BSCAM": "BSCAM",
}


def required_channels_by_type(alias_list):
    """Group an ``Alias.get_all()``-shaped list (``[{"alias", "channel",
    "channeltype"}, ...]``) into ``{channeltype: [channel, ...]}``."""
    out = {}
    for entry in alias_list:
        out.setdefault(entry["channeltype"], []).append(entry["channel"])
    return out


def compare_channel_lists(alias_list, recorded_by_list, list_names=None):
    """Compare the namespace's currently-initialized channels against the
    DAQ's recorded-channel lists.

    alias_list: ``Alias.get_all()``-shaped list from the (initialized part
        of the) namespace, e.g. ``namespace.alias.get_all()``.
    recorded_by_list: ``{"channels_JF": [...], "channels_BS": [...], ...}``
        -- the current value of each channels_* list. Only include a list
        here if it could actually be resolved; leaving one out skips it
        rather than reporting every required channel of that type as
        missing.
    list_names: which of ``CHANNEL_LIST_CHANNELTYPES`` to compare (default:
        all of them). Ignored for a name missing from `recorded_by_list`.

    Returns ``{list_name: {"channeltype", "missing", "exceeding",
    "n_required", "n_recorded"}}``.

    "missing": channels a currently-initialized namespace component of the
    matching channeltype provides but the recorded list does not -- these
    will not land in the run's raw data.
    "exceeding": channels in the recorded list that no currently-
    initialized namespace component of that channeltype backs -- still
    recorded, but not traceable to a currently-live device (could be a
    stale/renamed entry, or simply a component nobody has initialized in
    this session/server yet).
    """
    required = required_channels_by_type(alias_list)
    list_names = list_names or list(CHANNEL_LIST_CHANNELTYPES)
    result = {}
    for list_name in list_names:
        channeltype = CHANNEL_LIST_CHANNELTYPES.get(list_name)
        if channeltype is None or list_name not in recorded_by_list:
            continue
        req = set(required.get(channeltype, []))
        rec = set(recorded_by_list[list_name] or [])
        result[list_name] = {
            "channeltype": channeltype,
            "missing": sorted(req - rec),
            "exceeding": sorted(rec - req),
            "n_required": len(req),
            "n_recorded": len(rec),
        }
    return result
