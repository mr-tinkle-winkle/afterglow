"""
Shared ordered list of named "keyframes" -- checkpoints in the
hotkey-to-library clip capture pipeline (see clips.trigger_clip()).

This is deliberately its own tiny module rather than living inside
clips.py or config.py: it's used to key BOTH the per-keyframe sound
settings below (Advanced Sound / Error Noise, in config.py's
AppSettings) AND -- per how this was actually asked for -- is meant to
be the same list a future animation-trigger system reuses once that
exists, rather than that system inventing its own separate copy of
"the stages of a clip capture" that could drift out of sync with this
one. Keep both features (and any future one) importing from here.
"""
from __future__ import annotations

HOTKEY_RECEIVED = "hotkey_received"
REPLAY_BUFFER_SENT = "replay_buffer_sent"
REPLAY_BUFFER_COMPLETED = "replay_buffer_completed"
TRIM_FINISHED = "trim_finished"
CLEANED_UP_MOVED = "cleaned_up_moved"

# (key, human-readable label), in actual pipeline order -- Settings UI
# and any future keyframe-ordered UI should iterate this rather than
# hardcoding the order or the label text separately.
PIPELINE_KEYFRAMES: list[tuple[str, str]] = [
    (HOTKEY_RECEIVED, "Hotkey Received"),
    (REPLAY_BUFFER_SENT, "OBS Replay Buffer Sent"),
    (REPLAY_BUFFER_COMPLETED, "OBS Replay Buffer Completed"),
    (TRIM_FINISHED, "Trim Finished"),
    (CLEANED_UP_MOVED, "Cleaned Up & Moved Files"),
]
