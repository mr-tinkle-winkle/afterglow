"""
Themes: whole-app looks picked in Settings > General > Theme. Each one has
its own colours, fonts, icons (text glyphs drawn in place of the bundled
PNGs), words, card frame and animations (gui/theme_fx.py).

"classic" is afterglow's own look and changes nothing -- every other theme
is layered on top of the user's settings, never written into them:

  * colours / corner radius / card text -> config.load_readonly() returns the
    appearance with the theme's values applied (apply_to_appearance), so the
    ~everything that already reads Theme()/appearance picks them up as-is.
    Settings itself reads config.load(), which stays the user's own values.
  * icons and the gradient images -> resources.resource_qpixmap() asks
    glyph_pixmap()/gradient_pixmap() first.
  * app font + palette -> apply_to_app() at startup.
  * words -> word(key, default) at the few places the UI shows them.

The theme is read once per process (active()); changing it in Settings
restarts the window.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

# --------------------------------------------------------------------------
# preset definition
# --------------------------------------------------------------------------
SYM = "DejaVu Sans"                  # symbol glyphs (bundled, theme_fonts/)
MONO = "DejaVu Sans Mono"
INK = "Afterglow Ink Glyphs"


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    blurb: str
    # colours -------------------------------------------------------------
    app_bg: str                      # window / sidebar
    library_bg: str                  # the grid's page
    card_bg: str                     # outer card
    accent: str                      # info box, buttons
    tab: str                         # sidebar + Local/Uploaded buttons
    text: str                        # card title / general text
    text_outline: str
    text_outline_width: float
    muted: str                       # dates, secondary text
    icon: str                        # glyph colour (buttons)
    highlight: str                   # selection, focus, progress
    border: str                      # card frame colour ("" = none)
    border_width: float = 0.0
    fav_border: str = ""             # favourite card frame ("" = same as border)
    shadow: str = ""                 # hard drop shadow colour ("" = none)
    shadow_offset: int = 0
    glow: str = ""                   # soft outer glow on the frame
    unedited: tuple = ("#ff2fa6", "#7af3ff")      # unedited-clip thumbnail border gradient
    selected: tuple = ("#ffffff", "#bbbbbb")      # selected-card ring gradient
    page_border: tuple = ("#222222", "#111111")   # the gradient framing Library / Editor
    radius: int = 24
    btn_fill: str = ""               # buttons ("" = accent)
    btn_line: str = ""               # button outline ("" = none)
    btn_line_w: float = 0.0
    tab_fg: str = ""                 # glyph + word colour on the sidebar / Local-Uploaded buttons ("" = icon)
    icon_outline: str = ""           # stroke around glyph icons
    icon_glow: str = ""
    # type ----------------------------------------------------------------
    font_body: str = "Montserrat AG"
    font_display: str = "Montserrat AG"
    display_upper: bool = False
    display_italic: bool = False
    display_weight: int = 700
    letter_spacing: float = 0.0      # percent, display font
    # glyphs: resource name -> (text, font) ; font None = the display font
    glyphs: dict = field(default_factory=dict)
    words: dict = field(default_factory=dict)
    # effects (theme_fx.py) -----------------------------------------------
    backdrop: str = "none"
    enter: str = "fade"
    hover: str = "glow"
    decor: str = ""                  # extra card decoration ("tape", "dwg", "seal", "corners", ...)
    title_style: str = "plain"       # Library header title treatment
    dark: "dict | None" = None       # overrides for the Dark mode variant (when offered)


def _g(**kw):
    return dict(kw)


# icon resource names (gui/resources/*.png) a theme can replace
ICON_NAMES = (
    "library.png", "editor.png", "settings.png", "local_videos.png", "uploaded_videos.png",
    "search_icon.png", "search_icon_active.png", "refresh_icon.png", "sort_icon.png", "x_icon.png",
    "edit_icon.png", "copy_icon.png", "filters_icon.png", "delete_icon.png",
    "favorite_star_icon.png", "favorite_star_off_icon.png",
)

TAB_ICONS = ("library.png", "editor.png", "settings.png", "local_videos.png", "uploaded_videos.png")

PRESETS: "dict[str, Preset]" = {}


def _add(p: Preset) -> None:
    PRESETS[p.id] = p


_add(Preset(
    "synthwave", "Synthwave", "Neon pink and cyan over a sunset grid. Clips are tapes.",
    btn_fill="#1c0336", btn_line="#ff2fa6", btn_line_w=2.0,
    app_bg="#12002a", library_bg="#1c0336", card_bg="#260645", accent="#3a0a5c", tab="#ff2fa6",
    text="#ffffff", text_outline="#ff2fa6", text_outline_width=2.0, muted="#7af3ff", icon="#fff2fb",
    highlight="#7af3ff", border="#ff2fa6", border_width=2.5, fav_border="#ffcf3f", glow="#ff2fa6",
    unedited=("#ffcf3f", "#ff2fa6", "#7af3ff"), selected=("#7af3ff", "#ffffff", "#7af3ff"),
    page_border=("#ff2fa6", "#7a1fff", "#12002a"), radius=6, icon_glow="#ff2fa6",
    font_body="Montserrat AG", font_display="Orbitron AG", display_upper=True, display_weight=800, letter_spacing=6,
    glyphs=_g(**{"library.png": ("▶", SYM), "editor.png": ("✂", SYM), "settings.png": ("≋", SYM),
                 "local_videos.png": ("◉", SYM), "uploaded_videos.png": ("◬", SYM),
                 "search_icon.png": ("⌕", SYM), "search_icon_active.png": ("⌕", SYM), "refresh_icon.png": ("⟲", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✕", SYM), "edit_icon.png": ("✂", SYM),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("≋", SYM), "delete_icon.png": ("⏏", SYM),
                 "favorite_star_icon.png": ("★", SYM), "favorite_star_off_icon.png": ("☆", SYM)}),
    words=dict(nav_library="Tapes", nav_editor="Splice", nav_settings="Tune", tab_local="On deck",
               tab_uploaded="Broadcast", header_title="The Tapes", header_sub="── side a ──",
               search="Search the tapes…", empty_local="No tapes yet. Hit record.",
               empty_uploaded="Nothing on air yet.", badge_uploaded="ON AIR", fav="★"),
    backdrop="synthwave", enter="neon", hover="neon", title_style="chrome",
))

_add(Preset(
    "terminal", "Phosphor Terminal", "Green phosphor on black, scanlines and a blinking cursor.",
    btn_fill="#020a03", btn_line="#1fa046", btn_line_w=1.0,
    app_bg="#020a03", library_bg="#031207", card_bg="#041a0a", accent="#062a10", tab="#0d4a1d",
    text="#39ff6a", text_outline="#020a03", text_outline_width=0.0, muted="#1fa046", icon="#39ff6a",
    highlight="#39ff6a", border="#1fa046", border_width=1.0, fav_border="#39ff6a", glow="#39ff6a",
    unedited=("#39ff6a", "#0b5a1f"), selected=("#39ff6a", "#39ff6a"), page_border=("#0d4a1d", "#020a03"),
    radius=0, icon_glow="#39ff6a",
    font_body=MONO, font_display=MONO, display_weight=700,
    glyphs=_g(**{"library.png": ("~/", None), "editor.png": ("./e", None), "settings.png": ("/etc", None),
                 "local_videos.png": ("[l]", None), "uploaded_videos.png": ("[r]", None),
                 "search_icon.png": ("/", None), "search_icon_active.png": ("/", None), "refresh_icon.png": ("↻", None),
                 "sort_icon.png": ("ls", None), "x_icon.png": ("^C", None), "edit_icon.png": ("e", None),
                 "copy_icon.png": ("y", None), "filters_icon.png": ("f", None), "delete_icon.png": ("rm", None),
                 "favorite_star_icon.png": ("*", None), "favorite_star_off_icon.png": ("·", None)}),
    words=dict(nav_library="~/clips", nav_editor="./edit", nav_settings="/etc/conf", tab_local="local",
               tab_uploaded="remote", header_title="clips/", header_sub="afterglow@nixos:~$ ls -lt ./clips",
               search="grep -i '…'", empty_local="ls: ./clips: no clips yet", empty_uploaded="0 pushed",
               badge_uploaded="PUSHED", fav="[*]"),
    backdrop="phosphor", enter="scan", hover="cursor", title_style="cursor",
))

_add(Preset(
    "comic", "Comic Book", "Halftone pages, thick ink, starbursts. Every clip is an issue.",
    btn_fill="#1f5fd6", btn_line="#111111", btn_line_w=3.0,
    app_bg="#fff3b8", library_bg="#ffe14d", card_bg="#ffffff", accent="#ffffff", tab="#1f5fd6",
    text="#111111", text_outline="#ffe14d", text_outline_width=0.0, muted="#444444", icon="#ffffff",
    highlight="#e3262f", border="#111111", border_width=4.0, fav_border="#111111",
    shadow="#111111", shadow_offset=7,
    unedited=("#e3262f", "#ffe14d", "#e3262f"), selected=("#1f5fd6", "#1f5fd6"), page_border=("#111111", "#111111"),
    radius=2, icon_outline="#111111",
    font_body="Comic Neue", font_display="Bangers", display_upper=True, display_weight=400, letter_spacing=3,
    glyphs=_g(**{"library.png": ("▤", SYM), "editor.png": ("✎", SYM), "settings.png": ("⚙", SYM),
                 "local_videos.png": ("▥", SYM), "uploaded_videos.png": ("✸", SYM),
                 "search_icon.png": ("?!", None), "search_icon_active.png": ("?!", None), "refresh_icon.png": ("⟳", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✖", SYM), "edit_icon.png": ("✎", SYM),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("✸", SYM), "delete_icon.png": ("✖", SYM),
                 "favorite_star_icon.png": ("★", SYM), "favorite_star_off_icon.png": ("☆", SYM)}),
    words=dict(nav_library="Issues", nav_editor="Inking", nav_settings="Panel setup", tab_local="Longbox",
               tab_uploaded="Published", header_title="Issue #{count}!", header_sub="Meanwhile, in your library…",
               search="Who goes there?!", empty_local="Our story begins… with no clips yet!",
               empty_uploaded="Nothing published yet!", badge_uploaded="OUT NOW!", fav="★"),
    backdrop="halftone", enter="slam", hover="pow", decor="caption", title_style="comic",
))

_add(Preset(
    "blueprint", "Blueprint", "Drafting paper, dimension lines and title blocks.",
    btn_fill="#123d7a", btn_line="#e6f0ff", btn_line_w=1.5,
    app_bg="#0f3570", library_bg="#123d7a", card_bg="#123d7a", accent="#16478c", tab="#1b55a6",
    text="#e6f0ff", text_outline="#123d7a", text_outline_width=0.0, muted="#9fbbe6", icon="#e6f0ff",
    highlight="#ffd84a", border="#e6f0ff", border_width=2.0, fav_border="#ffd84a",
    unedited=("#ffd84a", "#ffd84a"), selected=("#ffffff", "#9fbbe6"), page_border=("#e6f0ff", "#9fbbe6"),
    radius=0,
    font_body="Courier Prime", font_display="Courier Prime", display_upper=True, display_weight=700, letter_spacing=8,
    glyphs=_g(**{"library.png": ("▦", SYM), "editor.png": ("✎", SYM), "settings.png": ("⌗", SYM),
                 "local_videos.png": ("▤", SYM), "uploaded_videos.png": ("⇪", SYM),
                 "search_icon.png": ("⌕", SYM), "search_icon_active.png": ("⌕", SYM), "refresh_icon.png": ("↻", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✕", SYM), "edit_icon.png": ("✎", SYM),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("⌗", SYM), "delete_icon.png": ("✕", SYM),
                 "favorite_star_icon.png": ("✓", SYM), "favorite_star_off_icon.png": ("○", SYM)}),
    words=dict(nav_library="Drawings", nav_editor="Drafting", nav_settings="Specs", tab_local="Archive",
               tab_uploaded="Issued", header_title="Drawing Index", header_sub="scale 1:1 · all dimensions in seconds",
               search="lookup dwg…", empty_local="No drawings on file.", empty_uploaded="Nothing issued yet.",
               badge_uploaded="ISSUED", fav="✓"),
    backdrop="blueprint", enter="draw", hover="brackets", decor="dwg", title_style="underline",
))

_add(Preset(
    "scrapbook", "Scrapbook", "Polaroids, washi tape and handwriting on warm paper.",
    btn_fill="#efe6d2", btn_line="#b39b6e", btn_line_w=1.5,
    app_bg="#d9c9a8", library_bg="#efe6d2", card_bg="#fffdf7", accent="#fffdf7", tab="#f3d27a",
    text="#3b2e22", text_outline="#fffdf7", text_outline_width=0.0, muted="#8a7457", icon="#6b5843",
    highlight="#e2675a", border="", border_width=0.0, shadow="#00000033", shadow_offset=5,
    unedited=("#e2675a", "#f3d27a"), selected=("#e2675a", "#e2675a"), page_border=("#c9b48c", "#b39b6e"),
    radius=3,
    font_body="Patrick Hand", font_display="Caveat", display_weight=400,
    glyphs=_g(**{"library.png": ("✿", SYM), "editor.png": ("✂", SYM), "settings.png": ("⌸", SYM),
                 "local_videos.png": ("✎", SYM), "uploaded_videos.png": ("✉", SYM),
                 "search_icon.png": ("⌕", SYM), "search_icon_active.png": ("⌕", SYM), "refresh_icon.png": ("↻", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✕", SYM), "edit_icon.png": ("✂", SYM),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("✿", SYM), "delete_icon.png": ("✕", SYM),
                 "favorite_star_icon.png": ("♥", SYM), "favorite_star_off_icon.png": ("♡", SYM)}),
    words=dict(nav_library="Scrapbook", nav_editor="Craft table", nav_settings="Drawer", tab_local="My pages",
               tab_uploaded="Shared", header_title="Scrapbook", header_sub="volume 3 · autumn",
               search="look for a memory…", empty_local="Nothing pinned yet — go make memories.",
               empty_uploaded="Nothing shared yet.", badge_uploaded="shared!", fav="♥"),
    backdrop="paper", enter="drop", hover="lift", decor="tape", title_style="script",
    dark=dict(app_bg="#141210", library_bg="#1b1917", card_bg="#2a2622", accent="#2a2622", tab="#5a4a20",
              text="#f6efe2", muted="#a89b88", icon="#e9d9bd", text_outline="#2a2622", shadow="#000000aa",
              page_border=("#3a332b", "#262019"), highlight="#f0a080", backdrop="paper_dark",
              btn_fill="#3a332b", btn_line="#5a5044",
              header_sub="volume 3 · after dark"),
))

_add(Preset(
    "ink", "Sumi Ink", "Rice paper, black ink and a red seal. Quiet and calm.",
    btn_fill="#fffaf0", btn_line="#1a171466", btn_line_w=1.0,
    app_bg="#e9e2d4", library_bg="#f1ebdf", card_bg="#fffaf0", accent="#fffaf0", tab="#1a1714",
    text="#1a1714", text_outline="#fffaf0", text_outline_width=0.0, muted="#8a8072", icon="#1a1714",
    highlight="#b8221c", border="", border_width=0.0, shadow="#0000001f", shadow_offset=3,
    unedited=("#b8221c", "#b8221c"), selected=("#1a1714", "#1a1714"), page_border=("#d8cfbd", "#c9bfab"),
    radius=2,
    tab_fg="#f1ebdf",
    font_body="Playfair Display AG", font_display="Playfair Display AG", display_weight=700, letter_spacing=4,
    glyphs=_g(**{"library.png": ("巻", INK), "editor.png": ("筆", INK), "settings.png": ("硯", INK),
                 "local_videos.png": ("蔵", INK), "uploaded_videos.png": ("送", INK),
                 "search_icon.png": ("⌕", SYM), "search_icon_active.png": ("⌕", SYM), "refresh_icon.png": ("↻", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✕", SYM), "edit_icon.png": ("筆", INK),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("墨", INK), "delete_icon.png": ("✕", SYM),
                 "favorite_star_icon.png": ("印", INK), "favorite_star_off_icon.png": ("一", INK)}),
    words=dict(nav_library="Scroll", nav_editor="Brush", nav_settings="Ink stone", tab_local="Kept",
               tab_uploaded="Shared", header_title="The Scroll", header_sub="a quiet record of moments",
               search="search the scroll", empty_local="The scroll is empty.", empty_uploaded="Nothing shared.",
               badge_uploaded="shared", fav=""),
    backdrop="washi", enter="bleed", hover="brush", decor="seal", title_style="brush",
))

_add(Preset(
    "aero", "Aero", "Glossy glass and floating bubbles under a bright sky.",
    btn_fill="#1c9be0", btn_line="#ffffff", btn_line_w=1.5,
    app_bg="#bfe6fb", library_bg="#d8f3ff", card_bg="#f4fbff", accent="#e8f6fd", tab="#1c9be0",
    text="#0d3b5c", text_outline="#ffffff", text_outline_width=0.0, muted="#4f7f9c", icon="#ffffff",
    highlight="#4bbf3a", border="#ffffff", border_width=1.5, fav_border="#ffd84a", shadow="#0d3b5c22", shadow_offset=0,
    glow="#7fd8ff",
    unedited=("#ffd84a", "#4bbf3a"), selected=("#1c9be0", "#7fd8ff"), page_border=("#8fd3ff", "#9ee6a8"),
    radius=20, icon_outline="#0b6aa5",
    font_body="Montserrat AG", font_display="Montserrat AG", display_weight=600,
    glyphs=_g(**{"library.png": ("❀", SYM), "editor.png": ("✦", SYM), "settings.png": ("☼", SYM),
                 "local_videos.png": ("⌂", SYM), "uploaded_videos.png": ("☁", SYM),
                 "search_icon.png": ("⌕", SYM), "search_icon_active.png": ("⌕", SYM), "refresh_icon.png": ("⟳", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✕", SYM), "edit_icon.png": ("✦", SYM),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("❀", SYM), "delete_icon.png": ("✕", SYM),
                 "favorite_star_icon.png": ("★", SYM), "favorite_star_off_icon.png": ("☆", SYM)}),
    words=dict(nav_library="My Moments", nav_editor="Studio", nav_settings="Preferences", tab_local="This PC",
               tab_uploaded="Shared", header_title="My Moments", header_sub="Everything you've saved, all in one place",
               search="Search your moments", empty_local="No moments yet.", empty_uploaded="Nothing shared yet.",
               badge_uploaded="☁ Shared", fav="★"),
    backdrop="aero", enter="float", hover="shine", title_style="aero",
    dark=dict(app_bg="#031522", library_bg="#06283a", card_bg="#0e3347", accent="#0b2a3c", tab="#1c7fc0",
              text="#f2fbff", muted="#86aec4", icon="#ffffff", border="#ffffff33", glow="#2b8cff",
              page_border=("#28d6a4", "#2b8cff", "#9a5bff"), backdrop="aero_dark",
              btn_fill="#134e74", btn_line="#ffffff66",
              header_sub="Everything you've saved, after hours"),
))

_add(Preset(
    "grimoire", "Grimoire", "Gold leaf on midnight vellum. Favourites are Legendary.",
    btn_fill="#1a1426", btn_line="#8a6d2e", btn_line_w=1.5,
    app_bg="#140f1e", library_bg="#1a1426", card_bg="#221a30", accent="#2a2040", tab="#3d2b5a",
    text="#f5e6be", text_outline="#140f1e", text_outline_width=2.0, muted="#a89470", icon="#e9c46a",
    highlight="#e9c46a", border="#5a4a72", border_width=2.0, fav_border="#ffb347", glow="#e9c46a",
    unedited=("#9d86c9", "#3d6b9e", "#9d86c9"), selected=("#e9c46a", "#fff3c4", "#b8860b"),
    page_border=("#8a6d2e", "#3d2b5a", "#8a6d2e"), radius=6,
    font_body="Playfair Display AG", font_display="Cinzel AG", display_weight=700, letter_spacing=2,
    glyphs=_g(**{"library.png": ("❦", SYM), "editor.png": ("✒", SYM), "settings.png": ("☿", SYM),
                 "local_videos.png": ("⚿", SYM), "uploaded_videos.png": ("☉", SYM),
                 "search_icon.png": ("☌", SYM), "search_icon_active.png": ("☌", SYM), "refresh_icon.png": ("↻", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✕", SYM), "edit_icon.png": ("✒", SYM),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("☿", SYM), "delete_icon.png": ("⚔", SYM),
                 "favorite_star_icon.png": ("✦", SYM), "favorite_star_off_icon.png": ("✧", SYM)}),
    words=dict(nav_library="Chronicle", nav_editor="Scriptorium", nav_settings="Arcana", tab_local="Vault",
               tab_uploaded="Proclaimed", header_title="The Chronicle", header_sub="being a record of deeds most worthy",
               search="Seek a deed…", empty_local="The chronicle awaits its first deed.",
               empty_uploaded="Nothing yet proclaimed.", badge_uploaded="✶ Proclaimed", fav="✦"),
    backdrop="grimoire", enter="shimmer", hover="sparkle", decor="legendary", title_style="gilded",
))

_add(Preset(
    "brutalist", "Brutalist", "Black on white, one red. Hard lines, hard cuts, no animation.",
    btn_fill="#ffffff", btn_line="#000000", btn_line_w=3.0,
    app_bg="#ececec", library_bg="#ffffff", card_bg="#ffffff", accent="#ffffff", tab="#000000",
    text="#000000", text_outline="#ffffff", text_outline_width=0.0, muted="#000000", icon="#000000",
    highlight="#ff3300", border="#000000", border_width=4.0, fav_border="#000000",
    unedited=("#000000", "#000000"), selected=("#ff3300", "#ff3300"), page_border=("#000000", "#000000"),
    radius=0,
    tab_fg="#ffffff",
    font_body="Roboto AG", font_display="Anton", display_upper=True, display_weight=400, letter_spacing=0,
    glyphs=_g(**{"library.png": ("01", None), "editor.png": ("02", None), "settings.png": ("03", None),
                 "local_videos.png": ("L", None), "uploaded_videos.png": ("P", None),
                 "search_icon.png": ("?", None), "search_icon_active.png": ("?", None), "refresh_icon.png": ("R", None),
                 "sort_icon.png": ("↓", None), "x_icon.png": ("X", None), "edit_icon.png": ("EDIT", None),
                 "copy_icon.png": ("COPY", None), "filters_icon.png": ("FX", None), "delete_icon.png": ("DEL", None),
                 "favorite_star_icon.png": ("FAV", None), "favorite_star_off_icon.png": ("—", None)}),
    words=dict(nav_library="Index", nav_editor="Cut", nav_settings="Config", tab_local="Local",
               tab_uploaded="Public", header_title="Index", header_sub="/ library / local / sort=date desc",
               search="SEARCH: _", empty_local="0 FILES.", empty_uploaded="0 PUBLIC.", badge_uploaded="PUBLIC",
               fav="■"),
    backdrop="brutalist", enter="cut", hover="invert", decor="number", title_style="brutal",
))

_add(Preset(
    "observatory", "Observatory", "A twinkling star chart with a slowly turning ringed planet.",
    btn_fill="#0a0f2e", btn_line="#4b5cff88", btn_line_w=1.0,
    app_bg="#05071a", library_bg="#070a22", card_bg="#0f1440", accent="#121a50", tab="#4b5cff",
    text="#ffffff", text_outline="#05071a", text_outline_width=1.5, muted="#7d88c4", icon="#cfd8ff",
    highlight="#ffd98a", border="#4b5cff55", border_width=1.0, fav_border="#ffd98a", glow="#4b5cff",
    unedited=("#4b5cff", "#9a5bff", "#4b5cff"), selected=("#ffd98a", "#fff1c2"), page_border=("#1d2a6b", "#05071a"),
    radius=18, icon_glow="#4b5cff",
    font_body="Montserrat AG", font_display="Montserrat AG", display_weight=400, letter_spacing=10,
    glyphs=_g(**{"library.png": ("✧", SYM), "editor.png": ("◎", SYM), "settings.png": ("⊹", SYM),
                 "local_videos.png": ("◌", SYM), "uploaded_videos.png": ("⟡", SYM),
                 "search_icon.png": ("◎", SYM), "search_icon_active.png": ("◎", SYM), "refresh_icon.png": ("↻", SYM),
                 "sort_icon.png": ("⇅", SYM), "x_icon.png": ("✕", SYM), "edit_icon.png": ("◎", SYM),
                 "copy_icon.png": ("⧉", SYM), "filters_icon.png": ("⊹", SYM), "delete_icon.png": ("✕", SYM),
                 "favorite_star_icon.png": ("✦", SYM), "favorite_star_off_icon.png": ("✧", SYM)}),
    words=dict(nav_library="Star chart", nav_editor="Lens", nav_settings="Calibrate", tab_local="Orbit",
               tab_uploaded="Transmitted", header_title="star chart", header_sub="sky survey · {count} bodies",
               search="Locate a body…", empty_local="The sky is empty. For now.", empty_uploaded="Nothing transmitted.",
               badge_uploaded="transmitted", fav="✦"),
    backdrop="stars", enter="twinkle", hover="orbit", title_style="spaced",
))

ORDER = ("classic", "synthwave", "terminal", "comic", "blueprint", "scrapbook", "ink", "aero", "grimoire",
         "brutalist", "observatory")
NAMES = {"classic": "Afterglow (classic)", **{k: v.name for k, v in PRESETS.items()}}


# --------------------------------------------------------------------------
# the active theme
# --------------------------------------------------------------------------
_active: "Preset | None | bool" = False      # False = not read yet; None = classic


def resolve(theme_id: str, dark: bool) -> "Preset | None":
    p = PRESETS.get(theme_id or "classic")
    if p is None:
        return None
    if dark and p.dark:
        over = dict(p.dark)
        words = dict(p.words)
        for k in [k for k in over if k in ("header_sub", "header_title")]:
            words[k] = over.pop(k)
        p = replace(p, words=words, **{k: (tuple(v) if isinstance(v, list) else v) for k, v in over.items()})
    return p


def active() -> "Preset | None":
    """The theme this process runs with (None = classic). Read once: a
    theme change restarts the window (see SettingsPage)."""
    global _active
    if _active is False:
        try:
            from .. import config as config_module
            a = config_module.load_raw_appearance()
            _active = resolve(getattr(a, "theme", "classic"), getattr(a, "theme_dark", False))
        except Exception:  # noqa: BLE001 -- never let a theme break startup
            _active = None
    return _active


def set_active_for_tests(theme_id: "str | None", dark: bool = False) -> None:
    global _active
    _active = resolve(theme_id or "classic", dark)
    _glyph_cache.clear()


def animations_enabled() -> bool:
    try:
        from .. import config as config_module
        return bool(getattr(config_module.load_raw_appearance(), "theme_animations", True))
    except Exception:  # noqa: BLE001
        return True


def word(key: str, default: str = "", **fmt) -> str:
    p = active()
    if p is None:
        return default
    w = p.words.get(key, default)
    try:
        return w.format(**fmt) if fmt else w.replace("{count}", "").strip()
    except (KeyError, IndexError):
        return w


def apply_to_appearance(appearance) -> None:
    """Layer the active theme's look onto an AppearanceSettings (in place)."""
    p = active()
    if p is None:
        return
    appearance.afterglow_theme_enabled = True
    appearance.custom_buttons_enabled = True
    appearance.afterglow_color_accent = p.accent[:7]
    appearance.afterglow_color_card_background = p.card_bg[:7]
    appearance.afterglow_color_app_background = p.app_bg[:7]
    appearance.afterglow_color_library = p.library_bg[:7]
    appearance.afterglow_color_turquoise = p.tab[:7]
    appearance.card_text_color = p.text[:7]
    appearance.card_text_outline_color = p.text_outline[:7]
    appearance.card_text_outline_width = p.text_outline_width
    appearance.rounded_corners_enabled = p.radius > 0
    appearance.rounded_corner_radius = p.radius if p.radius > 0 else appearance.rounded_corner_radius
    appearance.unedited_border_image_path = ""
    appearance.unedited_border_hue_shift = 0
    appearance.unedited_highlight_brightness = 100
    appearance.selected_border_image_path = ""
    appearance.sidebar_border_image_path = ""


