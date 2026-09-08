# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), a trim
editor (embedded mpv), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple
machines. The Editor page (video playback + trim UI) has been the
focus of many earlier sessions; recent sessions have focused on
Library filtering/display features instead.

All files compile and import cleanly as of this handoff, and the
GUI/DB/config layers have been exercised under an offscreen Qt
platform (categories, icons, favoriting, click-to-filter, info
display toggles, settings save round-trips) since there's no real
display in this sandbox to run the app against directly. Nothing is
left half-edited.

## Currently being worked on
Two consecutive batches of Library/Settings features, given together
each time.

### This session
- Filter categories: tags can be grouped into named categories, shown
  as side-opening submenus ("like folders") in the Library's Filters
  dropdown. New `filter_categories` table; `library.py` gained
  `create_category`, `rename_category`, `delete_category`,
  `set_tag_category`, `all_categories`, `tags_grouped_by_category`.
  Category assignment lives in Settings > Filters (a dropdown per tag,
  with a "+ New Category..." entry) rather than in the dropdown itself.
- Filter icons are now 3x their previous size (54px base, was 18px),
  centered within their row/column, and scale back down together
  (`_icon_size_for_count` in `video_card.py`) if more icons on one clip
  wouldn't otherwise fit within the thumbnail's width/height.
- The "Below" icon location now renders after the title (and after the
  optional length/size/date info lines -- see below), where tag-name
  text used to sit, rather than between the thumbnail and the title.
- Hovering a filter icon on a card shows the filter's name (tooltip);
  left-clicking toggles it as an include-filter and right-clicking
  toggles it as a blocked filter, mirroring the Filters dropdown's own
  checkbox gestures without needing to open the dropdown.
- Sidebar: the inactive nav tab's gradient is now rendered 35% darker
  (a multiply-blend, not a flat alpha overlay, so it darkens
  proportionally rather than washing out unevenly) than the active
  tab's. Separately, the active tab's native "checked" style no longer
  visually grows the button -- both buttons are now fully flat/
  transparent via stylesheet, so nothing but this app's own paintEvent
  draws anything beyond the icon itself.
- New "Info" dropdown next to Sort By, with four independent toggles,
  persisted to config (`AppSettings.card_info`): Show Filters, Show
  Video Length, Show File Size (rendered after length, same line), and
  Show Creation Date (its own line, under length/size, above filters).
- Auto Add Filter's app-match field is now an editable dropdown
  (`_AutoFilterRow.app_combo` in `filters_settings_page.py`) listing
  currently-running process names with a refresh button, rather than a
  plain text box -- still freely typable for an app that isn't running
  yet.
