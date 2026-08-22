# Kinetiq

> Made by [sinj0x7](https://github.com/sinj0x7). Started as a help for a friend
> who was struggling with DaVinci Resolve's graphs. MIT licensed — completely
> free to use, modify, and share. See `LICENSE.txt`.

An oversized bezier curve editor for DaVinci Resolve / Fusion.

Fusion's native spline editor is small and demands constant zooming for
precise handle adjustments. Kinetiq opens a large floating window (built
with Fusion's own UIManager toolkit — no browser, no Electron, no external
app) where you can shape one keyframe segment comfortably, then write the
result straight back onto the tool's BezierSpline via the scripting API.

## Requirements

- DaVinci Resolve 17+ (Free or Studio) or Fusion Studio 16+
- The Python 3 environment Resolve is configured to use
  (Resolve: `Preferences > System > General > Script language: Python 3`)
- No external Python packages — stdlib only

## Install

Copy the **whole `kinetiq` folder** (all files, not just `Kinetiq.py`)
into Fusion's `Scripts:/Comp` folder:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Comp\` |
| macOS (user) | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Comp/` |
| macOS (all users) | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Comp/` |
| Linux | `~/.local/share/DaVinciResolve/Fusion/Scripts/Comp/` (or `/opt/resolve/Fusion/Scripts/Comp/`) |

For standalone Fusion Studio, use the equivalent `Fusion/Scripts/Comp`
folder (`%APPDATA%\Blackmagic Design\Fusion\Scripts\Comp\` on Windows).

You should end up with:

```
...\Scripts\Comp\kinetiq\Kinetiq.py
...\Scripts\Comp\kinetiq\curve_math.py
...\Scripts\Comp\kinetiq\presets.py
...\Scripts\Comp\kinetiq\README.md
```

`Scripts:/Edit` also works if you prefer the script visible from the Edit
page menu, but the script itself operates on the current Fusion comp, so
`Scripts:/Comp` is recommended.

## Run

1. Open the **Fusion page** (a composition must be open).
2. Select the tool whose animated control you want to shape, e.g. a
   Transform with an animated Size. The control must already be animated
   with a keyframe spline (right-click the control > **Animate**) and have
   at least two keyframes.
3. Menu: **Workspace > Scripts > kinetiq > Kinetiq**.

## Usage

- On launch, Kinetiq reads the selected (active) tool, lists all of its
  BezierSpline-animated controls in the dropdown, and loads the first
  keyframe segment onto the canvas.
- **Canvas** — the curve is shown normalized: x is the 0–1 time fraction of
  the segment, y is the 0–1 fraction of the value range (the y axis extends
  beyond 0–1 so overshoot/anticipation curves are visible). Drag the two
  amber endpoint squares (vertically) or the two round tangent handles.
  Hit radius is generous by design — no zooming needed.
- **Numeric fields** — X/Y fields for every point, two-way synced with the
  canvas.
- **Keyboard** — click a point to select it, then nudge with the arrow keys
  (0.01 per press, 0.05 with Shift held).
- **Presets** — Linear, Overshoot, Anticipate, and Ease In / Out / In-Out at
  25/50/75/100% strength load directly onto the canvas.
- **Segment** — a spline with n keyframes has n−1 segments; pick which one
  to edit with the Segment spinner (the label shows its frame/value range).
- **Load from Selected** — re-reads the currently selected tool in Fusion.
- **Apply to Fusion** — writes the curve back onto the spline. The change
  appears instantly in Fusion's own spline editor, and it is undoable in
  Fusion (one undo step).

## Notes & troubleshooting

- *"Select a tool with an animated control…"* — make sure a tool is
  actually selected (active) in the Fusion flow and at least one of its
  controls is animated with a keyframe spline (BezierSpline).
- Applying strips the Linear/Step flag from the two keyframes of the edited
  segment (those flags would override bezier handles).
- If the two keyframes have the same value (a flat segment), the y axis has
  no natural scale; Kinetiq then treats y edits as raw value offsets and
  says so in the status line.
- Handle X positions are clamped inside the segment when writing, since
  Fusion requires a key's right handle to sit at or after the key and the
  left handle at or before it.
- Different Resolve/Fusion versions disagree about whether `SetKeyFrames()`
  handle coordinates are absolute or relative to the keyframe. Kinetiq
  writes, reads back to verify, and automatically retries with the other
  convention, so both behave correctly.
- The canvas is rendered as a PNG shown on a native widget (UIManager has
  no paint canvas); frames go to your temp folder and are cleaned up
  automatically.
