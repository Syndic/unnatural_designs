# light_meter

Measures light through the Mac's camera over time, so a change to a physical device — a lamp
changing colour, a timer firing — can be checked from a recording rather than by someone watching.
It was used to verify the projector's scenes, timers and white-channel tests.

- `capture.swift` records JPEG frames from the default camera at a fixed rate, with wall-clock
  timestamps and each frame's mean colour.
- `region.swift` measures the mean colour of one square region in every frame of a capture.
- `windows.py`, `changes.py` and `timeline.py` line those measurements up with events, such as the
  times commands were sent.

These are research aids: nothing builds or tests them in CI.

## Building

macOS lets a process use the camera only if its app bundle declares `NSCameraUsageDescription`,
and it judges a binary started from a shell by the app that launched it. So `capture.swift` runs
from a minimal app bundle. On the macOS host, with Xcode or its command-line tools:

```bash
docs/sunset_projector/scripts/light_meter/build.sh <outdir>
```

That writes `<outdir>/LightMeter.app`, ad-hoc signed.

## Recording

```bash
open -W -n --stdout <log> --stderr <log> <outdir>/LightMeter.app \
  --args <capture-dir> <seconds> <fps> [lock]
```

The first run asks for camera permission, and so may the first run after each rebuild, since the
ad-hoc signature changes. `lock` waits about 2 seconds for auto-exposure to settle
and then locks exposure and white balance, if the camera supports it. A capture directory holds
`fNNNN.jpg` frames, `frames.csv` (`epoch,file,r,g,b,luma`) and `cam.log`.

## Measuring

Pick a region by looking at a frame, then:

```bash
swift docs/sunset_projector/scripts/light_meter/region.swift \
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
