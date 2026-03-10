import hashlib
import hmac
from unittest.mock import patch

from src.app.core.json_utils import json_dumps
from src.app.signatures import generate_webhook_signature, verify_webhook_signature


def test_generate_webhook_signature_bytes() -> None:
    payload = b'{"test": "data"}'
    secret = "test_secret"
    
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    signature = generate_webhook_signature(payload, secret)  # type: ignore[arg-type]
    
    assert signature == expected


def test_generate_webhook_signature_dict() -> None:
    payload = {"test": "data"}
    secret = "test_secret"
    
    dumped = json_dumps(payload)
    if isinstance(dumped, str):
        payload_bytes = dumped.encode("utf-8")
    else:
        payload_bytes = dumped
        
    expected = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    signature = generate_webhook_signature(payload, secret)
    
    assert signature == expected


@patch("src.app.signatures.json_dumps")
def test_generate_webhook_signature_str_dump(mock_json_dumps) -> None:
    # Force json_dumps to return a string to cover the str branch
    mock_json_dumps.return_value = '{"test":"string"}'
    
    payload = {"ignored": "data"}
    secret = "test_secret"
    
    payload_bytes = b'{"test":"string"}'
    expected = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    
    signature = generate_webhook_signature(payload, secret)
    
    assert signature == expected
    mock_json_dumps.assert_called_once_with(payload)


def test_verify_webhook_signature_valid() -> None:
    payload = {"test": "data"}
    secret = "test_secret"
    
    signature = generate_webhook_signature(payload, secret)
    
    assert verify_webhook_signature(payload, secret, signature) is True


def test_verify_webhook_signature_invalid() -> None:
    payload = {"test": "data"}
    secret = "test_secret"
    
    signature = generate_webhook_signature(payload, "wrong_secret")
    
    assert verify_webhook_signature(payload, secret, signature) is False
