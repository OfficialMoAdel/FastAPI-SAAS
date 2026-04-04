"""Email templates service."""

import re
from pathlib import Path
from typing import Any, cast

from jinja2 import Template, TemplateNotFound
from sqlalchemy import select

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.modules.notifications.models import EmailTemplateOverride

logger = get_logger(__name__)

# OPT-12: Pre-compile regex patterns at module level
_TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_TITLE_STRIP_PATTERN = re.compile(r"<title>.*?</title>", re.DOTALL)


class EmailTemplatesService:
    """Service for rendering email templates."""

    def __init__(self):
        """Initialize the email templates service."""
        from src.app.core.db.database import async_session_maker

        self._session_maker = async_session_maker

    async def _get_template_override(self, tenant_id: str, template_name: str) -> EmailTemplateOverride | None:
        """Get template override from database."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(EmailTemplateOverride).where(
                    EmailTemplateOverride.tenant_id == tenant_id,  # type: ignore[arg-type]
                    EmailTemplateOverride.template_name == template_name,
                )
            )
            return cast(EmailTemplateOverride | None, result.scalar_one_or_none())

    async def render_template(self, tenant_id: str, template_name: str, context: dict[str, Any]) -> tuple[str, str]:
        """
        Render email template with context.

        Args:
            tenant_id: Tenant ID
            template_name: Name of template (e.g., 'password_changed')
            context: Template context variables

        Returns
        -------
            Tuple of (subject, html_content)

        Raises
        ------
            TemplateNotFound: If template file doesn't exist
        """
        # Check for tenant override
        override = await self._get_template_override(tenant_id, template_name)

        if override:
            # Use tenant's custom template
            subject = override.subject_override or "{{ subject }}"
            html_content = override.content or ""

            try:
                from jinja2.sandbox import SandboxedEnvironment
                env = SandboxedEnvironment()
                subject_template = env.from_string(subject)
                html_template = env.from_string(html_content)

                rendered_subject = subject_template.render(**context)
                rendered_html = html_template.render(**context)

                return rendered_subject, rendered_html

            except Exception as e:
                logger.error(
                    "Failed to render custom template for tenant :",
                    template_name=template_name,
                    tenant_id=tenant_id,
                    error=e,
                )
                # Fall back to default template

        # Load default template
        try:
            html_content = (
                Path(settings.EMAIL_TEMPLATES_DIR).joinpath(f"{template_name}.html").read_text(encoding="utf-8")
            )

        except FileNotFoundError:
            raise TemplateNotFound(f"Email template not found: {template_name}")

        # Render template
        try:
            template = Template(html_content)
            rendered_html = template.render(**context)

            # Extract subject from title tag (OPT-12: use pre-compiled patterns)
            subject_match = _TITLE_PATTERN.search(rendered_html)
            subject = subject_match.group(1).strip() if subject_match else ""

            # Clean up title tag from rendered HTML
            rendered_html = _TITLE_STRIP_PATTERN.sub("", rendered_html)

            return subject, rendered_html

        except Exception as e:
            logger.error("Failed to render template :", template_name=template_name, error=e)
            raise


email_templates_service = EmailTemplatesService()
