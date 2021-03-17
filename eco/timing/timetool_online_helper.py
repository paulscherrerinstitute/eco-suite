
from bsread import source
from collections import deque
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.animation import FuncAnimation
from threading import Thread
from time import sleep
plt.ion()



class TtProcessor:
    def __init__(self,Nbg = 10):
          self.bg = deque([],Nbg)
          self.sig = deque([],1)
          self.spec_ppd = deque([],1)
          self.accumulator = Thread(target=self.run_continuously)
          self.accumulator.start()

    def run_continuously(self):
        with source(channels=['SARES20-CAMS142-M5.roi_signal_x_profile','SAR-CVME-TIFALL5:EvtSet']) as s:
            while True:
                m = s.receive()
                ix = m.data.pulse_id
              
                prof = m.data.data['SARES20-CAMS142-M5.roi_signal_x_profile'].value
                if prof is None:
                    continue

                codes = m.data.data['SAR-CVME-TIFALL5:EvtSet'].value
                if codes is None:
                    continue
                is_reference = codes[25]==1
                try:
                    if (lastgoodix-ix)>1:
                        print(f'missed  {lastgoodix-ix-1} events!')
                except:
                    pass
                lastgoodix = ix
                if is_reference:
                    self.bg.append(prof)
                else:
                    self.sig.append((prof / np.asarray(self.bg).mean(axis=0)))
                    self.spec_ppd.append(prof )
                    # print(f'pumped_id is {m.data.pulse_id}')
        
    def setup_plot(self):
        self.lh_sig = self.axs[1].plot(self.sig[-1])[0]
        self.lh_bg = self.axs[0].plot(np.asarray(self.bg).mean(axis=0))[0]
        self.lh_bg_last = self.axs[0].plot(self.bg[-1])[0]
        self.lh_sig_last = self.axs[0].plot(self.spec_ppd[-1])[0]

    def update_plot(self,dum):
        self.lh_sig.set_ydata(self.sig[-1])
        self.lh_bg.set_ydata(np.asarray(self.bg).mean(axis=0))
        self.lh_bg_last.set_ydata(self.bg[-1])
        self.lh_sig_last.set_ydata(self.spec_ppd[-1])
        return self.lh_sig

    def plot_animation(self,name='TT online ana',animate=True):
        if len(self.sig)<1:
            print('no signals yet')
            return
        self.fig,self.axs = plt.subplots(2,1,sharex=True,num=name)
        # self.fig.clf()
        # self.ax = self.fig.add_subplot(111)
        if animate:
            self.ani = FuncAnimation(self.fig,self.update_plot,init_func=self.setup_plot)
            plt.show()

