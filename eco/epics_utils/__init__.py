# from eco import ecocnf
import logging

import eco

_logger = logging.getLogger(__name__)


def get_from_archive(Obj, attribute_name="pvname", force_type=None, remove_nulls=True):
    def _archiver_channels(self):
        """All channels of this object (ids, types and labels), from its
        aliases, falling back to the single `attribute_name` channel.

        A sibling "unit" sub-component - whether it's an in-memory string
        with no channel of its own, or a real channel such as a ".EGU" PV -
        is never itself included as a channel to plot; it's metadata, not a
        value, and would just show up as an empty/noisy legend entry. Its
        current string is read once instead and appended to the label(s) of
        the channel(s) it belongs alongside, e.g. "readback (SATES...)
        [mbar]".

        Also returns each channel's own leaf attribute name (e.g.
        "readback", "speed", "direction") alongside its id/type/label -
        `strip_plot`'s `readback_only` uses it to tell "the" value apart
        from everything else an Assembly happens to expose as its own
        channel."""

        def _owner(path):
            obj = self
            for part in path[1:-1]:
                obj = getattr(obj, part)
            return obj

        try:
            channels = self.alias.get_all()
            parsed = [(c, c["alias"].split(".")) for c in channels]
            value_entries = [(c, path) for c, path in parsed if path[-1] != "unit"]
            unit_entries = [(c, path) for c, path in parsed if path[-1] == "unit"]

            units = {}
            for c, path in unit_entries:
                try:
                    units[".".join(path[1:-1])] = getattr(
                        _owner(path), "unit"
                    ).get_current_value()
                except Exception:
                    pass
            for c, path in value_entries:
                key = ".".join(path[1:-1])
                if key in units:
                    continue
                try:
                    unit_obj = getattr(_owner(path), "unit", None)
                    if unit_obj is not None and hasattr(unit_obj, "get_current_value"):
                        units[key] = unit_obj.get_current_value()
                except Exception:
                    pass

            channel_ids = [c["channel"] for c, _ in value_entries]
            channel_types = [c.get("channeltype") for c, _ in value_entries]
            leaf_names = [path[-1] for _, path in value_entries]
            labels = []
            for c, path in value_entries:
                label = f'{c["alias"]} ({c["channel"]})'
                unit = units.get(".".join(path[1:-1]))
                if unit:
                    label += f" [{unit}]"
                labels.append(label)
        except:
            channel_ids = [self.__dict__[attribute_name]]
            channel_types = None
            leaf_names = [attribute_name]
            label = f"{self.alias.get_full_name()} ({channel_ids[0]})"
            try:
                unit_obj = getattr(self, "unit", None)
                if unit_obj is not None and hasattr(unit_obj, "get_current_value"):
                    unit = unit_obj.get_current_value()
                    if unit:
                        label += f" [{unit}]"
            except Exception:
                pass
            labels = [label]
        return channel_ids, channel_types, labels, leaf_names

    def _select_channels(channel_ids, channel_types, labels, select_names, leaf_names=None):
        """Narrow the channel/type/label(/leaf_name) lists down to
        `select_names`: a list of substrings matched case-insensitively
        against each channel's label, or `True` to pick interactively from a
        terminal checklist. Falls back to the full set on an empty/cancelled
        selection."""
        if not select_names:
            return channel_ids, channel_types, labels, leaf_names
        if select_names is True:
            from simple_term_menu import TerminalMenu

            terminal_menu = TerminalMenu(
                labels,
                multi_select=True,
                show_multi_select_hint=True,
                title="Select channels",
            )
            terminal_menu.show()
            chosen = terminal_menu.chosen_menu_indices or []
        else:
            chosen = [
                i
                for i, label in enumerate(labels)
                if any(n.lower() in label.lower() for n in select_names)
            ]
        if not chosen:
            _logger.info(
                "select_names matched no channels - showing all %d instead",
                len(labels),
            )
            return channel_ids, channel_types, labels, leaf_names
        keep = set(chosen)
        channel_ids = [c for i, c in enumerate(channel_ids) if i in keep]
        channel_types = (
            [t for i, t in enumerate(channel_types) if i in keep]
            if channel_types is not None
            else None
        )
        labels = [l for i, l in enumerate(labels) if i in keep]
        leaf_names = (
            [n for i, n in enumerate(leaf_names) if i in keep]
            if leaf_names is not None
            else None
        )
        return channel_ids, channel_types, labels, leaf_names

    def get_archiver_time_range(
        self, start=None, end=None, force_type=force_type, plot=True, **kwargs
    ):
        """Try to retrieve data within timerange from archiver. A time delta from now is assumed if end time is missing."""
        channel_ids, channel_types, labels, _leaf_names = _archiver_channels(self)
        channels = channel_ids

        data = eco.defaults.ARCHIVER.get_data_time_range(
            channels=channel_ids,
            start=start,
            end=end,
            plot=plot,
            force_type=force_type,
            channel_types=None if force_type else channel_types,
            labels=labels,
            **kwargs,
        )
        if data is None:
            return data

        channel_ids_found = [_ for _ in channel_ids if _ in data.keys()]
        channels_found = filter(lambda x: x in channel_ids_found, channels)
        if remove_nulls:
            data = data.dropna(how='all').dropna(how='all', axis=1)
            channel_ids_missing = [_ for _ in channel_ids if _ not in data.keys()]
            channels_missing = filter(lambda x: x in channel_ids_missing, channels)



        return data#.rename(columns={channelname: "data"})

    def strip_plot(
        self,
        *extra_monitorables,
        force_type=force_type,
        window=60,
        max_rate=5,
        duration=24 * 3600,
        labels=None,
        select_names=None,
        readback_only=True,
        **kwargs,
    ):
        """Open a live, rolling strip plot of all this object's channels,
        streamed from the dispatcher/EPICS. Same channel selection as
        `get_archiver_time_range`. Returns a handle whose `.stop()` ends it.

        `window`: seconds of history shown in the plot.
        `max_rate`: redraw rate cap in Hz, independent of the data rate.
        `duration`: how long (seconds) the underlying live stream stays open.
        `labels`: legend label per channel; defaults to this object's alias
        labels.
        `select_names`: show only a subset of this object's channels - a
        list of substrings matched against each channel's label, or `True`
        to pick interactively from a terminal checklist.
        `readback_only` (default `True`): an Assembly can expose plenty of
        channels beyond its actual value - limits, speed, enable flags,
        status, ... - which just makes for a noisy plot. When one of this
        object's channels is named "readback" (the usual convention for
        "the" value), only that one is monitored to start with; every other
        channel is instead offered behind a "More channels..." button on the
        plot, which opens a checkbox picker to add any of them live,
        on request. Has no effect when there is no "readback" channel (then
        all channels are shown as before), or when extra monitorables are
        also passed (below) - those are always shown in full.
        `step` (default `True`, via `**kwargs`): draw each channel as a step
        plot - a sample holds constant until the next one - rather than a
        plain connect-the-dots line; see `DataHub.strip_plot`. `grid`
        (default `True`, via `**kwargs`): show axis gridlines.

        Extra monitorables (anything satisfying `eco.elements.protocols.
        Detector` - has `get_current_value()`; an Adjustable, a Detector, a
        virtual/derived value, EPICS-backed or not) are added to the *same*
        plot alongside this object's own channels, polled rather than
        EPICS-monitored - see `eco.utilities.strip_plot.strip_plot`. Two
        ways to pass them: positionally (`mirror.strip_plot(vacuum.pressure)`
        - named from that object's own alias if it has one, else `.name`,
        else `repr()`), or as a keyword whose value satisfies `Detector`
        (`mirror.strip_plot(pressure=vacuum.pressure)` - named from the
        keyword instead). Mix freely.
        """
        from ..elements.protocols import is_detector
        from ..utilities.strip_plot import _monitorable_name

        extra_kwargs = {k: v for k, v in kwargs.items() if is_detector(v)}
        for name in extra_kwargs:
            del kwargs[name]
        extra_names = [_monitorable_name(m) for m in extra_monitorables] + list(
            extra_kwargs
        )
        extra_values = list(extra_monitorables) + list(extra_kwargs.values())
        channel_ids, channel_types, alias_labels, leaf_names = _archiver_channels(self)
        channel_ids, channel_types, alias_labels, leaf_names = _select_channels(
            channel_ids, channel_types, alias_labels, select_names, leaf_names=leaf_names
        )
        own_labels = labels if labels is not None else alias_labels
        if extra_values:
            from ..utilities.strip_plot import strip_plot as _generic_strip_plot

            return _generic_strip_plot(
                monitorables=extra_values,
                channels=channel_ids,
                force_type=force_type,
                channel_types=None if force_type else channel_types,
                labels=extra_names + list(own_labels),
                window=window,
                max_rate=max_rate,
                duration=duration,
                **kwargs,
            )
        primary_idx = list(range(len(channel_ids)))
        if readback_only and "readback" in leaf_names:
            primary_idx = [i for i, n in enumerate(leaf_names) if n == "readback"]
        extra_idx = [i for i in range(len(channel_ids)) if i not in primary_idx]
        extra_channels = [
            (channel_ids[i], channel_types[i] if channel_types else None, own_labels[i])
            for i in extra_idx
        ]
        return eco.defaults.ARCHIVER.strip_plot(
            channels=[channel_ids[i] for i in primary_idx],
            force_type=force_type,
            channel_types=(
                None
                if force_type or channel_types is None
                else [channel_types[i] for i in primary_idx]
            ),
            window=window,
            max_rate=max_rate,
            duration=duration,
            labels=[own_labels[i] for i in primary_idx],
            extra_channels=extra_channels,
            **kwargs,
        )

    Obj.get_archiver_time_range = get_archiver_time_range
    Obj.strip_plot = strip_plot
    return Obj
