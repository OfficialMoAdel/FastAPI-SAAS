"""Magic link email background task."""
from arq.worker import Worker

from src.app.core.logger import get_logger
from src.app.core.utils.job_wrapper import monitor_job

logger = get_logger(__name__)


@monitor_job("send_magic_link_email_task")
async def send_magic_link_email_task(
    ctx: Worker,
    email: str,
    magic_link_url: str,
    user_id: int,
) -> str:
    """Send magic link email via notification request event outbox seam."""
    from src.app.modules.notifications.service import request_notification

    await request_notification(
        to_email=email,
        template_name="magic_link",
        context={"magic_link_url": magic_link_url},
        tenant_id=None,
        user_id=user_id,
    )
    logger.info("magic_link_email_requested", user_id=user_id, email=email)
    return f"Magic link email sent to {email}"
