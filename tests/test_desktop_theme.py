"""Opt-in real Tk checks; run in a desktop session with isolated app data."""
import os
import unittest
import tkinter as tk
from tkinter import ttk

from desktop_theme import COLORS, ColorButton, install_theme


def contrast(first, second):
    def luminance(color):
        values = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
        linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
        return sum(x*y for x, y in zip(linear, (.2126, .7152, .0722)))
    a, b = sorted((luminance(first), luminance(second)))
    return (b+.05)/(a+.05)


@unittest.skipUnless(os.environ.get('SHEETS_POST_FILTER_UI_TESTS') == '1', 'requires a desktop Tk session')
class DesktopThemeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        install_theme(self.root, ('TkDefaultFont', 10))

    def tearDown(self):
        self.root.destroy()

    def test_button_invokes_once_and_disabled_button_cannot_invoke(self):
        calls = []
        button = ColorButton(self.root, text='示例按钮', command=lambda: calls.append(True),
                             bg=COLORS['head'], fg='#ffffff', activeforeground='#ffffff', activebackground=COLORS['accent'])
        button.invoke()
        self.assertEqual(calls, [True])
        button.configure(state='disabled')
        button.invoke()
        self.assertEqual(calls, [True])
        button.configure(state='normal', text='更新示例', bg=COLORS['accent'])
        button.invoke()
        self.assertEqual(calls, [True, True])
        self.assertEqual(button.cget('text'), '更新示例')
        self.assertEqual(ttk.Style(self.root).theme_use(), 'clam')

    def test_sidebar_recolor_and_every_button_state_has_readable_contrast(self):
        style = ttk.Style(self.root)
        button = ColorButton(self.root, bg=COLORS['sa'], fg=COLORS['cream'],
                             activebackground=COLORS['accent'], activeforeground=COLORS['cream'])
        for bg in (COLORS['sa'], COLORS['accent']):
            button.configure(bg=bg)
            for state in ((), ('active',), ('pressed',), ('focus',), ('disabled',)):
                name = button.cget('style')
                foreground = style.lookup(name, 'foreground', state)
                background = style.lookup(name, 'background', state)
                self.assertGreaterEqual(contrast(foreground, background), 4.5, (state, foreground, background))
        button.configure(bg=COLORS['sa'])
        original = button.cget('style')
        button.configure(bg=COLORS['accent'])
        button.configure(bg=COLORS['sa'])
        self.assertEqual(button.cget('style'), original)

    def test_later_dialog_forms_do_not_inherit_os_dark_text_or_background(self):
        dialog = tk.Toplevel(self.root)
        entry, text, label = tk.Entry(dialog), tk.Text(dialog), tk.Label(dialog, text='示例')
        for widget in (entry, text, label):
            self.assertEqual(widget.cget('background'), COLORS['card'])
            self.assertEqual(widget.cget('foreground'), COLORS['ink'])
        for widget in (entry, text):
            self.assertEqual(widget.cget('insertbackground'), COLORS['ink'])
        style = ttk.Style(dialog)
        for state in (('readonly',), ('disabled',)):
            self.assertGreaterEqual(contrast(style.lookup('TCombobox', 'foreground', state),
                                             style.lookup('TCombobox', 'fieldbackground', state)), 4.5)
        dialog.destroy()


if __name__ == '__main__':
    unittest.main()
