"""Real TLS handshakes with an ephemeral CA: no public network or credentials."""
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import HTTPServer, BaseHTTPRequestHandler

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from secure_http import create_ssl_context


class TLSBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();directory=Path(cls.temp.name)
        now=datetime.now(timezone.utc)
        ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Example ephemeral CA')])
        ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
            .not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),True)
            .sign(ca_key,hashes.SHA256()))
        cls.ca_file=directory/'ca.pem';cls.ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]))
              .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).sign(ca_key,hashes.SHA256()))
        cert_file=directory/'server.pem';cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file=directory/'server.key';key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
        cls.server=HTTPServer(('127.0.0.1',0),Handler)
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(cert_file,key_file)
        cls.server.socket=server_context.wrap_socket(cls.server.socket,server_side=True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def connect(self,context,hostname):
        with socket.create_connection(self.server.server_address,timeout=3) as connection:
            with context.wrap_socket(connection,server_hostname=hostname) as secured:
                self.assertTrue(secured.getpeercert())

    def test_shipped_ca_connects_even_when_system_store_is_empty(self):
        empty=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch('secure_http.ssl.create_default_context',return_value=empty),patch('secure_http.certifi.where',return_value=str(self.ca_file)):
            context=create_ssl_context()
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)
        self.connect(context,'localhost')

    def test_untrusted_chain_is_still_rejected(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.connect(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),'localhost')

    def test_wrong_hostname_is_still_rejected_with_shipped_ca(self):
        with patch('secure_http.certifi.where',return_value=str(self.ca_file)):
            context=create_ssl_context()
        with self.assertRaises(ssl.SSLCertVerificationError):self.connect(context,'wrong.example.com')
