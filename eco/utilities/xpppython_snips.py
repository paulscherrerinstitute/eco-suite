# def tweak_new(motor, step=0.1):
#     help = "q = exit; up = step*2; down = step/2, left = neg dir, right = pos dir\n"
#     help = help + "g = go abs, s = set"
#     print "tweaking motor %s (pv=%s)" % (motor.name, motor.pvname)
#     print "current position %s" % (motor.wm_string())
#     step = float(step)
#     oldstep = 0
#     k = KeyPress.KeyPress()
#     while k.isq() is False:
#         if oldstep != step:
#             nstr = "stepsize: %f" % step
#             notice(nstr)
#             sys.stdout.flush()
#             oldstep = step
#         k.waitkey()
#         if k.isu():
#             step = step * 2.0
#         elif k.isd():
#             step = step / 2.0
#         elif k.isr():
#             motor.umvr(step, show_previous=False)
#         elif k.isl():
#             motor.umvr(-step, show_previous=False)
#         elif k.iskey("g"):
#             print "enter absolute position (char to abort go to)"
#             sys.stdout.flush()
#             v = sys.stdin.readline()
#             try:
#                 v = float(v.strip())
#                 motor.umv(v)
#             except:
#                 print "value cannot be converted to float, exit go to mode ..."
#                 sys.stdout.flush()
#         elif k.iskey("s"):
#             print "enter new set value (char to abort setting)"
#             sys.stdout.flush()
#             v = sys.stdin.readline()
#             try:
#                 v = float(v[0:-1])
#                 motor.set(v)
#             except:
#                 print "value cannot be converted to float, exit go to mode ..."
#                 sys.stdout.flush()
#         elif k.isq():
#             break
#         else:
#             print help
#     print "final position: %s" % motor.wm_string()