# --------------------------------------------------------------------------
# glyph icons and gradient images
# --------------------------------------------------------------------------
_glyph_cache: dict = {}
GLYPH_PX = 256


def _qcolor(hex_or_rgba: str):
    from PySide6.QtGui import QColor
    s = hex_or_rgba.strip()
    if s.startswith("#") and len(s) == 9:            # #rrggbbaa
        c = QColor(s[:7])
        c.setAlpha(int(s[7:9], 16))
        return c
    return QColor(s)


def set_weight(font, weight: int) -> None:
    """The themes use static-weight copies of the bundled variable fonts
    ("Montserrat AG" etc., theme_fonts/): Qt renders an application-
    registered variable font at its default instance (Montserrat's is Thin)
    whatever weight is asked for, while static faces match normally --
    bold text stays bold too."""
    from PySide6.QtGui import QFont
    font.setWeight(QFont.Weight(weight))


def display_font(px: float, p: "Preset | None" = None):
    from PySide6.QtGui import QFont
    p = p or active()
    f = QFont(p.font_display)
    f.setPixelSize(max(1, round(px)))
    set_weight(f, p.display_weight)
    f.setItalic(p.display_italic)
    if p.display_upper:
        f.setCapitalization(QFont.AllUppercase)
    if p.letter_spacing:
        f.setLetterSpacing(QFont.PercentageSpacing, 100 + p.letter_spacing)
    return f


