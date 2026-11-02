"""Explicit light palette and non-native buttons, consistent on macOS and Windows."""
import hashlib
import tkinter as tk
from tkinter import ttk

COLORS = {
    'ink': '#1e293b', 'muted': '#526277', 'paper': '#f1f5f9',
    'card': '#ffffff', 'line': '#dce3ed', 'head': '#1e3a5f',
    'accent': '#2563eb', 'ok': '#047857', 'bad': '#e11d48',
    'log': '#101c30', 'cream': '#f8fafc', 'sa': '#17263d',
}


def install_theme(root, font):
    """Avoid Aqua drawing overrides and system dark foregrounds on light forms."""
    c = COLORS
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=font, background=c['card'], foreground=c['ink'])
    style.configure('TFrame', background=c['card'])
    style.configure('TLabel', background=c['card'], foreground=c['ink'])
    style.configure('TButton', background=c['card'], foreground=c['ink'],
                    bordercolor=c['line'], padding=(10, 6), focuscolor=c['accent'])
    style.map('TButton', background=[('disabled', '#e2e8f0'), ('pressed', '#cbd5e1'), ('active', '#e2e8f0')],
              foreground=[('disabled', '#475569'), ('active', c['ink'])])
    style.configure('TEntry', fieldbackground=c['card'], foreground=c['ink'], insertcolor=c['ink'],
                    bordercolor=c['line'], padding=4)
    style.map('TEntry', fieldbackground=[('disabled', '#e2e8f0'), ('readonly', '#f8fafc')],
              foreground=[('disabled', '#475569'), ('readonly', c['ink'])])
    style.configure('TCombobox', padding=4, font=font, fieldbackground=c['card'],
                    background='#f8fafc', foreground=c['ink'], arrowcolor=c['ink'],
                    selectbackground=c['accent'], selectforeground='#ffffff')
    style.map('TCombobox', fieldbackground=[('disabled', '#e2e8f0'), ('readonly', c['card'])],
              foreground=[('disabled', '#475569'), ('readonly', c['ink'])],
              selectbackground=[('readonly', c['card'])], selectforeground=[('readonly', c['ink'])],
              arrowcolor=[('disabled', '#475569')])
    for name in ('TCheckbutton', 'TRadiobutton'):
        style.configure(name, background=c['card'], foreground=c['ink'], focuscolor=c['accent'])
        style.map(name, background=[('active', c['card'])],
                  foreground=[('disabled', '#475569'), ('active', c['ink'])])
    style.configure('Vertical.TScrollbar', background='#cbd5e1', troughcolor=c['paper'], borderwidth=0, arrowsize=12)
    # Widget-class defaults also apply to forms created later or in Toplevels.
    # Explicit per-widget colors (sidebar/log) retain precedence.
    defaults = {
        '*Frame.background': c['card'], '*Toplevel.background': c['paper'],
        '*Canvas.background': c['card'], '*Label.background': c['card'], '*Label.foreground': c['ink'],
        '*Entry.background': c['card'], '*Entry.foreground': c['ink'], '*Entry.insertBackground': c['ink'],
        '*Entry.disabledBackground': '#e2e8f0', '*Entry.disabledForeground': '#475569',
        '*Entry.readonlyBackground': '#f8fafc', '*Entry.selectBackground': c['accent'], '*Entry.selectForeground': '#ffffff',
        '*Text.background': c['card'], '*Text.foreground': c['ink'], '*Text.insertBackground': c['ink'],
        '*Text.selectBackground': c['accent'], '*Text.selectForeground': '#ffffff',
        '*Listbox.background': c['card'], '*Listbox.foreground': c['ink'],
        '*Listbox.selectBackground': c['accent'], '*Listbox.selectForeground': '#ffffff',
        '*Checkbutton.background': c['card'], '*Checkbutton.foreground': c['ink'],
        '*Checkbutton.activeBackground': c['card'], '*Checkbutton.activeForeground': c['ink'],
        '*Checkbutton.disabledForeground': '#475569', '*Checkbutton.selectColor': c['card'],
        '*Radiobutton.background': c['card'], '*Radiobutton.foreground': c['ink'],
        '*Radiobutton.activeBackground': c['card'], '*Radiobutton.activeForeground': c['ink'],
        '*Radiobutton.disabledForeground': '#475569', '*Radiobutton.selectColor': c['card'],
    }
    for pattern, value in defaults.items():
        root.option_add(pattern, value, 'widgetDefault')
    root.option_add('*TCombobox*Listbox.font', font, 'widgetDefault')
    root.option_add('*TCombobox*Listbox.background', c['card'], 'widgetDefault')
    root.option_add('*TCombobox*Listbox.foreground', c['ink'], 'widgetDefault')
    root.option_add('*TCombobox*Listbox.selectBackground', c['accent'], 'widgetDefault')
    root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff', 'widgetDefault')


class ColorButton(ttk.Button):
    """A clam ttk button accepting the app's existing tk color/padding options.

    ttk retains keyboard activation, focus, invoke and disabled-state handling;
    colors belong to named styles instead of Aqua's native tk.Button drawing.
    Equal palettes reuse a style, including when sidebar rows are rebuilt.
    """
    _appearance_keys = frozenset({'bg', 'fg', 'activebackground', 'activeforeground',
        'font', 'padx', 'pady', 'anchor', 'justify', 'bd', 'relief',
        'highlightbackground', 'highlightcolor', 'highlightthickness', 'disabledforeground'})

    def __init__(self, master, **kwargs):
        self._appearance = dict(bg=COLORS['card'], fg=COLORS['ink'],
            activebackground='#e2e8f0', activeforeground=COLORS['ink'],
            font=('TkDefaultFont', 10), padx=10, pady=6, anchor='center', bd=1,
            relief='solid', highlightcolor=COLORS['accent'])
        self._appearance.update({k: kwargs.pop(k) for k in list(kwargs) if k in self._appearance_keys})
        kwargs.setdefault('width', 0)  # Natural text width, including compact sidebar actions.
        super().__init__(master, **kwargs)
        self._apply_appearance()

    def _apply_appearance(self):
        values = self._appearance
        identity = repr(sorted(values.items())).encode('utf-8')
        name = 'App' + hashlib.sha256(identity).hexdigest()[:20] + '.TButton'
        style = ttk.Style(self)
        if not style.configure(name):
            style.configure(name, background=values['bg'], foreground=values['fg'],
                font=values['font'], padding=(values['padx'], values['pady']), anchor=values['anchor'],
                borderwidth=values['bd'], relief=values['relief'], bordercolor=COLORS['line'],
                lightcolor=values['bg'], darkcolor=values['bg'], focuscolor=values['highlightcolor'])
            style.map(name,
                background=[('disabled', '#e2e8f0'), ('pressed', values['activebackground']), ('active', values['activebackground'])],
                foreground=[('disabled', '#475569'), ('pressed', values['activeforeground']), ('active', values['activeforeground'])],
                bordercolor=[('focus', COLORS['accent'])])
        super().configure(style=name)

    def configure(self, cnf=None, **kwargs):
        if cnf is not None and not isinstance(cnf, dict):
            return super().configure(cnf, **kwargs)
        options = {**(cnf or {}), **kwargs}
        if not options:
            return super().configure()
        appearance = {key: options.pop(key) for key in list(options) if key in self._appearance_keys}
        if appearance:
            self._appearance.update(appearance)
            self._apply_appearance()
        if options:
            return super().configure(**options)

    config = configure

    def cget(self, key):
        if key in self._appearance_keys:
            return self._appearance.get(key, '')
        return super().cget(key)
