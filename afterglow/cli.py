"""
Command-line surface over the backend, so everything can be driven and
tested by hand before the GTK4 front-end exists.

Examples:
  python cli.py settings show
  python cli.py settings set obs.host 192.168.1.50
  python cli.py settings set obs.password hunter2
  python cli.py settings set clips_dir /home/max/Videos/Clips

  python cli.py clipconfig add "Ace" --length 30 --hotkey "ctrl+shift+f9" --sound /path/to/ding.wav
  python cli.py clipconfig list
  python cli.py clipconfig update 1 --length 45
  python cli.py clipconfig delete 1

  python cli.py trigger 1            # fire clip config id 1 (needs OBS running)

  python cli.py library list
  python cli.py library list --tag clutch --tag 1v3
  python cli.py library rename 3 --title "Insane 1v3" --description "vs the sweats"
  python cli.py library tag 3 clutch
  python cli.py library untag 3 clutch
  python cli.py library delete 3

  python cli.py editor trim 3 --start 4.2 --end 19.8 --frame-perfect
  python cli.py editor undo 3

  python cli.py debug-listen  # prints every raw key event live; use this
                               # when hotkeys aren't firing to check whether
                               # keypresses reach the app at all
"""
from __future__ import annotations

import argparse
import sys

from . import config as config_module
from . import db
from . import clips
from . import library
from .clips import ClipError
from .library import LibraryError
from .editor import EditorError
from .obs_client import OBSError
from . import hotkeys


def cmd_settings_show(args):
    s = config_module.load()
    print(s)


def _coerce(current_value, raw: str):
    """Cast the incoming CLI string to match the existing field's type,
    so e.g. obs.port stays an int across save/load instead of silently
    turning into a string the first time someone edits it."""
    if isinstance(current_value, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current_value, int):
        return int(raw)
    if isinstance(current_value, float):
        return float(raw)
    return raw


def cmd_settings_set(args):
    s = config_module.load()
    path = args.key.split(".")
    if len(path) == 1:
        if not hasattr(s, path[0]):
            print(f"Unknown setting: {args.key}", file=sys.stderr)
            sys.exit(1)
        setattr(s, path[0], _coerce(getattr(s, path[0]), args.value))
    elif len(path) == 2:
        section = getattr(s, path[0], None)
        if section is None or not hasattr(section, path[1]):
            print(f"Unknown setting: {args.key}", file=sys.stderr)
            sys.exit(1)
        setattr(section, path[1], _coerce(getattr(section, path[1]), args.value))
    else:
        print(f"Unknown setting: {args.key}", file=sys.stderr)
        sys.exit(1)
    config_module.save(s)
    print(f"Set {args.key} = {args.value}")


def cmd_clipconfig_add(args):
    cfg = clips.create_clip_config(
        name=args.name, length_seconds=args.length,
        sound_path=args.sound, hotkey=args.hotkey,
    )
    print(cfg)


def cmd_clipconfig_list(args):
    for cfg in clips.list_clip_configs():
        print(cfg)


def _resolve_clipconfig_id(args) -> int:
    if args.id is not None and args.by_name is not None:
        print("Error: pass either an id or --by-name, not both.", file=sys.stderr)
        sys.exit(1)
    if args.id is None and args.by_name is None:
        print("Error: need an id or --by-name.", file=sys.stderr)
        sys.exit(1)
    if args.by_name:
        return clips.get_clip_config_by_name(args.by_name).id
    return args.id


def cmd_clipconfig_update(args):
    clip_config_id = _resolve_clipconfig_id(args)
    fields = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.length is not None:
        fields["length_seconds"] = args.length
    if args.sound is not None:
        fields["sound_path"] = args.sound
    if args.hotkey is not None:
        fields["hotkey"] = args.hotkey
    print(clips.update_clip_config(clip_config_id, **fields))


def cmd_clipconfig_delete(args):
    clip_config_id = _resolve_clipconfig_id(args)
    clips.delete_clip_config(clip_config_id)
    print(f"Deleted clip config {clip_config_id}")


