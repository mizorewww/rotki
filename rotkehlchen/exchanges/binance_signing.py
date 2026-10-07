import base64
import hashlib
import hmac
import re

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from rotkehlchen.errors.misc import RemoteError


def sign_binance_payload(secret: bytes, payload: str) -> str:
    """Sign an encoded request using an HMAC secret or an unencrypted RSA/Ed25519 PEM.

    PEMs pasted on one line or with literal escaped newlines are accepted. Invalid PEMs
    must never fall back to HMAC, and parser errors must not expose key material.
    """
    if not secret.lstrip().startswith(b'-----BEGIN'):
        return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()

    pem = secret.replace(b'\\n', b'\n').strip()
    match = re.fullmatch(
        rb'-----BEGIN (PRIVATE KEY|RSA PRIVATE KEY)-----\s*(.*?)\s*-----END \1-----',
        pem,
        flags=re.DOTALL,
    )
    if match is None:
        raise RemoteError('Binance requires an unencrypted RSA or Ed25519 PEM private key')

    pem = (
        b'-----BEGIN ' + match[1] + b'-----\n' +
        re.sub(rb'\s+', b'', match[2]) + b'\n-----END ' + match[1] + b'-----\n'
    )
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as e:
        raise RemoteError('Invalid Binance private key: expected unencrypted RSA or Ed25519 PEM') from e  # noqa: E501

    if isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(payload.encode(), padding.PKCS1v15(), hashes.SHA256())
    elif isinstance(key, ed25519.Ed25519PrivateKey):
        signature = key.sign(payload.encode())
    else:
        raise RemoteError('Unsupported Binance private key type: use RSA or Ed25519')

    return base64.b64encode(signature).decode('ascii')
