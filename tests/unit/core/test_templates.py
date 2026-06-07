from unittest.mock import AsyncMock, MagicMock

import pytest
from jinja2 import TemplateNotFound

from src.app.templates import email_templates_service


@pytest.fixture
def mock_session_maker():
    mock_maker = MagicMock()
    mock_session = AsyncMock()
    mock_maker.return_value.__aenter__.return_value = mock_session

    # Store original and restore after test
    original_maker = email_templates_service._session_maker
    email_templates_service._session_maker = mock_maker
    yield mock_session
    email_templates_service._session_maker = original_maker


@pytest.mark.asyncio
async def test_get_template_override_found(mock_session_maker) -> None:
    expected_override = MagicMock()
    expected_override.tenant_id = "tenant-123"
    expected_override.template_name = "test_template"
    expected_override.subject_override = "Test Subject"
    expected_override.content = "<p>Test Content</p>"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = expected_override
    mock_session_maker.execute.return_value = mock_result

    result = await email_templates_service._get_template_override("tenant-123", "test_template")
    assert result == expected_override


@pytest.mark.asyncio
async def test_get_template_override_not_found(mock_session_maker) -> None:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session_maker.execute.return_value = mock_result

    result = await email_templates_service._get_template_override("tenant-123", "test_template")
    assert result is None


@pytest.mark.asyncio
async def test_render_template_with_override(mock_session_maker) -> None:
    expected_override = MagicMock()
    expected_override.tenant_id = "tenant-123"
    expected_override.template_name = "test_template"
    expected_override.subject_override = "Subject: {{ name }}"
    expected_override.content = "<title>Should be ignored</title><p>Hello {{ name }}</p>"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = expected_override
    mock_session_maker.execute.return_value = mock_result

    subject, html = await email_templates_service.render_template("tenant-123", "test_template", {"name": "World"})

    assert subject == "Subject: World"
    assert html == "<title>Should be ignored</title><p>Hello World</p>"


@pytest.mark.asyncio
async def test_render_template_override_error_fallback(mock_session_maker, tmp_path, monkeypatch) -> None:
    expected_override = MagicMock()
    expected_override.tenant_id = "tenant-123"
    expected_override.template_name = "test_template"
    expected_override.subject_override = "Subject: {{ unclosed"  # invalid jinja
    expected_override.content = "<p>Hello</p>"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = expected_override
    mock_session_maker.execute.return_value = mock_result

    # Setup fallback template
    d = tmp_path / "templates"
    d.mkdir()
    p = d / "test_template.html"
    p.write_text("<title>Fallback Subject</title><p>Fallback Content {{ name }}</p>", encoding="utf-8")

    class MockSettings:
        EMAIL_TEMPLATES_DIR = str(d)

    monkeypatch.setattr("src.app.templates.settings", MockSettings())

    subject, html = await email_templates_service.render_template("tenant-123", "test_template", {"name": "World"})

    assert subject == "Fallback Subject"
    assert html == "<p>Fallback Content World</p>"


@pytest.mark.asyncio
async def test_render_template_default_success(mock_session_maker, tmp_path, monkeypatch) -> None:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session_maker.execute.return_value = mock_result

    # Setup default template
    d = tmp_path / "templates"
    d.mkdir()
    p = d / "test_template.html"
    p.write_text("<title>Default {{ name }}</title><p>Default Content</p>", encoding="utf-8")

    class MockSettings:
        EMAIL_TEMPLATES_DIR = str(d)

    monkeypatch.setattr("src.app.templates.settings", MockSettings())

    subject, html = await email_templates_service.render_template("tenant-123", "test_template", {"name": "World"})

    assert subject == "Default World"
    assert html == "<p>Default Content</p>"


@pytest.mark.asyncio
async def test_render_template_not_found(mock_session_maker, tmp_path, monkeypatch) -> None:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session_maker.execute.return_value = mock_result

    d = tmp_path / "templates"
    d.mkdir()
    # Don't create the file

    class MockSettings:
        EMAIL_TEMPLATES_DIR = str(d)

    monkeypatch.setattr("src.app.templates.settings", MockSettings())

    with pytest.raises(TemplateNotFound):
        await email_templates_service.render_template("tenant-123", "test_template", {"name": "World"})


@pytest.mark.asyncio
async def test_render_template_default_error(mock_session_maker, tmp_path, monkeypatch) -> None:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session_maker.execute.return_value = mock_result

    # Setup default template with invalid jinja
    d = tmp_path / "templates"
    d.mkdir()
    p = d / "test_template.html"
    p.write_text("<title>Default</title><p>Default Content {{ unclosed </p>", encoding="utf-8")

    class MockSettings:
        EMAIL_TEMPLATES_DIR = str(d)

    monkeypatch.setattr("src.app.templates.settings", MockSettings())

    from jinja2.exceptions import TemplateSyntaxError

    with pytest.raises(TemplateSyntaxError):
        await email_templates_service.render_template("tenant-123", "test_template", {"name": "World"})
