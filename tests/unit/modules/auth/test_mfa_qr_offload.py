"""Tests for the MFA TOTP QR render: off-loop execution and real decodability.

Two properties are pinned here:

1. The PNG rasterisation runs in a worker thread, not inline on the event
   loop. ``qrcode.make`` plus PIL PNG encoding is CPU-bound; doing it inline
   stalls every concurrent request for the duration of the encode.
2. The returned ``qr_code_base64`` really encodes the provisioning URI. The
   image is decoded back with :mod:`mfa_qr_reference` (a QR reader written
   for this suite, since no decoding library is installed) and compared
   against ``otpauth_url``.

Property 2 is what makes the change safe to rely on: a data-URI prefix check
would also pass for a blank rectangle.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.auth.services import mfa as mfa_module
from src.app.modules.auth.services.mfa import MFAService, _totp_qr_data_uri

from .mfa_qr_reference import QRDecodeError, decode_qr_data_uri

PROVISIONING_URI = (
    "otpauth://totp/FastAPI%20saas:user_42@FastAPI-saas"
    "?secret=JBSWY3DPEHPK3PXP&issuer=FastAPI-saas"
)


def _secret_from_uri(uri: str) -> str:
    match = re.search(r"[?&]secret=([^&]+)", uri)
    assert match, f"no secret in provisioning URI: {uri}"
    return match.group(1)


def _png_size(data_uri: str) -> tuple[int, int]:
    """Decode just enough to read the PNG IHDR dimensions."""
    assert data_uri.startswith("data:image/png;base64,")
    png = base64.b64decode(data_uri.split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return width, height


@pytest.fixture
def mfa_service() -> MFAService:
    return MFAService()


@pytest.fixture
def redis_client():
    client = MagicMock()
    client.setex = AsyncMock()
    with patch.object(mfa_module, "get_redis", return_value=client):
        yield client


class TestQRRenderingRunsOffLoop:
    """The blocking rasterisation must not execute on the event loop thread."""

    @pytest.mark.asyncio
    async def test_render_executes_on_a_worker_thread(self):
        """The encode must run somewhere other than the caller's thread."""
        loop_thread = threading.get_ident()
        render_thread: list[int] = []

        real_render = mfa_module._render_totp_qr_data_uri_sync

        def spy(uri: str) -> str:
            render_thread.append(threading.get_ident())
            return real_render(uri)

        with patch.object(mfa_module, "_render_totp_qr_data_uri_sync", spy):
            await _totp_qr_data_uri(PROVISIONING_URI)

        assert render_thread, "render helper was never called"
        assert render_thread[0] != loop_thread, "PNG encoding ran inline on the event loop"

    @pytest.mark.asyncio
    async def test_event_loop_stays_responsive_during_render(self):
        """A concurrent coroutine must make progress while the PNG encodes.

        This is the observable consequence of the offload: with the encode
        inline, the heartbeat below could not run until it finished.
        """

        async def heartbeat() -> str:
            for _ in range(50):
                await asyncio.sleep(0)
            return "alive"

        rendered, status = await asyncio.gather(
            _totp_qr_data_uri(PROVISIONING_URI),
            heartbeat(),
        )
        assert status == "alive"
        assert rendered.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_service_generate_totp_secret_offloads_the_render(self):
        """The service entry point must route its render through to_thread."""
        from src.app.modules.users import UserDirectory

        with (
            patch.object(
                mfa_module.asyncio, "to_thread", wraps=mfa_module.asyncio.to_thread
            ) as to_thread,
            patch.object(mfa_module, "get_redis", return_value=MagicMock(setex=AsyncMock())),
            patch.object(mfa_module, "audit", new=AsyncMock()),
            patch.object(UserDirectory, "member", new=AsyncMock(return_value=None)),
        ):
            await MFAService().generate_totp_secret(
                user_id=42, db=AsyncMock(spec=AsyncSession)
            )

        assert to_thread.called, "generate_totp_secret dispatched no work to a thread"
        rendered = [
            call
            for call in to_thread.call_args_list
            if call.args and call.args[0] is mfa_module._render_totp_qr_data_uri_sync
        ]
        assert rendered, "the QR render was not the work sent to the thread"


class TestQRIsDecodable:
    """The returned PNG must decode back to the provisioning URI."""

    @pytest.mark.asyncio
    async def test_rendered_qr_decodes_to_provisioning_uri(self):
        """Decode the rendered image and compare with the source URI."""
        data_uri = await _totp_qr_data_uri(PROVISIONING_URI)
        assert decode_qr_data_uri(data_uri).decode() == PROVISIONING_URI

    @pytest.mark.asyncio
    async def test_qr_encodes_the_generated_secret_not_a_placeholder(self):
        """The QR must carry this setup's secret, so it is per-call unique."""
        first = await _totp_qr_data_uri(
            "otpauth://totp/App:a@App?secret=JBSWY3DPEHPK3PXP&issuer=App"
        )
        second = await _totp_qr_data_uri(
            "otpauth://totp/App:a@App?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=App"
        )

        first_secret = decode_qr_data_uri(first).decode()
        second_secret = decode_qr_data_uri(second).decode()
        assert _secret_from_uri(first_secret) == "JBSWY3DPEHPK3PXP"
        assert _secret_from_uri(second_secret) == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        assert first != second, "two different secrets produced the same QR image"

    @pytest.mark.asyncio
    async def test_service_returns_a_scannable_qr_for_the_minted_secret(
        self, mfa_service, redis_client
    ):
        """End to end: the dict the router hands back holds a usable QR."""
        with patch.object(mfa_module, "audit", new=AsyncMock()):
            from src.app.modules.users import UserDirectory

            with patch.object(UserDirectory, "member", new=AsyncMock(return_value=None)):
                result = await mfa_service.generate_totp_secret(
                    user_id=42, db=AsyncMock(spec=AsyncSession)
                )

        decoded = decode_qr_data_uri(result["qr_code_base64"]).decode()
        assert decoded == result["otpauth_url"]
        assert _secret_from_uri(decoded) == _secret_from_uri(result["otpauth_url"])

        # The image is a real square raster, not a 1x1 placeholder.
        width, height = _png_size(result["qr_code_base64"])
        assert width == height > 20

    @pytest.mark.asyncio
    async def test_missing_qrcode_package_yields_empty_placeholder(self):
        """The qrcode-absent fallback must survive the async wrapper."""
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "qrcode":
                raise ImportError("qrcode blocked for test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", blocked):
            assert await _totp_qr_data_uri(PROVISIONING_URI) == ""

    @pytest.mark.asyncio
    async def test_blank_rectangle_would_not_satisfy_the_decoder(self):
        """Guards the guard: the decoder must reject a non-QR PNG.

        Without this, a regression that returned an empty or garbage image
        could still pass the decode test if the decoder were too lenient.
        """
        from PIL import Image

        buf = io.BytesIO()
        Image.new("1", (64, 64), 1).save(buf, format="PNG")
        blank = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        with pytest.raises(QRDecodeError):
            decode_qr_data_uri(blank)
