"""Email rules engine — applies EmailRule conditions to InboundEmail instances.

The engine evaluates all active rules in priority order.
For each matching rule, actions are executed and logged.

Condition operators
-------------------
- ``contains``: case-insensitive substring search.
- ``equals``: case-insensitive exact match.
- ``regex``: compiled regular expression (max 500 chars, 1-second timeout to
  prevent catastrophic backtracking from crashing the worker).

Supported action types
----------------------
- ``create_task``: create a new Task linked to the email.
- ``categorize``: set ``InboundEmail.category``.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from typing import TYPE_CHECKING

from app.extensions import db
from app.models.email import EmailRule, EmailRuleLog, InboundEmail, MatchMode
from app.models.task import Task, TaskPriority, TaskSource, TaskStatus

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    """Strip HTML tags and normalise whitespace for plain-text use."""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Maximum regex pattern length (per PLAN.md security requirement)
_MAX_REGEX_LEN = 500
# Timeout for regex execution to prevent catastrophic backtracking
_REGEX_TIMEOUT = 1.0  # seconds

# Mapping: condition "field" → InboundEmail attribute name
_FIELD_MAP = {
    "subject": "subject",
    "body": "body_text",
    "sender": "sender",
    "recipients": "recipients",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_rules_to_email(email: InboundEmail) -> list[EmailRuleLog]:
    """Evaluate all active email rules against an inbound email.

    Commits changes to the session. Should be called within a Flask app context.

    Args:
        email: The inbound email to process.

    Returns:
        List of :class:`EmailRuleLog` records for each rule that matched.
    """
    rules = db.session.scalars(
        db.select(EmailRule)
        .where(EmailRule.is_active.is_(True))
        .order_by(EmailRule.priority, EmailRule.id)
    ).all()

    logs: list[EmailRuleLog] = []
    for rule in rules:
        try:
            matched = _evaluate_conditions(rule, email)
        except Exception:
            logger.exception("Error evaluating rule %r against email %d", rule.name, email.id)
            continue

        if not matched:
            continue

        triggered = []
        for action in rule.actions:
            try:
                result = _execute_action(action, email, rule)
                triggered.append({**action, **result})
            except Exception:
                logger.exception("Error executing action %r for rule %r", action, rule.name)

        log = EmailRuleLog(
            rule_id=rule.id,
            email_id=email.id,
            actions_triggered=triggered,
        )
        db.session.add(log)
        logs.append(log)

    if logs:
        email.processed = True
    db.session.commit()
    return logs


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------


def _evaluate_conditions(rule: EmailRule, email: InboundEmail) -> bool:
    """Return True if the email matches the rule's conditions.

    Args:
        rule: The rule whose conditions to evaluate.
        email: The inbound email.

    Returns:
        True if ``match_mode == "all"`` and all conditions match, or
        if ``match_mode == "any"`` and at least one condition matches.
    """
    results = [_evaluate_single_condition(cond, email) for cond in rule.conditions]
    if rule.match_mode == MatchMode.ALL.value:
        return all(results)
    return any(results)


def _evaluate_single_condition(cond: dict, email: InboundEmail) -> bool:
    """Evaluate one condition dict against an email.

    Args:
        cond: A condition dict with keys ``field``, ``operator``, ``value``.
        email: The inbound email.

    Returns:
        True if the condition is satisfied.
    """
    field = cond.get("field", "")
    operator = cond.get("operator", "")
    value = str(cond.get("value", ""))

    attr_name = _FIELD_MAP.get(field)
    if attr_name is None:
        return False

    target = str(getattr(email, attr_name) or "")
    if field == "body" and not target and email.body_html:
        target = _html_to_text(email.body_html)

    if operator == "contains":
        return value.lower() in target.lower()
    if operator == "equals":
        return target.lower() == value.lower()
    if operator == "regex":
        if len(value) > _MAX_REGEX_LEN:
            logger.warning("Regex pattern exceeds max length %d, skipping", _MAX_REGEX_LEN)
            return False
        return _match_regex_with_timeout(value, target, _REGEX_TIMEOUT)
    return False


def _match_regex_with_timeout(pattern: str, text: str, timeout: float) -> bool:
    """Match a regex pattern against text with a hard timeout.

    Uses a thread pool so the regex runs in a separate thread. If it does not
    complete within ``timeout`` seconds, the match is considered to have failed.

    This protects the worker against catastrophic backtracking.

    Args:
        pattern: The regular expression pattern.
        text: The text to search.
        timeout: Maximum seconds to allow.

    Returns:
        True if the pattern matches; False if no match or timed out.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(re.search, pattern, text, re.IGNORECASE)
        try:
            result = future.result(timeout=timeout)
            return result is not None
        except concurrent.futures.TimeoutError:
            logger.warning("Regex pattern %r timed out after %.1f s", pattern, timeout)
            return False
        except re.error as exc:
            logger.warning("Invalid regex pattern %r: %s", pattern, exc)
            return False


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------


