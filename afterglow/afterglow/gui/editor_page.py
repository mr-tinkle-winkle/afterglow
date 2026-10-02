"""
The Editor page is the Advanced Editor now (quick trimming moved into the
video previewer). The implementation lives in advanced_editor/; this
module keeps the old import path working for MainWindow.
"""
from .advanced_editor.page import AdvancedEditorPage as EditorPage

__all__ = ["EditorPage"]
