# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), a trim
editor (embedded mpv), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple
machines. The Editor page (video playback + trim UI) has been the
focus of many recent sessions.

All files compile and import cleanly as of this handoff. Nothing is
left half-edited.

## Currently being worked on
A batch of Library/Editor/Settings features, given together:

Done this session:
- Favoriting: a star toggle on Library cards and in the Editor, a
  pinned "Favorite" filter shown above all other filters in the
  Library's Filters dropdown, and a `favorite` column added to the
  video table (migrated in for existing databases).
- Block/exclude filters: right-clicking a filter checkbox in the
  Library's Filters dropdown marks it "blocked" -- any clip carrying
  that tag is hidden. Qt has no native tri-state checkbox visual for
  this (its own tristate mode means something else -- a hierarchical
  "some children checked" state, not this), so the blocked state is
  shown as a crossed-out box glyph + red label text rather than a true
  X-shaped checkbox indicator.
- "+ Add Filter" embedded directly in both the Library's and Editor's
  Filters dropdowns, just above "Highlight Unedited" in the Library's
  case.
- The Library's "Add Filter" dialog is now a dropdown of existing tags
  plus a "+" button for a new one, replacing the earlier free-text
  field with autocomplete.
- Editor: a clip loaded in the Editor now stays loaded (not unloaded)
  when switching to another page, but pauses via `hideEvent`, rather
  than continuing to play in the background.
- Fullscreen-on-launch setting, plus a default (non-fullscreen) window
  size fix (1600x900, a 16:9-ish size matching the existing 1920x1080
  scaling baseline, replacing a narrower 1000x700 default).
- New Settings > Filters tab: per-tag icon assignment (auto-shown
  above/below/beside clip thumbnails per a location dropdown), "Show
  Filter Names under Clips" / "Show Filter Icons" toggles, and tag
  rename (moved here from the Filters dropdown's right-click, since
  that gesture is now used for blocking instead).
- New Settings > Auto Add Filter tab: a rule list (filter name, an app
  match string, and a mode of "while app is open" vs "only while app
  is focused"). Multiple rules can be active simultaneously and are
  independent of each other.
- Auto Add Filter detection (`autofilter.py`) and its hookup into the
  clip-capture pipeline (`clips.trigger_clip`): "open" mode scans
  `/proc` for a matching process name or command line (compositor
  agnostic). "Focused" mode targets KDE Plasma on Wayland via
  `kdotool` (drives KWin's own scripting/DBus interface; packaged in
  nixpkgs as `kdotool`, added to the flake's runtime PATH and dev
  shell), with Hyprland (`hyprctl activewindow -j`) and Sway (`swaymsg
  -t get_tree`) kept as fallback backends for a different compositor.
  If none of the three is present, "focused" rules log one warning and
  never match. A rule's match text is checked against both fields
  relevant to its mode (process name AND full command line for "open";
  window title AND window class for "focused") -- a hit on either
  counts.
- A `--filter` CLI flag on `trigger`, repeatable
  (`--filter clutch --filter 1v3`), applying additional tags to a
  manually-triggered clip on top of whatever Auto Add Filter rules
  already matched.
- Planned YouTube "unlisted library" behavior written into README.md:
  thumbnails/titles/filters cached locally so browsing the uploaded
  library doesn't need an API call per video; double-click calls and
  embeds playback (or falls back to the system YouTube viewer),
  without relying on the cache for playback itself.

## What's not working / not finished
- **Auto Add Filter "focused" detection via kdotool is unverified on
  real hardware** -- built and reasoned through against kdotool's
  documented CLI, but there was no live KDE Plasma/KWin session
  available to test its actual output against. `kdotool` needs to be
  present on PATH (now added to the flake's package + dev shell) for
  "focused" rules to work at all.
- **Per-card config/DB reads are not cached.** `VideoCard.__init__`
  calls `config_module.load()` and `library.tag_icons()` once per
  card, on every grid rebuild. Fine at the scale this library
  currently reaches, but worth hoisting up to a single call per
  refresh if the library grows very large.
- **README.md as a whole has not been brought in line with the
  now-documented anonymous/impersonal handoff-and-README style** --
  only the new "Planned: YouTube unlisted library behavior" section
  follows it. The rest of README.md is a long personal, first/second-
  person session changelog; rewriting the whole document is a
  separate, sizeable task on its own.

## Architecture pointers
- `autofilter.py` -- Auto Add Filter detection (process scanning +
  Wayland compositor queries).
- `afterglow/clips.py` -- `trigger_clip()` snapshots
  `autofilter.compute_active_auto_tags()` at the start of the
  pipeline (not after the OBS save/trim wait) and applies matching
  tags to the resulting video.
- `afterglow/config.py` -- `AppSettings.default_to_fullscreen`,
  `FilterDisplaySettings`, `AutoFilterRule` (list on
  `AppSettings.auto_filters`).
- `afterglow/db.py` -- schema plus `_migrate_columns()`, which
  ALTER-TABLEs new columns (`videos.favorite`, `tags.icon_path`) into
  a pre-existing database file; `CREATE TABLE IF NOT EXISTS` alone
  only helps a brand new database.
- `afterglow/library.py` -- `set_favorite`, `all_tags_with_ids`,
  `rename_tag`, `set_tag_icon`, `tag_icons`; `list_videos` gained
  `tag_exclude` and `favorite_only` parameters.
- `afterglow/gui/library_page.py` -- `FilterCheckBox` is the
  include/exclude tri-state checkbox used in the Filters dropdown.
- `afterglow/gui/video_card.py` -- `_build_icon_row` renders the
  per-tag icon overlay in whichever of the four configured positions;
  `_title_text` adds the favorite star.
- `afterglow/gui/filters_settings_page.py` -- new file; the Settings >
  Filters and Auto Add Filter tabs.
- `afterglow/gui/settings_page.py` -- now a `QTabWidget` (General +
  Filters) instead of one long scrolling page.

## Open questions / pending decisions
- Whether the README rewrite (anonymizing the whole changelog, not
  just new sections) should happen as its own dedicated pass.

## Workflow reminder
Edits are committed via a `full-git-update` command, then a
`flake-update` command to bump the consuming system flake's lock
file, then a rebuild. The project zip is expected alongside every
message where changes are made, not just at explicit handoff time.
