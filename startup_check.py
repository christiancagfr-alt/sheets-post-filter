"""Frozen GUI self-check with its own disposable data directory and no jobs."""
from pathlib import Path
import json
import os
import tempfile
import threading


def run_startup_check():
    report=os.environ.get('SHEETS_POST_FILTER_SMOKE_REPORT')
    if not report:
        raise ValueError('Startup check requires an explicit report path')
    report=Path(report).resolve()
    old_directory=Path.cwd()
    with tempfile.TemporaryDirectory(prefix='summary-startup-') as directory:
        os.environ['SHEETS_POST_FILTER_DATA_DIR']=directory
        import desktop_app
        from desktop_app import main
        # Use the same startup/IPC path on an ephemeral port, never the user's
        # real running instance on the fixed desktop port.
        desktop_app.MUTEX_PORT=0
        from version import APP_VERSION
        app=None
        failures=[]
        try:
            def ready(window):
                nonlocal app
                app=window
                desktop_app.MUTEX_PORT=app._mutex.getsockname()[1]
                restored=[]
                def failed(exc,value,tb):
                    failures.append(exc.__name__)
                    app.quit()
                app.report_callback_exception=failed
                def verify():
                    try:
                        app.update_idletasks()
                        assert app.winfo_viewable() and app.winfo_width()>100 and app.winfo_height()>100
                        assert app._menus and app._active_menu_id
                        assert restored==[True], 'Repeated launch did not restore the window'
                        from config_transfer_dialog import ConfigTransferDialog
                        dialog=ConfigTransferDialog(app)
                        assert dialog.menu_ids and dialog.menu_list.curselection()
                        dialog.destroy()
                        assert not failures
                        report.write_text(json.dumps({'ok':True,'version':APP_VERSION,'window_visible':True,
                            'menus':len(app._menus),'config_export_selection':True,'repeat_launch_restores_window':True}),encoding='utf-8')
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                    finally:
                        app.quit()
                def reopen():
                    app.withdraw()
                    threading.Thread(target=lambda:restored.append(desktop_app._bring_existing_window()),daemon=True).start()
                app.after(300,reopen)
                app.after(1800,verify)
                app.after(12000,lambda:(failures.append('StartupTimeout'),app.quit()))
            main(on_ready=ready)
            if failures or not report.exists():
                raise RuntimeError('Frozen GUI startup check failed: '+','.join(failures))
        finally:
            if app is not None:
                app._alive=False
                app._mutex.close()
                app.destroy()
            os.chdir(old_directory)
