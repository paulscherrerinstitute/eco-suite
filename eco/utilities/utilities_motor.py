import numpy as np
from eco.devices_general.adjustable import AdjustableError
from time import sleep


def find_limit(
    adjustable_motor, direction, search_interval, abs_move_limit=9e5, speed=None
):
    lims_old = adjustable_motor.get_limits()
    direction = np.sign(direction)
    abs_move_limit = np.abs(abs_move_limit)
    lims_new = list(lims_old) + []

    curr_pos = adjustable_motor.get_current_value()

    print(
        (not np.sum(np.sign(np.asarray(lims_old) - curr_pos)) == 0),
        ((np.asarray(lims_old) - curr_pos) == 0).any(),
    )
    if (not np.sum(np.sign(np.asarray(lims_old) - curr_pos)) == 0) or (
        (np.asarray(lims_old) - curr_pos) == 0
    ).any():
        print("Need to change limits to start with")
        adjustable_motor.set_limits(*(curr_pos * np.asarray([-1, 1]) * abs_move_limit))

    if direction == -1:
        pos_check = -1 * (abs_move_limit)
        lims_new[0] = pos_check - abs(search_interval)
    elif direction == 1:
        pos_check = 1 * (abs_move_limit)
        lims_new[1] = pos_check + abs(search_interval)
    adjustable_motor.set_limits(*lims_new)
    sleep(0.2)
    print(pos_check)
    try:
        adjustable_motor.set_target_value(pos_check).wait()
        input("test")
    except AdjustableError as e:
        print(e)
        test_pos = adjustable_motor.get_current_value()
        in_limit = True

    while in_limit:
        try:
            test_pos = test_pos - direction * np.abs(search_interval)
            adjustable_motor.set_target_value(test_pos).wait()
            in_limit = False
            limit_pos = test_pos + direction * np.abs(search_interval)
        except AdjustableError as e:
            print(e)
            test_pos = adjustable_motor.get_current_value()
            in_limit = True

    if direction == -1:
        lims_new[0] = limit_pos
    elif direction == 1:
        lims_new[1] = limit_pos
    adjustable_motor.set_limits(*lims_new)
    sleep(0.2)

    return limit_pos


def find_limits(adjustable_motor, search_interval, abs_move_limit=9e5, speed=None):
    currpos = adjustable_motor.get_current_value()
    limits = []
    for direction in (-1, 1):
        limits.append(
            find_limit(
                adjustable_motor,
                direction,
                search_interval,
                abs_move_limit=abs_move_limit,
                speed=speed,
            )
        )

    adjustable_motor.set_target_value(currpos).wait()
    return limits


def find_set_and_report_limits(adj, search_interval=0.1):
    limits = find_limits(adj, search_interval)
    alias_name = adj.alias.get_full_name()
    pvname = adj.Id
    print(f'{alias_name} "{pvname}" soft limits: {limits}')


def limitfinder_bernina():
    from eco import bernina

    adjs = [
        bernina.slit_switch.up,
        bernina.slit_switch.down,
        bernina.slit_switch.left,
        bernina.slit_switch.right,
        bernina.slit_und._ax1,
        bernina.slit_und._ax2,
        bernina.slit_und._ay1,
        bernina.slit_und._ay2,
        bernina.slit_und._bx1,
        bernina.slit_und._bx2,
        bernina.slit_und._by1,
        bernina.slit_und._by2,
        bernina.slit_att.hgap,
        bernina.slit_att.hpos,
        bernina.slit_att.vgap,
        bernina.slit_att.vpos,
        bernina.mon_und.diode_x,
        bernina.mon_und.diode_y,
        bernina.mon_und.target_pos,
        bernina.mon_opt.x_diodes,
        bernina.mon_opt.y_diodes,
        bernina.mon_opt.target_y,
        bernina.mon_att.diode_x,
        bernina.mon_att.diode_y,
        bernina.mon_att.target_pos,
        bernina.prof_fe.target_pos,
        bernina.prof_mirr_alv1.target_pos,
        bernina.prof_mirr1.target_pos,
        bernina.prof_mono.target_pos,
        bernina.prof_opt.target_pos,
        bernina.prof_att.target_pos,
        bernina.att.motor_1,
        bernina.att.motor_2,
        bernina.att.motor_3,
        bernina.att.motor_4,
        bernina.att.motor_5,
        bernina.att.motor_6,
    ]
    for adj in adjs:
        try:
            print(adj.alias.get_full_name(), adj.Id, adj.get_limits())
        except:
            print(adj)