def _execute_action(action: dict, email: InboundEmail, rule: EmailRule) -> dict:
    """Execute a single action and return a result dict to include in the log.

    Args:
        action: An action dict with at least a ``type`` key.
        email: The inbound email.
        rule: The rule that triggered this action.

    Returns:
        A dict of result metadata to include in the rule log.
    """
    action_type = action.get("type", "")

    if action_type == "create_task":
        return _action_create_task(action, email, rule)

    if action_type == "categorize":
        category = str(action.get("category", ""))
        email.category = category or None
        return {"category": category}

    if action_type == "forward_to":
        return _action_forward_to(action, email)

    if action_type == "mark_as_read":
        return _action_mark_as_read(email)

    logger.warning("Unknown action type %r in rule %r", action_type, rule.name)
    return {}


def _action_forward_to(action: dict, email: InboundEmail) -> dict:
    """Forward an email to an external address via Gmail.

    Args:
        action: Action dict with key ``email`` (target address).
        email: The inbound email to forward.

    Returns:
        Dict with ``forwarded_to`` and ``message_id``.
    """
    import base64
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from app.services.gmail import GmailClient

    to_address = str(action.get("email", "")).strip()
    if not to_address:
        logger.warning("forward_to action missing 'email' key")
        return {}

    msg = MIMEMultipart()
    msg["To"] = to_address
    msg["Subject"] = f"Fwd: {email.subject or ''}"

    header = (
        f"---------- Message transféré ----------\n"
        f"De : {email.sender}\n"
        f"Date : {email.received_at.strftime('%d/%m/%Y %H:%M')}\n"
        f"Objet : {email.subject or ''}\n\n"
    )
    msg.attach(MIMEText(header + (email.body_text or ""), "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        client = GmailClient.from_db()
        result = client.send_message(raw)
        return {"forwarded_to": to_address, "message_id": result.get("id")}
    except Exception:
        logger.exception("forward_to failed for email %d", email.id)
        return {"forwarded_to": to_address, "error": "send failed"}


def _action_mark_as_read(email: InboundEmail) -> dict:
    """Mark an email as read in Gmail.

    Args:
        email: The inbound email whose Gmail message ID to use.

    Returns:
        Dict with ``marked_read`` flag.
    """
    from app.services.gmail import GmailClient

    if not email.gmail_message_id:
        return {}
    try:
        client = GmailClient.from_db()
        client.mark_as_read(email.gmail_message_id)
        return {"marked_read": True}
    except Exception:
        logger.exception("mark_as_read failed for email %d", email.id)
        return {"marked_read": False}


def _action_create_task(action: dict, email: InboundEmail, rule: EmailRule) -> dict:
    """Create a Task from an inbound email.

    The task description is populated with the email body, and any attachments
    on the email are linked to the new task.

    Args:
        action: Action dict with optional ``priority`` key.
        email: The source email.
        rule: The triggering rule.

    Returns:
        Dict with ``task_id`` of the created task.
    """
    priority_str = action.get("priority", TaskPriority.NORMAL.value)
    if priority_str not in {e.value for e in TaskPriority}:
        priority_str = TaskPriority.NORMAL.value

    task = Task(
        title=f"Email : {email.subject[:180]}" if email.subject else "Email sans sujet",
        description=(
            email.body_text or _html_to_text(email.body_html or "") or "Email sans contenu texte."
        ),
        source=TaskSource.EMAIL.value,
        source_email_id=email.id,
        priority=priority_str,
        status=TaskStatus.OPEN.value,
        created_by_id=rule.created_by_id,
    )

    # Link attachments from the email to the task
    if email.documents:
        task.documents = list(email.documents)

    db.session.add(task)
    db.session.flush()  # get task.id before further ops
    email.generated_task_id = task.id
    return {"task_id": task.id}
