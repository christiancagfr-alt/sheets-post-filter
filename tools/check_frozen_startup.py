"""Launch the actual built app and require a visible-window self-check report."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile


def check(application):
    application=Path(application).resolve()
    if application.suffix=='.app':
        info=plistlib.loads((application/'Contents'/'Info.plist').read_bytes())
        executable=application/'Contents'/'MacOS'/info['CFBundleExecutable']
    else:
        executable=application
    if not executable.is_file():
        raise ValueError('Built app executable missing')
    with tempfile.TemporaryDirectory(prefix='frozen-gui-check-') as directory:
        report=Path(directory)/'startup-report.json'
        env=dict(os.environ,SHEETS_POST_FILTER_SMOKE_REPORT=str(report))
        # Do not let a developer's Tcl/Python paths hide a broken bundle.
        for key in ('PYTHONPATH','PYTHONHOME','TCL_LIBRARY','TK_LIBRARY'):
            env.pop(key,None)
        result=subprocess.run([str(executable),'--startup-smoke-test'],env=env,
                              cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
        if result.returncode or not report.exists():
            error=result.stderr.decode('utf-8',errors='replace')
            if report.exists():error+='\n'+report.read_text(encoding='utf-8')
            print(error[-6000:],file=sys.stderr)
            raise RuntimeError('Built app did not create a visible window: '+str(result.returncode))
        data=json.loads(report.read_text(encoding='utf-8'))
        if not data.get('ok') or not data.get('window_visible'):
            print(data.get('error','No window report'),file=sys.stderr)
            raise RuntimeError('Built app startup report failed')
        print('FROZEN_GUI_STARTUP_OK',json.dumps(data))
        return data


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('application')
    check(parser.parse_args().application)
