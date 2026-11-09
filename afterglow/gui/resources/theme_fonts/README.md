# Theme fonts

Fonts the themes (gui/theme_presets.py) need beyond the editor's bundled set
in ../fonts. Registered at startup alongside them (gui/fonts.py) but not
offered in the Advanced Editor's font list.

| File | Used for | License |
|------|----------|---------|
| DejaVuSans.ttf | the symbol glyphs themes use as icons (▶ ✂ ⚙ ★ ...) | DejaVu (Bitstream Vera) license, `licenses/dejavu-LICENSE.txt` |
| DejaVuSansMono(-Bold).ttf | Phosphor Terminal | same |
| AfterglowInkGlyphs.otf | Sumi Ink's icons (残 光 巻 筆 硯 蔵 送 墨 印 一) -- a 10-glyph subset of Noto Serif CJK JP Bold, renamed as the OFL requires for a modified version | SIL OFL 1.1, `licenses/noto-cjk-OFL.txt` |
| MontserratAG-*, PlayfairDisplayAG-*, RobotoAG-*, OrbitronAG-*, CinzelAG-*, CaveatAG-* | the themes' body / title fonts | SIL OFL 1.1, `licenses/` |

The `* AG` fonts are fixed-weight instances of the variable fonts in ../fonts
(made with fontTools' `varLib.instancer`), renamed with an " AG" suffix as the
OFL requires for modified versions. Why: Qt renders an application-registered
variable font at its default instance (Montserrat's is Thin) no matter what
weight is requested; separate static faces are matched by weight normally.

Overlaps are merged too (`fontTools.ttLib.removeOverlaps`, needs skia-pathops): a variable font's
glyphs are built from overlapping contours, and anything that draws text as a path with the
default odd-even fill (OutlinedLabel, the theme glyphs) showed holes -- "black dots" in t, p, a.