def cmd_clipconfig_get(args):
    if args.name:
        print(clips.get_clip_config_by_name(args.name))
    else:
        print(clips.get_clip_config(args.id))


def cmd_trigger(args):
    if args.name and args.clip_config_id is not None:
        print("Error: pass either a clip_config_id or --name, not both.", file=sys.stderr)
        sys.exit(1)
    if not args.name and args.clip_config_id is None:
        print("Error: need a clip_config_id or --name.", file=sys.stderr)
        sys.exit(1)

    if args.name:
        clip_config_id = clips.get_clip_config_by_name(args.name).id
    else:
        clip_config_id = args.clip_config_id
    video = clips.trigger_clip(clip_config_id)
    print(f"Captured: {video.title} -> {video.path}")


def cmd_library_list(args):
    videos = library.list_videos(
        tag_filter=args.tag or None,
        uploaded_only=args.uploaded,
        local_only=args.local,
    )
    if not videos:
        print("(no videos match)")
    for v in videos:
        tag_str = f" [{', '.join(v.tags)}]" if v.tags else ""
        edit_str = " (edited)" if v.has_edit else ""
        print(f"#{v.id}  {v.title}{edit_str}  {v.duration_sec:.1f}s{tag_str}")


def cmd_library_rename(args):
    v = library.rename_video(args.id, title=args.title, description=args.description)
    print(v)


def cmd_library_tag(args):
    library.add_tag_to_video(args.id, args.tag)
    print(f"Tagged #{args.id} with '{args.tag}'")


def cmd_library_untag(args):
    library.remove_tag_from_video(args.id, args.tag)
    print(f"Removed tag '{args.tag}' from #{args.id}")


def cmd_library_delete(args):
    library.delete_video(args.id, delete_file=not args.keep_file)
    print(f"Deleted video #{args.id}")


def cmd_library_tags(args):
    for t in library.all_known_tags():
        print(t)


def cmd_editor_trim(args):
    v = library.apply_trim(args.id, args.start, args.end, frame_perfect=args.frame_perfect)
    print(f"Trimmed #{v.id}: now {v.duration_sec:.3f}s (has_edit={v.has_edit})")


def cmd_editor_undo(args):
    v = library.undo_edit(args.id)
    print(f"Undone #{v.id}: now {v.duration_sec:.3f}s (has_edit={v.has_edit})")


