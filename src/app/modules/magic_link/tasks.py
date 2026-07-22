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
    """Send magic link email via notification service."""
    from src.app.core.db.database import async_session_maker
    from src.app.modules.notifications.service import NotificationService

    async with async_session_maker() as db:
        service = NotificationService(db)
        await service.send_email(
            tenant_id=None,
            to_email=email,
            template_name="magic_link",
            context={"magic_link_url": magic_link_url},
            user_id=user_id,
        )
    logger.info("magic_link_email_sent", user_id=user_id, email=email)
    return f"Magic link email sent to {email}"
