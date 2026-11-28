# light_meter

Measures light through the Mac's camera over time, so a change to a physical device — a lamp
changing colour, a timer firing — can be checked from a recording rather than by someone watching.
It verified the Sunset projector's scenes, timers and white-channel tests
([`docs/sunset_projector/`](../../docs/sunset_projector/README.md)).

- `capture.swift` records JPEG frames from the default camera at a fixed rate, with wall-clock
  timestamps and each frame's mean colour.
- `region.swift` measures the mean colour of one square region in every frame of a capture.
- `windows.py`, `changes.py` and `timeline.py` line those measurements up with events, such as the
  times commands were sent.

No automated tests. Last checked by hand on 2026-11-27: a 2-second capture at 2 fps on a MacBook
Pro camera (1920×1080), `region.swift` on that capture, and each Python script on its output.

## Building

`capture.swift` needs camera permission, so it runs from an app bundle (see the
[workbench README](../README.md)). On the macOS host, with Xcode or its command-line tools:

```bash
workbench/light_meter/build.sh <outdir>
```

That writes `<outdir>/LightMeter.app`.

## Recording

```bash
open -W -n --stdout <log> --stderr <log> <outdir>/LightMeter.app \
  --args <capture-dir> <seconds> <fps> [lock]
```

The first run asks for camera permission. `lock` waits about 2 seconds for auto-exposure to settle
and then locks exposure and white balance, if the camera supports it. A capture directory holds
`fNNNN.jpg` frames, `frames.csv` (`epoch,file,r,g,b,luma`) and `cam.log`.

## Measuring

Pick a region by looking at a frame, then:

```bash
swift workbench/light_meter/region.swift \
  <capture-dir> <cx> <cy> <half-size> >region.csv
```

`cx` and `cy` are the region's centre in pixels from the top-left corner; the region is
`2 × half-size` pixels square. Its CSV (`epoch,file,r,g,b`) feeds the Python scripts, run with
`python3 -I`:

- `windows.py region.csv <t0> name@start-end ...`: mean, standard deviation and range of luma per
  window, in seconds after `t0`.
- `changes.py region.csv <target>`: each jump in luma, timed relative to `target`.
- `timeline.py <t0> <every-nth-row> name@seconds ... <region.csv`: each row labelled with the
  latest event before it.

## Getting good readings

- **Measure what the light falls on, not the light itself.** A lamp in the frame saturates to 255;
  a lit wall beside it shows colour and brightness changes clearly.
- **The built-in MacBook camera can't lock exposure or white balance**, so auto-exposure rescales
  brightness as the scene changes. Compare readings within one recording and look for steps, not
  absolute values.
- **Turn off Center Stage.** It reframes the shot to follow people, which moves the region.
- **Frame size can change mid-capture.** One recording produced 1920×1080 frames, then 640×480
  after another app opened the camera. Check a frame's size before reusing region coordinates.
- **Captures can include people in the room.** Keep them out of the repository.
