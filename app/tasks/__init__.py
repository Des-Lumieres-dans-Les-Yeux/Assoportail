"""Celery tasks package.

The Celery application instance is configured here.
Tasks are defined in sub-modules and auto-discovered.
"""

import os

from celery import Celery


def make_celery() -> Celery:
    """Create a Celery application configured from environment variables.

    Each task execution is automatically wrapped in a Flask application context
    via ``ContextTask``.  The Flask app is created lazily on the first task run
    so that importing this module in the web process (e.g. to call .delay())
    does not create a second Flask application.

    Returns:
        Configured Celery application instance.
    """
    celery_app = Celery(
        "assoportail",
        broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        include=[
            "app.tasks.centers",
            "app.tasks.cerfa",
            "app.tasks.email_polling",
            "app.tasks.email_rules",
            "app.tasks.events",
            "app.tasks.mailing",
            "app.tasks.notifications",
            "app.tasks.reminders",
            "app.tasks.social",
        ],
    )

    class ContextTask(celery_app.Task):
        abstract = True
        _flask_app = None

        def _flask(self):
            if ContextTask._flask_app is None:
                from app import create_app

                ContextTask._flask_app = create_app()
            return ContextTask._flask_app

        def __call__(self, *args, **kwargs):
            app = self._flask()
            base_url = app.config.get("TASK_BASE_URL", "http://localhost")
            with app.app_context():
                with app.test_request_context(base_url=base_url):
                    return self.run(*args, **kwargs)

    celery_app.Task = ContextTask

    poll_interval = int(os.environ.get("GMAIL_POLL_INTERVAL", 300))
    celery_app.conf.beat_schedule = {
        "poll-gmail-inbox": {
            "task": "tasks.poll_gmail_inbox",
            "schedule": float(poll_interval),
        },
        "check-membership-expiry": {
            "task": "tasks.check_membership_expiry",
            "schedule": 86400.0,  # daily
        },
        "send-event-reminders": {
            "task": "tasks.send_event_reminders",
            "schedule": 86400.0,  # daily
        },
        "update-event-statuses": {
            "task": "tasks.update_event_statuses",
            "schedule": 86400.0,  # daily
        },
        "publish-scheduled-social-posts": {
            "task": "tasks.publish_scheduled_social_posts",
            "schedule": 300.0,  # every 5 minutes
        },
    }
    celery_app.conf.timezone = "UTC"
    celery_app.conf.broker_connection_retry_on_startup = True
    return celery_app


celery = make_celery()
