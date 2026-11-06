# GuideBro

Photoshop-style 2D and 3D guides for Blender's camera and viewport.

GuideBro is a Blender extension (Blender 4.2 or newer). It adds a **GuideBro** tab
to the 3D View sidebar (N panel).

## Features

- **2D guides:** horizontal, vertical or tilted lines drawn over the camera frame
  (or the whole viewport). Each guide has two points, A and B: drag either point to
  turn the guide about the other, or drag the line to move it.
- **Guide groups:** named sets of 2D guides kept per scene. Each camera shows one
  group, so a group can be reused by several cameras, and a duplicated camera keeps
  its guides.
- **3D guides:** infinite lines in world space that stay put when you orbit. Place
  them along an axis, along a mesh edge, through two clicked points (snap to two
  vertices to join them), or convert a 2D guide. Point A, Point B, heading and pitch
  are editable as numbers.
- **Snapping while dragging guides:** to the frame grid, object origins, on-screen
  bounds or mesh vertices (toggles in the Snapping panel, or hold Ctrl).
- **Rulers:** Photoshop-style rulers in camera view, in render pixels. Drag out of
  the top ruler for a horizontal guide, out of the left one for a vertical guide.
- **Frame grid:** optional visible grid, with square (1:1) cells if you like.
- **Objects snap to guides:** while you move objects with Blender's own Move (G, the
  Move tool or its gizmo), their origin, bounds or vertices snap to nearby guides.
  In Edit Mode, the vertices you move snap too.
- Labels with name, pixel position, percentage and angle, with adjustable size.

## Install

Download the latest zip from
[Releases](https://github.com/kokotron/guidebro/releases), then in Blender use
**Edit > Preferences > Get Extensions > Install from Disk** (or drag the zip into
Blender).

## Shortcuts

- **Ctrl+Alt+;** toggles Edit Guides.
- **Ctrl+Alt+'** hides or shows all guides.

Change them under Preferences > Keymap.

## Building

From the repository root:

```
blender --command extension build
```
## Special thanks
Special thanks to @sotoszogos for testing and feedback

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
