# from eco import ecocnf
import logging

import eco

_logger = logging.getLogger(__name__)


def get_from_archive(Obj, attribute_name="pvname", force_type=None, remove_nulls=True):
    def _archiver_channels(self):
        """All channels of this object (ids, types and labels), from its
        aliases, falling back to the single `attribute_name` channel."""
        try:
            channels = self.alias.get_all()
            channel_ids = [_['channel'] for _ in channels]
            channel_types = [_.get('channeltype') for _ in channels]
            labels = [f'{_["alias"]} ({_["channel"]})' for _ in channels]
        except:
            channel_ids = [self.__dict__[attribute_name]]
            channel_types = None
            labels = [f"{self.alias.get_full_name()} ({channel_ids[0]})"]
        return channel_ids, channel_types, labels

    def _select_channels(channel_ids, channel_types, labels, name_selection):
        """Narrow the channel/type/label lists down to `name_selection`: a
        list of substrings matched case-insensitively against each channel's
        label, or `True` to pick interactively from a terminal checklist.
        Falls back to the full set on an empty/cancelled selection."""
        if not name_selection:
            return channel_ids, channel_types, labels
        if name_selection is True:
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
                if any(n.lower() in label.lower() for n in name_selection)
            ]
        if not chosen:
            _logger.info(
                "name_selection matched no channels - showing all %d instead",
                len(labels),
            )
            return channel_ids, channel_types, labels
        keep = set(chosen)
        channel_ids = [c for i, c in enumerate(channel_ids) if i in keep]
        channel_types = (
            [t for i, t in enumerate(channel_types) if i in keep]
            if channel_types is not None
            else None
        )
        labels = [l for i, l in enumerate(labels) if i in keep]
        return channel_ids, channel_types, labels

    def get_archiver_time_range(
        self, start=None, end=None, force_type=force_type, plot=True, **kwargs
    ):
        """Try to retrieve data within timerange from archiver. A time delta from now is assumed if end time is missing."""
        channel_ids, channel_types, labels = _archiver_channels(self)
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
        force_type=force_type,
        window=60,
        max_rate=5,
        duration=24 * 3600,
        labels=None,
        name_selection=None,
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
        `name_selection`: show only a subset of this object's channels - a
        list of substrings matched against each channel's label, or `True`
        to pick interactively from a terminal checklist.
        """
        channel_ids, channel_types, alias_labels = _archiver_channels(self)
        channel_ids, channel_types, alias_labels = _select_channels(
            channel_ids, channel_types, alias_labels, name_selection
        )
        return eco.defaults.ARCHIVER.strip_plot(
            channels=channel_ids,
            force_type=force_type,
            channel_types=None if force_type else channel_types,
            window=window,
            max_rate=max_rate,
            duration=duration,
            labels=labels if labels is not None else alias_labels,
            **kwargs,
        )

    Obj.get_archiver_time_range = get_archiver_time_range
    Obj.strip_plot = strip_plot
    return Obj
