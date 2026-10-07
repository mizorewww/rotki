import base64
import hashlib
import hmac
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.exchanges.binance_signing import sign_binance_payload
from rotkehlchen.types import ApiSecret, ExchangeAuthCredentials


@pytest.mark.parametrize('algorithm', ['rsa', 'ed25519'])
@pytest.mark.parametrize('pem_format', ['multiline', 'singleline', 'escaped'])
def test_binance_private_key_signature(algorithm: str, pem_format: str) -> None:
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if algorithm == 'rsa' else ed25519.Ed25519PrivateKey.generate()
    )
    secret = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    if pem_format == 'singleline':
        secret = secret.replace(b'\n', b'')
    elif pem_format == 'escaped':
        secret = secret.replace(b'\n', b'\\n')
    signature = sign_binance_payload(secret, payload := 'timestamp=123456&recvWindow=10000')
    if isinstance(key, rsa.RSAPrivateKey):
        key.public_key().verify(base64.b64decode(signature), payload.encode(), padding.PKCS1v15(), hashes.SHA256())  # noqa: E501
    else:
        key.public_key().verify(base64.b64decode(signature), payload.encode())


def test_binance_hmac_signature_is_unchanged() -> None:
    assert sign_binance_payload(b'secret', 'timestamp=123') == hmac.new(
        b'secret', b'timestamp=123', hashlib.sha256,
    ).hexdigest()


@pytest.mark.parametrize('secret', [
    b'-----BEGIN PRIVATE KEY-----invalid-----END PRIVATE KEY-----',
    b'-----BEGIN ENCRYPTED PRIVATE KEY-----redacted-----END ENCRYPTED PRIVATE KEY-----',
    b'-----BEGIN PUBLIC KEY-----redacted-----END PUBLIC KEY-----',
])
def test_binance_rejects_invalid_pem_without_exposing_secret(secret: bytes) -> None:
    with pytest.raises(RemoteError) as error:
        sign_binance_payload(secret, 'timestamp=123')
    assert secret.decode() not in str(error.value)


def test_binance_rejects_unsupported_private_key() -> None:
    secret = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with pytest.raises(RemoteError, match='Unsupported Binance private key type'):
        sign_binance_payload(secret, 'timestamp=123')


@pytest.mark.parametrize(('api_type', 'method'), [('api', 'account'), ('sapi', 'capital/config/getall'), ('fapi', 'account'), ('dapi', 'account')])  # noqa: E501
def test_binance_private_signature_is_url_encoded(
        function_scope_binance,
        api_type: str,
        method: str,
) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    function_scope_binance.edit_exchange_credentials(ExchangeAuthCredentials(
        api_key=None,
        api_secret=ApiSecret(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )),
        passphrase=None,
    ))
    with patch.object(function_scope_binance.session, 'request', return_value=Mock(status_code=200, text='{}')) as request:  # noqa: E501
        function_scope_binance.api_query_dict(api_type, method)
    prepared = requests.Request(**{
        key: value for key, value in request.call_args.kwargs.items() if key != 'timeout'
    }).prepare()
    params = parse_qs(urlparse(prepared.url).query)
    payload = urlparse(prepared.url).query.rsplit('&signature=', 1)[0]
    key.public_key().verify(base64.b64decode(params['signature'][0]), payload.encode())

    function_scope_binance.edit_exchange_credentials(ExchangeAuthCredentials(
        api_key=None, api_secret=ApiSecret(b'replacement'), passphrase=None,
    ))
    assert sign_binance_payload(function_scope_binance.secret, 'test') == hmac.new(
        b'replacement', b'test', hashlib.sha256,
    ).hexdigest()
