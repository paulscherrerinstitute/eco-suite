# from eco import ecocnf
import eco


def get_from_archive(Obj, attribute_name="pvname", force_type=None, remove_nulls=True):
    def get_archiver_time_range(
        self, start=None, end=None, force_type=force_type, plot=True, **kwargs
    ):
        """Try to retrieve data within timerange from archiver. A time delta from now is assumed if end time is missing."""
        try:
            channels = self.alias.get_all()
            channel_ids = [_['channel'] for _ in channels]
            labels = [f'{_["alias"]} ({_["channel"]})' for _ in channels]


        except:
            channel_ids = [self.__dict__[attribute_name]]
            labels = [f"{self.alias.get_full_name()} ({channel_ids[0]})"]
        
        data = eco.defaults.ARCHIVER.get_data_time_range(
            channels=channel_ids,
            start=start,
            end=end,
            plot=plot,
            force_type=force_type,
            labels=labels,
            **kwargs,
        )

        channel_ids_found = [_ for _ in channel_ids if _ in data.keys()]
        channels_found = filter(lambda x: x in channel_ids_found, channels)
        if remove_nulls:
            data = data.dropna(how='all').dropna(how='all', axis=1)
            channel_ids_missing = [_ for _ in channel_ids if _ not in data.keys()]
            channels_missing = filter(lambda x: x in channel_ids_missing, channels)
        

        
        return data#.rename(columns={channelname: "data"})

    Obj.get_archiver_time_range = get_archiver_time_range
    return Obj
