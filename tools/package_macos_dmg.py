"""Create and verify a read-only DMG using Apple's bundled tools only."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

from check_frozen_startup import check


def run(*args):
    return subprocess.run(list(map(str,args)),check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)


def package(application,output):
    if sys.platform!='darwin':raise RuntimeError('DMG must be built on macOS')
    application=Path(application).resolve();output=Path(output).resolve()
    if not application.is_dir() or application.suffix!='.app':raise ValueError('Missing app bundle')
    run('codesign','--verify','--deep','--strict',application)
    check(application)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='summary-dmg-') as temporary:
        stage=Path(temporary)/'contents';stage.mkdir()
        # ditto preserves executable permissions and framework/bundle symlinks.
        run('ditto',application,stage/application.name)
        (stage/'Applications').symlink_to('/Applications',target_is_directory=True)
        (stage/'安装说明.txt').write_text(
            '将“数据汇总工具.app”拖入 Applications（应用程序）。\n'
            '复制完成后从“应用程序”启动，再推出此磁盘。\n'
            'Apple Silicon 与 Intel 安装包需按芯片类型选择。\n'
            '原配置保留在用户 Library/Application Support/sheets-post-filter。\n'
            '本构建使用本机临时签名验证完整性，尚未经过 Apple Developer ID 公证。\n',encoding='utf-8')
        run('hdiutil','create','-volname','数据汇总工具','-srcfolder',stage,'-format','UDZO','-ov',output)
        run('hdiutil','verify',output)
        mount=Path(temporary)/'mounted';mount.mkdir()
        attached=False
        try:
            data=run('hdiutil','attach','-readonly','-nobrowse','-mountpoint',mount,'-plist',output)
            attached=True
            plistlib.loads(data.stdout)
            app=mount/application.name
            if not app.is_dir() or not (mount/'Applications').is_symlink():
                raise RuntimeError('DMG layout missing app / Applications shortcut')
            run('codesign','--verify','--deep','--strict',app)
            installed=Path(temporary)/'installed'/application.name
            installed.parent.mkdir()
            run('ditto',app,installed)
            run('codesign','--verify','--deep','--strict',installed)
            check(installed)
        finally:
            if attached:run('hdiutil','detach',mount)
    print('DMG_VERIFIED_INSTALL_AND_STARTUP_OK',output.name)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('application');parser.add_argument('output')
    args=parser.parse_args();package(args.application,args.output)
