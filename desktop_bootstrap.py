"""Capture startup failures before the desktop module has created its window."""
from pathlib import Path
import os
import sys


def main():
    if '--startup-smoke-test' in sys.argv:
        try:
            from startup_check import run_startup_check
            run_startup_check()
        except Exception:
            import json
            import traceback
            report=os.environ.get('SHEETS_POST_FILTER_SMOKE_REPORT')
            if report:
                Path(report).write_text(json.dumps({'ok':False,'error':traceback.format_exc()}),encoding='utf-8')
            raise
        return
    try:
        from desktop_app import main as start
        start()
    except Exception:
        import traceback
        if sys.platform == 'darwin':
            directory=Path.home()/'Library'/'Application Support'/'sheets-post-filter'
        else:
            directory=Path(os.environ.get('LOCALAPPDATA') or Path.home())/'sheets-post-filter'
        log=directory/'desktop_error.log'
        try:
            directory.mkdir(parents=True,exist_ok=True)
            log.write_text(traceback.format_exc(),encoding='utf-8')
        except OSError:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            root=tk.Tk();root.withdraw()
            messagebox.showerror('数据汇总工具启动失败',
                '程序未能创建主窗口，原配置已保留。\n启动错误已记录在：\n'+str(log),parent=root)
            root.destroy()
        except Exception:
            pass
        raise


if __name__=='__main__':
    main()
