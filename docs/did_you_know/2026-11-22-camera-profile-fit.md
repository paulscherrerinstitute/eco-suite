---
added: 2026-11-22
tags: [ui, cameras]
details: live_gui
---

# The camera viewer fits the beam, like pshell's screen panel

**Profile** and **Fit** in a camera viewer draw the projections and their
Gaussian fits over the image; **Settings → Fit** picks the camera server's
own fit, a fit of what the viewer shows, or the "good region" fit. The
numbers are in the session too, also as escape Streams with pulse ids.

```python
v = cam_north.widget(show_fit=True, show_profile=True)
v.fit["x"]["mean"], v.fit["x"]["sigma"]
v.streams("fit_x_mean")["fit_x_mean"].plot()
```

More: {doc}`../live_gui`
