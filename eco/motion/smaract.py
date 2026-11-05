from ..devices_general.motors import SmaractRecord
from ..elements.adjustable import AdjustableTrigger
from ..elements.assembly import Assembly


class SmaractController(Assembly):
    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        self.all_stages = []
        for n in range(1, 19):
            self._append(
                SmaractRecord,
                f"{pvbase}{n}",
                name=f"stage{n}",
                is_setting=True,
                is_display=True,
            )
            self.all_stages.append(self.__dict__[f"stage{n}"])
        self.set_autozero_on()
        self._append(
            AdjustableTrigger, self.home_all, name="home_all", button_label="Home All"
        )

    def home_all(self):
        # not implemented yet -- kept as the no-op it already was; wiring
        # it in as a trigger only makes the existing (currently empty)
        # action discoverable/consistent, not new hardware sequencing
        pass

    def _stage(self, stage):
        if isinstance(stage, int):
            return self.__dict__[f"stage{stage}"]
        if isinstance(stage, str):
            return self.__dict__[stage]
        return stage

    def startup(self, stages, wait=True, **kwargs):
        """Run SmaractRecord.startup_stage (sensor type, max frequency, calibration,
        homing) on several stages at the same time.

        stages: stage numbers (1..18), names ("stage4") or the stage objects.
        kwargs go to SmaractRecord.startup_stage (sensor_type=, max_frequency=,
        calibrate=, home=, timeouts, verbose=).

        With wait=True the call returns {stage name: result or exception}
        once all have finished, and raises if any failed (after all
        finished, so one broken channel does not leave the others
        unattended). With wait=False it returns {stage name: thread};
        thread.join() gives the result or raises."""
        threads = {}
        for stage in stages:
            stg = self._stage(stage)
            threads[stg.name] = stg.startup_stage(wait=False, **kwargs)
        if not wait:
            return threads
        results = {}
        for name, thread in threads.items():
            try:
                results[name] = thread.join()
            except Exception as e:
                results[name] = e
        failed = {n: r for n, r in results.items() if isinstance(r, Exception)}
        if failed:
            raise RuntimeError(
                "startup failed for "
                + ", ".join(f"{n} ({type(e).__name__}: {e})" for n, e in failed.items())
            )
        return results

    def diagnose(self, stages=None, query_controller=False, verbose=True):
        """Why do channels not work? For all stages (or the given numbers /
        names / objects): read-only EPICS checks, and with
        query_controller=True also the controller's own answers (channel
        count, status, sensor, position) through the IOC's asyn record --
        read-only get commands only. Also flags whole 3-channel modules
        failing together. Prints a report, returns a list of dicts.
        See eco.motion.smaract_diagnose."""
        from .smaract_diagnose import diagnose_channels

        stages = self.all_stages if stages is None else stages
        return diagnose_channels(
            [self._stage(s).pvname for s in stages],
            query_controller=query_controller,
            verbose=verbose,
        )

    def set_autozero_on(self):
        for stg in self.all_stages:
            stg.motor_parameters.autozero_on_homing(1)
