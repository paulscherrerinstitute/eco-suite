from eco import ecocnf


def get_from_archive(Obj, attribute_name="pvname"):
    def get_archiver_time_range(self, start=None, end=None, plot=True, **kwargs):
        """Try to retrieve data within timerange from archiver. A time delta from now is assumed if end time is missing."""
        channelname = self.__dict__[attribute_name]
        return ecocnf.archiver.get_data_time_range(
            channels=[channelname],
            start=start,
            end=end,
            plot=plot,
            **kwargs,
        )

    def strip_plot(self, **kwargs):
        """Open a live, rolling strip plot of this object's channel, streamed
        from the dispatcher. Returns a handle whose `.stop()` ends it."""
        channelname = self.__dict__[attribute_name]
        return ecocnf.archiver.strip_plot(channels=[channelname], **kwargs)

    Obj.get_archiver_time_range = get_archiver_time_range
    Obj.strip_plot = strip_plot
    return Obj
