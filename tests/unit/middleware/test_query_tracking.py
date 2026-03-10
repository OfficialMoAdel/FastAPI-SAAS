from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from starlette.responses import JSONResponse

from src.app.middleware.query_tracking import QueryTrackingMiddleware


@pytest.fixture
def mock_app():
    return AsyncMock()


@pytest.fixture
def middleware(mock_app):
    return QueryTrackingMiddleware(mock_app)


@pytest.mark.asyncio
async def test_dispatch_success(middleware) -> None:
    mock_request = AsyncMock(spec=Request)
    mock_request.url.path = "/test/path"
    
    expected_response = JSONResponse({"status": "ok"})
    async def mock_call_next(request):
        return expected_response
        
    with patch("src.app.middleware.query_tracking.start_request_tracking") as mock_start, \
         patch("src.app.middleware.query_tracking.end_request_tracking") as mock_end:
         
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        assert response == expected_response
        mock_start.assert_called_once()
        mock_end.assert_called_once_with(
            request_path="/test/path",
            threshold=3,
            detailed_logs=False,
        )


@pytest.mark.asyncio
async def test_dispatch_exception_in_call_next(middleware) -> None:
    mock_request = AsyncMock(spec=Request)
    mock_request.url.path = "/test/path"
    
    async def mock_call_next(request):
        raise ValueError("Handler error")
        
    with patch("src.app.middleware.query_tracking.start_request_tracking") as mock_start, \
         patch("src.app.middleware.query_tracking.end_request_tracking") as mock_end:
         
        with pytest.raises(ValueError, match="Handler error"):
            await middleware.dispatch(mock_request, mock_call_next)
        
        mock_start.assert_called_once()
        mock_end.assert_called_once_with(
            request_path="/test/path",
            threshold=3,
            detailed_logs=False,
        )


@pytest.mark.asyncio
async def test_dispatch_exception_in_end_tracking(middleware) -> None:
    mock_request = AsyncMock(spec=Request)
    mock_request.url.path = "/test/path"
    
    expected_response = JSONResponse({"status": "ok"})
    async def mock_call_next(request):
        return expected_response
        
    with patch("src.app.middleware.query_tracking.start_request_tracking") as mock_start, \
         patch("src.app.middleware.query_tracking.end_request_tracking", side_effect=Exception("Tracking error")) as mock_end, \
         patch("src.app.middleware.query_tracking.logger.debug") as mock_logger:
         
        response = await middleware.dispatch(mock_request, mock_call_next)
        
        assert response == expected_response
        mock_start.assert_called_once()
        mock_end.assert_called_once()
        
        # Verify error was logged and caught (not re-raised)
        mock_logger.assert_called_once()
        assert "Error ending query tracking" in mock_logger.call_args[0][0]