- Auto Add Filter's "focused" backend now targets KDE Plasma via
  `kdotool` first (see previous session's entry below), confirmed as
  the actual compositor in use.

### Previous session
- Favoriting (star on cards/Editor, pinned "Favorite" filter, DB
  migration), block/exclude filters (right-click a Filters checkbox),
  "+ Add Filter" embedded in both dropdowns, the Library's Add Filter
  dialog redesigned as dropdown+"+", Editor clips pausing (not
  unloading) on tab switch, a fullscreen-on-launch setting + default
  window size fix, a new Settings > Filters tab (per-tag icons, tag
  rename, display toggles), a new Settings > Auto Add Filter tab, Auto
  Add Filter detection (`autofilter.py`) hooked into clip capture, a
  `--filter` CLI flag on `trigger`, and the planned YouTube
  "unlisted library" behavior written into README.md.

## What's not working / not finished
- **Auto Add Filter "focused" detection via kdotool is unverified on
  real hardware** -- built and reasoned through against kdotool's
  documented CLI (`kdotool getactivewindow getwindowname` /
  `getwindowclassname`), but there's been no live KDE Plasma/KWin
  session available in this sandbox to test its actual output
  against. `kdotool` needs to be present on PATH (added to the
  flake's package + dev shell already) for "focused" rules to work at
  all. This is the single biggest remaining unknown across both
  sessions' work.
- **Per-card config/DB reads are not cached.** `VideoCard.__init__`
  calls `config_module.load()`, `library.tag_icons()` once per card,
  on every grid rebuild. Fine at the scale this library currently
  reaches, but worth hoisting to a single call per refresh if the
  library grows very large.
- **README.md as a whole has not been brought in line with the
  documented anonymous/impersonal handoff-and-README style** -- only
  the "Planned: YouTube unlisted library behavior" section follows it.
  The rest is a long personal, first/second-person session changelog;
  rewriting the whole document is a separate, sizeable task.
- Category management (rename/delete a whole category, not just a
  tag's membership in one) has no dedicated UI yet -- categories are
  currently only created inline from a tag's "+ New Category..."
  dropdown entry in Settings > Filters.

## Architecture pointers
- `autofilter.py` -- Auto Add Filter detection (process scanning +
  Wayland compositor queries via kdotool/hyprctl/swaymsg); also
  `list_running_process_display_names()` for the Settings dropdown.
- `afterglow/clips.py` -- `trigger_clip()` snapshots
  `autofilter.compute_active_auto_tags()` at the start of the
  pipeline (not after the OBS save/trim wait) and applies matching
  tags to the resulting video.
- `afterglow/config.py` -- `AppSettings.default_to_fullscreen`,
  `FilterDisplaySettings`, `AutoFilterRule` (list on
  `AppSettings.auto_filters`), `CardInfoSettings` (on
  `AppSettings.card_info`).
- `afterglow/db.py` -- schema plus `_migrate_columns()`, which
  ALTER-TABLEs new columns (`videos.favorite`, `tags.icon_path`,
  `tags.category_id`) into a pre-existing database file, and a new
  `filter_categories` table.
- `afterglow/library.py` -- `set_favorite`, `all_tags_with_ids`,
  `rename_tag`, `set_tag_icon`, `tag_icons`, `tag_category_ids`,
  category CRUD (`create_category`/`rename_category`/
  `delete_category`/`set_tag_category`/`all_categories`/
  `tags_grouped_by_category`); `list_videos` has `tag_exclude` and
  `favorite_only` parameters.
- `afterglow/gui/library_page.py` -- `FilterCheckBox` is the
  include/exclude tri-state checkbox used in the Filters dropdown;
  `_rebuild_filters_menu` builds one `QMenu.addMenu()` submenu per
  category; `_build_info_menu` is the new Info dropdown.
- `afterglow/gui/video_card.py` -- `_build_icon_row` renders the
  per-tag icon overlay (now via `_FilterIconLabel`, which carries the
  hover tooltip + click signals) in whichever of the four configured
  positions; `_icon_size_for_count` handles the shrink-to-fit sizing;
  `_title_text` adds the favorite star; `_format_duration` /
  `_format_file_size` / `_format_date` back the Info toggles.
- `afterglow/gui/filters_settings_page.py` -- Settings > Filters (now
  including per-tag category assignment) and Auto Add Filter tabs.
- `afterglow/gui/settings_page.py` -- a `QTabWidget` (General +
  Filters) instead of one long scrolling page.
- `afterglow/gui/main_window.py` -- `_ScalingIconButton` draws the
  sidebar nav gradient border and the inactive-tab darkening.

## Open questions / pending decisions
- Whether the README rewrite (anonymizing the whole changelog, not
  just new sections) should happen as its own dedicated pass.
- Whether categories need their own rename/delete UI, or whether
  renaming a category by re-entering it as a "new" one from a tag row
  (picking up the existing one via the unique-name check) is enough.

## Workflow reminder
Edits are committed via a `full-git-update` command, then a
`flake-update` command to bump the consuming system flake's lock
file, then a rebuild. The project zip is expected alongside every
message where changes are made, not just at explicit handoff time.
