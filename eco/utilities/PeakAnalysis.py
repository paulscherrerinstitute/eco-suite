#!/usr/bin/env python
# -*- coding: latin-1 -*-
import scipy
from scipy.stats import skew
from scipy.interpolate import interp1d 
# from numpy import *
from pylab import *
import config

def PeakAnalysis(x,y,nb=3,plotpoints=False):
	""" nb = number of point (on each side) to use as background"""
	## get background
	xb = hstack((x[0:nb],x[-(nb):]))
	yb = hstack((y[0:nb],y[-(nb):]))
	a = polyfit(xb,yb,1)
	b = polyval(a,x)
	yf = y-b
	yd = diff(yf)

	## determine whether peak or step
	ispeak = abs(skew(yf))>abs(skew(yd))
	if ispeak:
		yw = yf
		xw = x
	else:
		yw = yd
		xw = (x[1:]+x[0:-1])/2
		## get background
		xwb = hstack((xw[0:nb],xw[-(nb):]))
		ywb = hstack((yw[0:nb],yw[-(nb):]))
		aw = polyfit(xwb,ywb,1)
		bw = polyval(aw,xw)
		yw = yw-bw
	

	Iw = (xw[1:]-xw[0:-1])*(yw[1:]+yw[0:-1])/2
	if sum(Iw)<0:
		yw = -yw

	## get parameters	
	mm = yw.argmax(0)
	PEAK = xw[mm]
	ywmax = yw[mm]
	gg = (yw[:mm][::-1]<(ywmax/2)).argmax()
	ip = interp1d(yw.take([mm-gg-1,mm-gg]),xw.take([mm-gg-1,mm-gg]),kind='linear')
	xhm1 = ip(ywmax/2)
	gg = (yw[mm:]<(ywmax/2)).argmax()
	ip = interp1d(yw.take([mm+gg,mm+gg-1]),xw.take([mm+gg,mm+gg-1]),kind='linear')
	xhm2 = ip(ywmax/2)

	FWHM = abs(xhm2-xhm1)
	CEN = (xhm2+xhm1)/2
	if plotpoints and ispeak is True:
		# plot the found points for center and FWHM edges
		ion()
		hold(True)
		plot(x,b,'g--')
		plot(x,b+ywmax,'g--')

		plot([xhm1,xhm1],polyval(a,xhm1)+[0,ywmax],'g--')
		plot([xhm2,xhm2],polyval(a,xhm2)+[0,ywmax],'g--')
		plot([CEN,CEN],polyval(a,CEN)+[0,ywmax],'g--')
		plot([xhm1,xhm2],[polyval(a,xhm1),polyval(a,xhm2)]+ywmax/2,'gx')


		draw()

	
	if (config.DEBUG):
		print "is peak ? %d" % ispeak
	if not ispeak:
		# findings start of step coming from left.
		std0 = scipy.std(y[0:nb])
		nt = nb
	
		while (scipy.std(y[0:nt])<(2*std0)) and (nt<len(y)):
			nt = nt+1
		lev0 = scipy.mean(y[0:nt])
	
		# findings start of step coming from right.
		std0 = scipy.std(y[-nb:])
		nt = nb

		while (scipy.std(y[-nt:])<(2*std0)) and (nt<len(y)):
			nt = nt+1
		lev1 = scipy.mean(y[-nt:])
	
		gg = abs(y-((lev0+lev1)/2)).argmin()     

		ftx = y[gg-2:gg+2]
		fty  = x[gg-2:gg+2]
		if ftx[-1]<ftx[0]:
			ftx = ftx[::-1]
			fty = fty[::-1]
	
		ip = interp1d(ftx,fty,kind='linear')
		CEN = ip((lev0+lev1)/2)
		
		gg = abs(y-(lev1+(lev0-lev1)*0.1195)).argmin()     

		ftx = y[gg-2:gg+2]
		fty  = x[gg-2:gg+2]
		if ftx[-1]<ftx[0]:
			ftx = ftx[::-1]
			fty = fty[::-1]
		#print " %f %f %f %f %f" % (ftx[0],ftx[1],fty[0],fty[1],lev1+(lev0-lev1)*0.1195)
		ip = interp1d(ftx,fty,kind='linear')
		H1 = ip((lev1+(lev0-lev1)*0.1195))
		#print "H1=%f" % H1

		gg = abs(y-(lev0+(lev1-lev0)*0.1195)).argmin()     

		ftx = y[gg-2:gg+2]
		fty  = x[gg-2:gg+2]
		
		if ftx[-1]<ftx[0]:
			ftx = ftx[::-1]
			fty = fty[::-1]
#		print " %f %f %f %f %f" % (ftx[0],ftx[1],fty[0],fty[1],lev0+(lev1-lev0)*0.1195)
		ip = interp1d(ftx,fty,kind='linear')
		H2 = ip((lev0+(lev1-lev0)*0.1195))
		#print "H2=%f" % abs(H2-H1)
		FWHM = abs(H2-H1)
		if plotpoints is True:
			# plot the found points for center and FWHM edges
			ion()
			hold(True)
			plot([x.min(),x.max()],[lev0,lev0],'g--')
			plot([x.min(),x.max()],[lev1,lev1],'g--')

			plot([H2,H2],[lev0,lev1],'g--')
			plot([H1,H1],[lev0,lev1],'g--')
			plot([CEN,CEN],[lev0,lev1],'g--')
			plot([H2,CEN,H1],[lev0+(lev1-lev0)*0.1195,(lev1+lev0)/2,lev1+(lev0-lev1)*0.1195],'gx')


			draw()

	return (CEN,FWHM,PEAK)

if (__name__ == "__main__"):
	x = arange(-10,10,1)
	sig = 2
	y = .00*x+exp(-x**2/2/sig**2)+multiply(0.15,(rand(x.shape[0])-0.5))
	x = x+22.473
	nb = 3
	(CEN,FWHM,PEAK) = PeakAnalysis(x,y,nb)
	## plotting results
	figh = figure(10)
	plot(x,y,'k.-')
	tit = 'CEN=%f, FWHM=%f, PEAK=%f' %(CEN,FWHM,PEAK)
	title(tit)
	figh.canvas.set_window_title('Scan analysis')
	show()

	## make step function
	yy = ones(x.shape[0]-1)
	for ii in (range(x.shape[0])[1:]):
		yy[ii-1] = sum(y[:ii])
		xx=x[1:];
	x=xx
	y=yy
	(CEN,FWHM,PEAK) = PeakAnalysis(x,y,nb)
	## plotting results
	fig = figure(11)
	plot(x,y,'k.-')
	tit = 'CEN=%f, FWHM=%f, PEAK=%f' %(CEN,FWHM,PEAK)
	title(tit)
	fig.canvas.set_window_title('Scan analysis')
	show()
