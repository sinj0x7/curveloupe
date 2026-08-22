# CurveLoupe

A curve editor for DaVinci Resolve 

Started as help for a friend who was struggling with Resolve’s graphs. MIT licensed — free to use, modify, and share.

**Site + download:** [sinj0x7.github.io/kinetiq](https://sinj0x7.github.io/kinetiq)  
**Instagram:** [offgridkhaled](https://instagram.com/offgridkhaled)

Fusion’s spline editor is tiny. CurveLoupe opens the same curve in a large window, lets you drag the handles properly, then writes the result straight back onto your keyframes.

## Install

Download [CurveLoupe.zip](https://sinj0x7.github.io/kinetiq/CurveLoupe.zip), unzip it, and copy the whole `curveloupe` folder into Fusion’s scripts location:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Comp\` |
| macOS | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Comp/` |
| Linux | `~/.local/share/DaVinciResolve/Fusion/Scripts/Comp/` |

Restart Resolve. On the Fusion page: select a tool with an animated control, then **Workspace > Scripts > curveloupe > CurveLoupe**.

Windows needs [Python 3](https://www.python.org/downloads/) for Resolve scripting.

More detail is in [`curveloupe/README.md`](curveloupe/README.md).
