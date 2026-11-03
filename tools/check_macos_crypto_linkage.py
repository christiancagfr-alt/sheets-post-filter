"""Fail before bundling if cryptography could collide with Python's OpenSSL."""
from pathlib import Path
import subprocess
import sys


def main():
    if sys.platform!='darwin':raise RuntimeError('This check runs on macOS')
    from cryptography.hazmat.bindings import _rust
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    output=subprocess.check_output(['otool','-L',str(Path(_rust.__file__).resolve())],text=True)
    dependencies=output.splitlines()[1:]
    if any('libssl' in line or 'libcrypto' in line for line in dependencies):
        raise RuntimeError('cryptography links a shared OpenSSL library; rebuild with OPENSSL_STATIC=1')
    # Exercise the module's OpenSSL binding rather than trusting metadata.
    cipher=AESGCM(AESGCM.generate_key(bit_length=256))
    nonce=b'\x00'*12
    assert cipher.decrypt(nonce,cipher.encrypt(nonce,b'build-check',None),None)==b'build-check'
    print('MACOS_CRYPTOGRAPHY_STATIC_OPENSSL_OK')


if __name__=='__main__':main()
