"""Frozen GUI self-check with its own disposable data directory and no jobs."""
from pathlib import Path
import json
import os
import tempfile


def run_startup_check():
    report=os.environ.get('SHEETS_POST_FILTER_SMOKE_REPORT')
    if not report:
        raise ValueError('Startup check requires an explicit report path')
    report=Path(report).resolve()
    old_directory=Path.cwd()
    with tempfile.TemporaryDirectory(prefix='summary-startup-') as directory:
        os.environ['SHEETS_POST_FILTER_DATA_DIR']=directory
        from desktop_app import DesktopApp
        from version import APP_VERSION
        app=None
        failures=[]
        try:
            app=DesktopApp()
            def failed(exc,value,tb):
                failures.append(exc.__name__)
                app.quit()
            app.report_callback_exception=failed
            app.show_main_window()
            def verify():
                try:
                    app.update_idletasks()
                    assert app.winfo_viewable() and app.winfo_width()>100 and app.winfo_height()>100
                    assert app._menus and app._active_menu_id
                    from config_transfer_dialog import ConfigTransferDialog
                    dialog=ConfigTransferDialog(app)
                    assert dialog.menu_ids and dialog.menu_list.curselection()
                    dialog.destroy()
                    assert not failures
                    report.write_text(json.dumps({'ok':True,'version':APP_VERSION,'window_visible':True,
                        'menus':len(app._menus),'config_export_selection':True}),encoding='utf-8')
                except Exception as exc:
                    failures.append(type(exc).__name__)
                finally:
                    app.quit()
            app.after(1500,verify)
            app.after(12000,lambda:(failures.append('StartupTimeout'),app.quit()))
            app.mainloop()
            if failures or not report.exists():
                raise RuntimeError('Frozen GUI startup check failed: '+','.join(failures))
        finally:
            if app is not None:
                app._alive=False
                app.destroy()
            os.chdir(old_directory)