def cmd_debug_listen(args):
    hotkeys.debug_listen()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_settings = sub.add_parser("settings")
    settings_sub = p_settings.add_subparsers(dest="settings_command", required=True)
    settings_sub.add_parser("show").set_defaults(func=cmd_settings_show)
    p_set = settings_sub.add_parser("set")
    p_set.add_argument("key", help="e.g. clips_dir, obs.host, obs.password, youtube.default_privacy")
    p_set.add_argument("value")
    p_set.set_defaults(func=cmd_settings_set)

    p_cc = sub.add_parser("clipconfig")
    cc_sub = p_cc.add_subparsers(dest="clipconfig_command", required=True)
    p_cc_add = cc_sub.add_parser("add")
    p_cc_add.add_argument("name")
    p_cc_add.add_argument("--length", type=int, required=True)
    p_cc_add.add_argument("--sound", default=None)
    p_cc_add.add_argument("--hotkey", default=None)
    p_cc_add.set_defaults(func=cmd_clipconfig_add)
    cc_sub.add_parser("list").set_defaults(func=cmd_clipconfig_list)

    p_cc_get = cc_sub.add_parser("get")
    p_cc_get.add_argument("id", type=int, nargs="?", default=None)
    p_cc_get.add_argument("--name", default=None)
    p_cc_get.set_defaults(func=cmd_clipconfig_get)

    p_cc_upd = cc_sub.add_parser("update")
    p_cc_upd.add_argument("id", type=int, nargs="?", default=None,
                           help="Clip config id (or use --by-name)")
    p_cc_upd.add_argument("--by-name", default=None, dest="by_name",
                           help="Look up the clip config to edit by its current name")
    p_cc_upd.add_argument("--name", default=None, help="New name to set")
    p_cc_upd.add_argument("--length", type=int, default=None)
    p_cc_upd.add_argument("--sound", default=None)
    p_cc_upd.add_argument("--hotkey", default=None)
    p_cc_upd.set_defaults(func=cmd_clipconfig_update)

    p_cc_del = cc_sub.add_parser("delete")
    p_cc_del.add_argument("id", type=int, nargs="?", default=None)
    p_cc_del.add_argument("--by-name", default=None, dest="by_name")
    p_cc_del.set_defaults(func=cmd_clipconfig_delete)

    p_trigger = sub.add_parser(
        "trigger",
        description="Fire a clip config, by id or by name. This is the command "
                    "a system-level hotkey binding should call, e.g.: "
                    "bind a shortcut to `cli.py trigger --name Ace`.",
    )
    p_trigger.add_argument("clip_config_id", type=int, nargs="?", default=None,
                            help="Clip config id (omit if using --name)")
    p_trigger.add_argument("--name", default=None,
                            help="Clip config name (case-insensitive), e.g. 'Ace'")
    p_trigger.set_defaults(func=cmd_trigger)

    p_lib = sub.add_parser("library")
    lib_sub = p_lib.add_subparsers(dest="library_command", required=True)
    p_lib_list = lib_sub.add_parser("list")
    p_lib_list.add_argument("--tag", action="append", default=[])
    p_lib_list.add_argument("--uploaded", action="store_true")
    p_lib_list.add_argument("--local", action="store_true")
    p_lib_list.set_defaults(func=cmd_library_list)
    p_lib_ren = lib_sub.add_parser("rename")
    p_lib_ren.add_argument("id", type=int)
    p_lib_ren.add_argument("--title", default=None)
    p_lib_ren.add_argument("--description", default=None)
    p_lib_ren.set_defaults(func=cmd_library_rename)
    p_lib_tag = lib_sub.add_parser("tag")
    p_lib_tag.add_argument("id", type=int)
    p_lib_tag.add_argument("tag")
    p_lib_tag.set_defaults(func=cmd_library_tag)
    p_lib_untag = lib_sub.add_parser("untag")
    p_lib_untag.add_argument("id", type=int)
    p_lib_untag.add_argument("tag")
    p_lib_untag.set_defaults(func=cmd_library_untag)
    p_lib_del = lib_sub.add_parser("delete")
    p_lib_del.add_argument("id", type=int)
    p_lib_del.add_argument("--keep-file", action="store_true")
    p_lib_del.set_defaults(func=cmd_library_delete)
    lib_sub.add_parser("tags").set_defaults(func=cmd_library_tags)

    p_ed = sub.add_parser("editor")
    ed_sub = p_ed.add_subparsers(dest="editor_command", required=True)
    p_ed_trim = ed_sub.add_parser("trim")
    p_ed_trim.add_argument("id", type=int)
    p_ed_trim.add_argument("--start", type=float, required=True)
    p_ed_trim.add_argument("--end", type=float, required=True)
    p_ed_trim.add_argument("--frame-perfect", action="store_true")
    p_ed_trim.set_defaults(func=cmd_editor_trim)
    p_ed_undo = ed_sub.add_parser("undo")
    p_ed_undo.add_argument("id", type=int)
    p_ed_undo.set_defaults(func=cmd_editor_undo)

    p_debug = sub.add_parser(
        "debug-listen",
        description="Print every raw keyboard event live, bypassing combo "
                    "matching and the daemon entirely. Use this to check "
                    "whether keypresses reach the app at all, and what "
                    "they look like, when hotkeys aren't firing.",
    )
    p_debug.set_defaults(func=cmd_debug_listen)

    return parser


def main():
    db.init_db()
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (ClipError, LibraryError, EditorError, OBSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
