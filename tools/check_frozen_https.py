"""Test urllib TLS from the shipped executable with default CA paths absent."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile


def check(application):
    application=Path(application).resolve()
    if application.suffix=='.app':
        info=plistlib.loads((application/'Contents'/'Info.plist').read_bytes())
        executable=application/'Contents'/'MacOS'/info['CFBundleExecutable']
    else:executable=application
    with tempfile.TemporaryDirectory(prefix='frozen-ca-check-') as directory:
        directory=Path(directory);report=directory/'report.json'
        env=dict(os.environ,SHEETS_POST_FILTER_HTTPS_REPORT=str(report),
                 SSL_CERT_FILE=str(directory/'missing-ca.pem'),SSL_CERT_DIR=str(directory/'missing-ca-dir'))
        for key in ('PYTHONPATH','PYTHONHOME','TCL_LIBRARY','TK_LIBRARY'):
            env.pop(key,None)
        result=subprocess.run([str(executable),'--https-smoke-test'],env=env,cwd=directory,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35)
        if result.returncode or not report.exists():
            raise RuntimeError('Frozen certificate-chain check failed: '+result.stderr.decode('utf-8',errors='replace')[-2500:])
        data=json.loads(report.read_text())
        assert data.get('ok') and data.get('certificate_verification') and data.get('bundled_ca_loaded')
        print('FROZEN_HTTPS_CERTIFICATE_VERIFICATION_OK',json.dumps(data))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('application');check(parser.parse_args().application)
