// A ~40-line shim: LayerShellQt has a C++ API only (no Python bindings exist), so this
// exposes the one call afterglow's indicator helper needs as a plain C function that
// Python loads with ctypes (afterglow/indicator/layershell.py).
//
// Build against the SAME Qt as PySide6 -- see flake.nix (afterglowLayerShell).
#include <LayerShellQt/Window>
#include <QMargins>
#include <QWindow>

extern "C" {

// Turns an existing, not-yet-shown QWindow into an overlay-layer surface:
//   layer            overlay (above fullscreen windows)
//   keyboard         none (never takes focus)
//   exclusive zone   0 (doesn't push other windows)
//   anchors          LayerShellQt::Window::Anchor bits: top=1 bottom=2 left=4 right=8
//   margins          pixels from the anchored edges
// The output is the window's own QScreen (set it with QWindow::setScreen first).
// Returns 0 on success, -1 if the window isn't usable as a layer surface.
int afterglow_layershell_configure(void *qwindow, int anchors, int top, int right, int bottom, int left)
{
    auto *window = static_cast<QWindow *>(qwindow);
    if (!window) {
        return -1;
    }
    auto *ls = LayerShellQt::Window::get(window);
    if (!ls) {
        return -1;
    }
    ls->setScope(QStringLiteral("afterglow-indicator"));
    ls->setLayer(LayerShellQt::Window::LayerOverlay);
    ls->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
    ls->setExclusiveZone(0);
    ls->setAnchors(LayerShellQt::Window::Anchors(anchors));
    ls->setMargins(QMargins(left, top, right, bottom));
    return 0;
}

} // extern "C"
