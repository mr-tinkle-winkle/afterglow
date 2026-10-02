# hands_bake -- the Hands indicator's traced frames

The clip indicator's Hands style is drawn from `afterglow/indicator/resources/hands_frames.json.gz`:
2D vector frames traced from a 3D model of the two gloves. This folder makes that file. The app
never imports anything here; it only reads the JSON (`afterglow/indicator/hands2d.py`).

## Pipeline

1. **Pose (3D).** `g3d.py` models a cartoon glove (palm slab, three fingers, a thumb, a cuff) from
   analytic primitives. `ready3d.py` builds the "button pressed" pose (hands apart),
   `clasp2.py` builds the clasp in the left glove's frame and closes every finger and thumb by
   contact (`pose3d.py`: clearances, `wrap_finger`, `close_chain`), so the hands interlock
   without passing through each other. `design.py` has the camera helpers.
2. **Animate.** `bake_hands.py` builds the timeline from `hands_spec.json`: ready (idle) ->
   wind-up -> swing -> contact -> clasp -> settle -> rest (idle). Poses are interpolated with
   `interp.lerp_pose` (slerp of each hand's frame, straight palm-centre path, blended joints).
   The camera turns from `cam_ready` to `cam_impact` degrees during the swing.
3. **Render.** `g3d.render` draws each frame into a z-buffer (depth, part and glove ids, normals,
   toon tone) and, with `g3d.CAPTURE = {}`, hands back those buffers.
4. **Trace.** `trace2d.trace_frame` turns the buffers into 2D shapes: the silhouette, fill
   regions per part (glove, cuff, rim, cuff end), shadow / deep-shadow / light shapes from the
   tone, and the ink lines (part boundaries and depth jumps, thinned to centre lines, with a
   weight from the size of the depth jump). `bake_hands.py` adds the stitching, the icon square
   on the back of the right glove (with the region where it shows), the shine spots, the palm
   centres for the swing's speed lines and the impact squash, then simplifies, quantises and
   writes everything (box units, x Q) as gzipped JSON.

## Run

    pip install numpy scipy scikit-image opencv-python-headless PySide6 pillow
    QT_QPA_PLATFORM=offscreen python bake_hands.py ../../afterglow/indicator/resources/hands_frames.json.gz

`PROCS=4` renders four frames at once (about 6 s per frame per process at the default
`BOX=300 SS=3`). `--quick` bakes every 15th frame only, for a fast look. The script prints the
worst clearance between the gloves per sequence (in glove sizes; slightly negative = soft
contact).

## Timing contract

`hands_spec.json` `timeline` = [wind-up end, swing end, contact, clasp closed, settled], in
seconds from the clap event. The app's `HANDS_WINDUP_MS + HANDS_SWING_MS` (`indicator/__init__.py`)
must equal the contact time (the clap sound is delayed by it); `tests/test_indicator_draw.py`
checks this against the file's `timing.contact_ms`.
