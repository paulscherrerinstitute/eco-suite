from bsread import source
from collections import deque
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.animation import FuncAnimation
from threading import Thread
from time import sleep
from scipy.special import erf

plt.ion()


class TtProcessor:
    def __init__(self, Nbg=10, ref_code=25, channel_proj = "SARES20-CAMS142-M5.roi_signal_x_profile"):
        self.channel_proj = channel_proj
        self.bg = deque([], Nbg)
        self.sig = deque([], 1)
        self.pos = deque([], 1)
        self.amp = deque([], 1)
        self.ref_code=ref_code
        self.spec_ppd = deque([], 1)
        self.accumulator = Thread(target=self.run_continuously)
        self.accumulator.start()
        

    def run_continuously(self):
        with source(
            channels=[
                self.channel_proj,
                "SAR-CVME-TIFALL5:EvtSet",
            ]
        ) as s:
            while True:
                m = s.receive()
                ix = m.data.pulse_id

                prof = m.data.data[self.channel_proj].value

                if prof is None:
                    continue

                codes = m.data.data["SAR-CVME-TIFALL5:EvtSet"].value
                if codes is None:
                    continue
                is_reference = codes[self.ref_code] == 1
                try:
                    if (lastgoodix - ix) > 1:
                        print(f"missed  {lastgoodix-ix-1} events!")
                except:
                    pass
                lastgoodix = ix
                if is_reference:
                    self.bg.append(prof)
                else:
                    self.sig.append((prof / np.asarray(self.bg).mean(axis=0)))
                    self.spec_ppd.append(prof)
                    pos, amp = self.analyze_step()
                    self.pos.append(pos)
                    self.amp.append(amp)
                    # print(f'pumped_id is {m.data.pulse_id}')

    def setup_plot(self):
        self.lh_sig = self.axs[1].plot(self.sig[-1])[0]
        self.lh_pos = self.axs[1].axvline(self.pos[-1])
        self.lh_bg = self.axs[0].plot(np.asarray(self.bg).mean(axis=0))[0]
        self.lh_bg_last = self.axs[0].plot(self.bg[-1])[0]
        self.lh_sig_last = self.axs[0].plot(self.spec_ppd[-1])[0]

    def update_plot(self, dum):
        self.lh_sig.set_ydata(self.sig[-1])
        self.lh_pos.set_xdata([self.pos[-1]] * 2)
        self.lh_bg.set_ydata(np.asarray(self.bg).mean(axis=0))
        self.lh_bg_last.set_ydata(self.bg[-1])
        self.lh_sig_last.set_ydata(self.spec_ppd[-1])
        return self.lh_sig

    def plot_animation(self, name=None, animate=True):
        if name is None:
            name = "TT online ana" + self.channel_proj
        if len(self.sig) < 1:
            print("no signals yet")
            return
        self.fig, self.axs = plt.subplots(2, 1, sharex=True, num=name)
        # self.fig.clf()
        # self.ax = self.fig.add_subplot(111)
        if animate:
            self.ani = FuncAnimation(
                self.fig, self.update_plot, init_func=self.setup_plot
            )
            plt.show()

    def analyze_step(self):
        ref = calc_ref()
        return find_signal(self.spec_ppd[-1][::-1], ref)


def calc_ref(width_px=100, reflen=200):
    rng = reflen / width_px
    ref = -erf(np.linspace(-rng, rng, reflen)) / 2
    ref = normstep(ref)
    return ref


def normstep(step):
    """normalizing a test signal for np.correlate"""
    step = step - np.mean(step)
    step = step / np.sum(step ** 2)
    return step


def get_max(c, px_w=None):
    """getting maximum from a correlation curve (optionally using polynomial fit)"""
    im = c.argmax()
    mx = c[im]

    if px_w:
        order = 2
        i_f = max(0, im - (px_w // 2))
        i_t = min(im + (px_w // 2))
        x = np.arange(i_f, i_t)
        y = c[i_f:i_t]
        p = np.polyfit(x, y, order)
        dp = np.polyder(p)
        im = -dp[1] / dp[0]
    return im, mx


def find_signal(d, ref):
    """finding signal ref in d.
    ref is expected to be properly normalized
    return position is corrected to to center location of the reference signal (as found in signal d)"""
    # need to invert both to get correct direction
    x0 = (len(ref) + 1) // 2
    c = np.correlate(d, ref, "valid")
    if False:
        plt.figure("debug plot")
        plt.plot(c)
    p, mx = get_max(c)

    return p + x0, mx