def glyph_pixmap(name: str):
    """A theme's icon for a bundled resource name, or None (use the PNG)."""
    p = active()
    if p is None or name not in p.glyphs:
        return None
    color = p.tab_fg if (p.tab_fg and name in TAB_ICONS) else p.icon
    key = (p.id, color, name)
    if key in _glyph_cache:
        return _glyph_cache[key]
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
    text, family = p.glyphs[name]
    pm = QPixmap(GLYPH_PX, GLYPH_PX)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    if family is None:
        font = display_font(10, p)
        font.setLetterSpacing(QFont.PercentageSpacing, 100)
    else:
        font = QFont(family)
        font.setWeight(QFont.Bold if family == MONO else QFont.Normal)
    # fit the glyph (or word) into ~74% of the square
    box = GLYPH_PX * 0.74
    font.setPixelSize(200)
    fm = QFontMetricsF(font)
    br = fm.tightBoundingRect(text) if text.strip() else fm.boundingRect("M")
    scale = min(box / max(1.0, br.width()), box / max(1.0, br.height()))
    font.setPixelSize(max(8, int(200 * scale)))
    fm = QFontMetricsF(font)
    br = fm.tightBoundingRect(text)
    origin = QPointF((GLYPH_PX - br.width()) / 2 - br.x(), (GLYPH_PX - br.height()) / 2 - br.y())
    path = QPainterPath()
    path.addText(origin, font, text)
    if p.icon_glow:
        glow = _qcolor(p.icon_glow)
        for w, a in ((28, 40), (18, 70), (10, 110)):
            glow.setAlpha(a)
            painter.strokePath(path, QPen(glow, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if p.icon_outline:
        painter.strokePath(path, QPen(_qcolor(p.icon_outline), 16, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.fillPath(path, _qcolor(color))
    painter.end()
    _glyph_cache[key] = pm
    return pm


_GRADIENT_ROLE = {
    "unedited_highlight_gradient.png": "unedited",
    "selected_border_gradient.png": "selected",
    "library_bg_gradient.png": "page_border",
    "editor_bg_gradient.png": "page_border",
}


def gradient_pixmap(name: str):
    """A theme's colours for one of the bundled gradient images, or None."""
    p = active()
    role = _GRADIENT_ROLE.get(name)
    if p is None or role is None:
        return None
    key = (p.id, "grad", name, getattr(p, role))
    if key in _glyph_cache:
        return _glyph_cache[key]
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QLinearGradient, QPainter, QPixmap
    stops = getattr(p, role)
    pm = QPixmap(512, 512)
    painter = QPainter(pm)
    g = QLinearGradient(QPointF(0, 0), QPointF(512, 512))
    for i, c in enumerate(stops):
        g.setColorAt(i / max(1, len(stops) - 1), _qcolor(c))
    painter.fillRect(pm.rect(), g)
    painter.end()
    _glyph_cache[key] = pm
    return pm


# --------------------------------------------------------------------------
# app-wide font + palette
# --------------------------------------------------------------------------
def apply_to_app(app) -> None:
    p = active()
    if p is None:
        return
    from PySide6.QtGui import QFont, QPalette
    f = QFont(p.font_body)
    f.setPointSizeF(max(app.font().pointSizeF(), 10.0))
    set_weight(f, 400)
    app.setFont(f)
    pal = app.palette()
    c = _qcolor
    roles = {
        QPalette.Window: c(p.app_bg), QPalette.WindowText: c(p.text if _dark(p.app_bg) == _dark(p.library_bg) else p.text),
        QPalette.Base: c(p.library_bg), QPalette.AlternateBase: c(p.card_bg), QPalette.Text: c(p.text),
        QPalette.Button: c(p.accent), QPalette.ButtonText: c(p.text), QPalette.Highlight: c(p.highlight),
        QPalette.HighlightedText: c("#000000" if not _dark(p.highlight) else "#ffffff"),
        QPalette.ToolTipBase: c(p.card_bg), QPalette.ToolTipText: c(p.text), QPalette.PlaceholderText: c(p.muted),
        QPalette.Mid: c(p.tab), QPalette.Link: c(p.highlight),
    }
    if not _dark(p.app_bg):
        roles[QPalette.WindowText] = c(p.text if _dark(p.text) else "#111111")
    for role, col in roles.items():
        for grp in (QPalette.Active, QPalette.Inactive):
            pal.setColor(grp, role, col)
        dis = c(p.muted)
        if role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            pal.setColor(QPalette.Disabled, role, dis)
    app.setPalette(pal)


def _dark(hex_color: str) -> bool:
    col = _qcolor(hex_color)
    return (0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()) < 140
