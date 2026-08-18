// Handheld pendant enclosure for the Pi Zero 2 W manual-control box.
// Parametric master (clean manifold, round holes, screw posts).
//
// Render printable STLs:
//   openscad -o pendant_tray.stl      -D 'part="tray"'      pendant.scad
//   openscad -o pendant_faceplate.stl -D 'part="faceplate"' pendant.scad
//
// FIRST ARTICLE: dimensions are estimates from published part sizes.
// Print the faceplate alone first, check it against your real display /
// thumbstick / encoder, then tune the variables below.

part = "both";   // "tray" | "faceplate" | "both"
$fn = 64;

// ---- dimensions in mm (VERIFY) ----
wall     = 2.5;
floor_t  = 2.0;
face_thk = 2.5;
depth_in = 36;    // Pi + PiSugar 3+ (5000mAh) + display standoffs (was 24 for 1200mAh)

face_w = 148;
face_h = 76;

// display window (visible area of the 4" 480x320 panel)
win_w = 85; win_h = 57;
win_x0 = 5; win_y0 = (face_h - win_h) / 2;

// right-hand control column
col_cx = (win_x0 + win_w + (face_w - wall)) / 2;   // ~118
joy_d  = 28;  joy_cy = face_h * 0.66;              // thumbstick cap clearance
enc_d  = 7.5; enc_cy = face_h * 0.25;              // encoder shaft/nut

// USB cable exit
usb_w = 16; usb_h = 8; usb_cx = win_x0 + win_w/2;

// corner screw posts
post_r  = 3.2;
screw_r = 1.1;   // pilot for M2.5 self-tapping
inset   = 5;
posts = [[inset,inset],[face_w-inset,inset],
         [inset,face_h-inset],[face_w-inset,face_h-inset]];

// ventilation (passive convection; pair with a stick-on SoC heatsink)
vent_w = 3; vent_l = 16; vent_step = 9;

module floor_vents() {
  for (x = [24 : vent_step : 92])
    translate([x, face_h/2 - vent_l/2, -1])
      cube([vent_w, vent_l, floor_t + 2]);
}
module sidewall_vents() {
  // slots high on both long side walls for cross-flow
  for (side = [wall/2 - 1, face_w - wall/2 - vent_w + 1])
    for (y = [18 : 12 : face_h - 18])
      translate([side, y, floor_t + depth_in - 14])
        cube([vent_w + 1, 8, 10]);
}
module faceplate_edge_vents() {
  for (yc = [win_y0 - 4, win_y0 + win_h + 4])
    for (x = [12 : 11 : 78])
      translate([x, yc - 1.75, -1]) cube([6, 3.5, face_thk + 2]);
}

module tray() {
  difference() {
    union() {
      // outer shell with inner cavity
      difference() {
        cube([face_w, face_h, floor_t + depth_in]);
        translate([wall, wall, floor_t])
          cube([face_w - 2*wall, face_h - 2*wall, depth_in + 1]);
      }
      // screw posts rising from the floor
      for (p = posts)
        translate([p[0], p[1], floor_t])
          cylinder(r = post_r, h = depth_in);
    }
    // USB notch in the front (y=0) wall
    translate([usb_cx - usb_w/2, -1, floor_t + depth_in - usb_h])
      cube([usb_w, wall + 2, usb_h + 1]);
    // ventilation
    floor_vents();
    sidewall_vents();
    // pilot holes down the posts
    for (p = posts)
      translate([p[0], p[1], floor_t + depth_in - 8])
        cylinder(r = screw_r, h = 10);
  }
}

module faceplate() {
  difference() {
    cube([face_w, face_h, face_thk]);
    translate([win_x0, win_y0, -1])
      cube([win_w, win_h, face_thk + 2]);
    translate([col_cx, joy_cy, -1]) cylinder(d = joy_d, h = face_thk + 2);
    translate([col_cx, enc_cy, -1]) cylinder(d = enc_d, h = face_thk + 2);
    // ventilation slots in the top/bottom margins
    faceplate_edge_vents();
    // corner screw clearance holes
    for (p = posts)
      translate([p[0], p[1], -1]) cylinder(r = screw_r + 0.5, h = face_thk + 2);
  }
}

if (part == "tray") tray();
else if (part == "faceplate") faceplate();
else {
  tray();
  translate([0, 0, floor_t + depth_in + 6]) faceplate();  // exploded preview
}
