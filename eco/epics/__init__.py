from eco import ecocnf


def get_from_archive(Obj, attribute_name="pvname", force_type=None):
    def get_archiver_time_range(
        self, start=None, end=None, force_type=force_type, plot=True, **kwargs
    ):
        """Try to retrieve data within timerange from archiver. A time delta from now is assumed if end time is missing."""
        channelname = self.__dict__[attribute_name]
        data = ecocnf.archiver.get_data_time_range(
            channels=[channelname],
            start=start,
            end=end,
            plot=plot,
            force_type=force_type,
            labels=[f"{self.alias.get_full_name()} ({channelname})"],
            **kwargs,
        )
        return data.rename(columns={channelname: "data"})

    Obj.get_archiver_time_range = get_archiver_time_range
    return Obj
