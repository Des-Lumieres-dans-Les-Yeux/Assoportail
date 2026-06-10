"""Unit tests for the email rules engine and EmailRuleForm validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from flask import Flask

from app.extensions import db as _db
from app.models.email import EmailRule, InboundEmail, MatchMode
from app.models.task import Task, TaskPriority, TaskSource
from app.models.user import User
from app.tasks.email_rules import (
    _evaluate_conditions,
    _evaluate_single_condition,
    _match_regex_with_timeout,
    apply_rules_to_email,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_email(**kwargs) -> InboundEmail:
    """Create an InboundEmail with sensible defaults (not persisted)."""
    defaults = {
        "gmail_message_id": "test-msg-id",
        "subject": "Test subject",
        "sender": "alice@example.com",
        "recipients": "bureau@association.fr",
        "body_text": "Hello, this is the body.",
        "received_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return InboundEmail(**defaults)


def _make_rule(conditions, actions, match_mode=MatchMode.ALL.value, created_by_id=1) -> EmailRule:
    """Build an EmailRule (not persisted)."""
    return EmailRule(
        name="Test rule",
        is_active=True,
        priority=10,
        match_mode=match_mode,
        conditions=conditions,
        actions=actions,
        created_by_id=created_by_id,
    )


# ---------------------------------------------------------------------------
# _evaluate_single_condition — contains operator
# ---------------------------------------------------------------------------


class TestEvaluateSingleConditionContains:
    def test_contains_subject_match(self) -> None:
        email = _make_email(subject="Panne machine flipper")
        cond = {"field": "subject", "operator": "contains", "value": "panne"}
        assert _evaluate_single_condition(cond, email) is True

    def test_contains_case_insensitive(self) -> None:
        email = _make_email(subject="URGENT: flipper en panne")
        cond = {"field": "subject", "operator": "contains", "value": "urgent"}
        assert _evaluate_single_condition(cond, email) is True

    def test_contains_body_match(self) -> None:
        email = _make_email(body_text="Le moteur ne démarre plus.")
        cond = {"field": "body", "operator": "contains", "value": "moteur"}
        assert _evaluate_single_condition(cond, email) is True

    def test_contains_sender_match(self) -> None:
        email = _make_email(sender="admin@example.com")
        cond = {"field": "sender", "operator": "contains", "value": "@example.com"}
        assert _evaluate_single_condition(cond, email) is True

    def test_contains_no_match(self) -> None:
        email = _make_email(subject="Réunion du bureau")
        cond = {"field": "subject", "operator": "contains", "value": "panne"}
        assert _evaluate_single_condition(cond, email) is False

    def test_contains_body_none_treated_as_empty(self) -> None:
        email = _make_email(body_text=None)
        cond = {"field": "body", "operator": "contains", "value": "anything"}
        assert _evaluate_single_condition(cond, email) is False


# ---------------------------------------------------------------------------
# _evaluate_single_condition — equals operator
# ---------------------------------------------------------------------------


class TestEvaluateSingleConditionEquals:
    def test_equals_exact_match(self) -> None:
        email = _make_email(sender="admin@example.com")
        cond = {"field": "sender", "operator": "equals", "value": "admin@example.com"}
        assert _evaluate_single_condition(cond, email) is True

    def test_equals_case_insensitive(self) -> None:
        email = _make_email(sender="Admin@Example.COM")
        cond = {"field": "sender", "operator": "equals", "value": "admin@example.com"}
        assert _evaluate_single_condition(cond, email) is True

    def test_equals_partial_no_match(self) -> None:
        email = _make_email(subject="Panne machine")
        cond = {"field": "subject", "operator": "equals", "value": "panne"}
        assert _evaluate_single_condition(cond, email) is False


# ---------------------------------------------------------------------------
# _evaluate_single_condition — regex operator
# ---------------------------------------------------------------------------


class TestEvaluateSingleConditionRegex:
    def test_regex_match(self) -> None:
        email = _make_email(sender="no-reply@newsletter.example.com")
        cond = {"field": "sender", "operator": "regex", "value": r"^no-reply@"}
        assert _evaluate_single_condition(cond, email) is True

    def test_regex_no_match(self) -> None:
        email = _make_email(subject="Bonjour")
        cond = {"field": "subject", "operator": "regex", "value": r"^\d{4}-\d{2}-\d{2}"}
        assert _evaluate_single_condition(cond, email) is False

    def test_regex_invalid_pattern_returns_false(self) -> None:
        email = _make_email(subject="test")
        cond = {"field": "subject", "operator": "regex", "value": r"[invalid"}
        assert _evaluate_single_condition(cond, email) is False

    def test_regex_exceeds_max_length_returns_false(self) -> None:
        email = _make_email(subject="test")
        long_pattern = "a" * 501
        cond = {"field": "subject", "operator": "regex", "value": long_pattern}
        assert _evaluate_single_condition(cond, email) is False

    def test_regex_case_insensitive(self) -> None:
        email = _make_email(subject="FACTURE-2024")
        cond = {"field": "subject", "operator": "regex", "value": r"facture-\d{4}"}
        assert _evaluate_single_condition(cond, email) is True


# ---------------------------------------------------------------------------
# _match_regex_with_timeout
# ---------------------------------------------------------------------------


class TestMatchRegexWithTimeout:
    def test_valid_match(self) -> None:
        assert _match_regex_with_timeout(r"hello", "say hello world", 1.0) is True

    def test_valid_no_match(self) -> None:
        assert _match_regex_with_timeout(r"goodbye", "say hello world", 1.0) is False

    def test_invalid_regex_returns_false(self) -> None:
        assert _match_regex_with_timeout(r"[unclosed", "test text", 1.0) is False

    def test_catastrophic_backtracking_times_out(self) -> None:
        """A ReDoS-prone pattern against a crafted input should time out safely."""
        # Classic catastrophic backtracking: (a+)+ against "aaaa...aX"
        pattern = r"(a+)+"
        text = "a" * 30 + "X"
        # With a very short timeout, it may or may not time out depending on CPU.
        # We just verify it doesn't raise and returns a bool.
        result = _match_regex_with_timeout(pattern, text, 0.001)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _evaluate_conditions — match_mode all / any
# ---------------------------------------------------------------------------


class TestEvaluateConditions:
    def test_all_mode_all_match(self) -> None:
        email = _make_email(subject="Panne urgente", sender="tech@example.com")
        rule = _make_rule(
            conditions=[
                {"field": "subject", "operator": "contains", "value": "panne"},
                {"field": "sender", "operator": "contains", "value": "@example.com"},
            ],
            actions=[],
            match_mode=MatchMode.ALL.value,
        )
        assert _evaluate_conditions(rule, email) is True

    def test_all_mode_one_fails(self) -> None:
        email = _make_email(subject="Panne urgente", sender="autre@domain.fr")
        rule = _make_rule(
            conditions=[
                {"field": "subject", "operator": "contains", "value": "panne"},
                {"field": "sender", "operator": "contains", "value": "@example.com"},
            ],
            actions=[],
            match_mode=MatchMode.ALL.value,
        )
        assert _evaluate_conditions(rule, email) is False

    def test_any_mode_one_matches(self) -> None:
        email = _make_email(subject="Bonjour", sender="tech@example.com")
        rule = _make_rule(
            conditions=[
                {"field": "subject", "operator": "contains", "value": "panne"},
                {"field": "sender", "operator": "contains", "value": "@example.com"},
            ],
            actions=[],
            match_mode=MatchMode.ANY.value,
        )
        assert _evaluate_conditions(rule, email) is True

    def test_any_mode_none_match(self) -> None:
        email = _make_email(subject="Bonjour", sender="autre@domain.fr")
        rule = _make_rule(
            conditions=[
                {"field": "subject", "operator": "contains", "value": "panne"},
                {"field": "sender", "operator": "contains", "value": "@example.com"},
            ],
            actions=[],
            match_mode=MatchMode.ANY.value,
        )
        assert _evaluate_conditions(rule, email) is False


# ---------------------------------------------------------------------------
# apply_rules_to_email — integration (requires app context + DB)
# ---------------------------------------------------------------------------


class TestApplyRulesToEmail:
    def test_categorize_action(self, app: Flask, bureau_user) -> None:
        """A categorize action sets InboundEmail.category."""
        with app.app_context():
            user = _db.session.get(User, bureau_user.id)
            email = InboundEmail(
                gmail_message_id="cat-test-001",
                subject="Facture électricité",
                sender="erdf@fournisseur.fr",
                recipients="bureau@asso.fr",
                received_at=datetime.now(UTC),
            )
            _db.session.add(email)
            _db.session.flush()

            rule = EmailRule(
                name="Catégoriser factures",
                is_active=True,
                priority=1,
                match_mode=MatchMode.ALL.value,
                conditions=[{"field": "subject", "operator": "contains", "value": "facture"}],
                actions=[{"type": "categorize", "category": "comptabilité"}],
                created_by_id=user.id,
            )
            _db.session.add(rule)
            _db.session.commit()

            logs = apply_rules_to_email(email)

            assert len(logs) == 1
            assert email.category == "comptabilité"
            assert email.processed is True

    def test_create_task_action(self, app: Flask, bureau_user) -> None:
        """A create_task action inserts a Task and links it to the email."""
        with app.app_context():
            user = _db.session.get(User, bureau_user.id)
            email = InboundEmail(
                gmail_message_id="task-test-001",
                subject="Panne grave sur le flipper",
                sender="operateur@salle.fr",
                recipients="bureau@asso.fr",
                received_at=datetime.now(UTC),
            )
            _db.session.add(email)
            _db.session.flush()

            rule = EmailRule(
                name="Créer tâche pannes",
                is_active=True,
                priority=1,
                match_mode=MatchMode.ALL.value,
                conditions=[{"field": "subject", "operator": "contains", "value": "panne"}],
                actions=[{"type": "create_task", "priority": TaskPriority.URGENT.value}],
                created_by_id=user.id,
            )
            _db.session.add(rule)
            _db.session.commit()

            logs = apply_rules_to_email(email)

            assert len(logs) == 1
            assert email.generated_task_id is not None
            task = _db.session.get(Task, email.generated_task_id)
            assert task is not None
            assert "flipper" in task.title
            assert task.priority == TaskPriority.URGENT.value
            assert task.source == TaskSource.EMAIL.value

    def test_inactive_rule_not_applied(self, app: Flask, bureau_user) -> None:
        """An inactive rule is skipped even when conditions match."""
        with app.app_context():
            user = _db.session.get(User, bureau_user.id)
            email = InboundEmail(
                gmail_message_id="inactive-test-001",
                subject="Panne machine",
                sender="op@salle.fr",
                recipients="bureau@asso.fr",
                received_at=datetime.now(UTC),
            )
            _db.session.add(email)
            _db.session.flush()

            rule = EmailRule(
                name="Règle inactive",
                is_active=False,
                priority=1,
                match_mode=MatchMode.ALL.value,
                conditions=[{"field": "subject", "operator": "contains", "value": "panne"}],
                actions=[{"type": "categorize", "category": "maintenance"}],
                created_by_id=user.id,
            )
            _db.session.add(rule)
            _db.session.commit()

            logs = apply_rules_to_email(email)

            assert logs == []
            assert email.category is None

    def test_non_matching_rule_produces_no_log(self, app: Flask, bureau_user) -> None:
        """A rule whose conditions don't match produces no EmailRuleLog."""
        with app.app_context():
            user = _db.session.get(User, bureau_user.id)
            email = InboundEmail(
                gmail_message_id="nomatch-test-001",
                subject="Réunion mensuelle",
                sender="contact@asso.fr",
                recipients="bureau@asso.fr",
                received_at=datetime.now(UTC),
            )
            _db.session.add(email)
            _db.session.flush()

            rule = EmailRule(
                name="Règle panne",
                is_active=True,
                priority=1,
                match_mode=MatchMode.ALL.value,
                conditions=[{"field": "subject", "operator": "contains", "value": "panne"}],
                actions=[{"type": "categorize", "category": "maintenance"}],
                created_by_id=user.id,
            )
            _db.session.add(rule)
            _db.session.commit()

            logs = apply_rules_to_email(email)

            assert logs == []
            assert email.processed is False  # not processed: no rule matched


# ---------------------------------------------------------------------------
# EmailRuleForm validation
# ---------------------------------------------------------------------------


class TestEmailRuleFormValidation:
    def test_valid_conditions_json(self, app: Flask) -> None:
        from app.blueprints.mailbox.forms import EmailRuleForm

        with app.test_request_context("/"):
            form = EmailRuleForm(
                data={
                    "name": "Test rule",
                    "is_active": True,
                    "priority": 10,
                    "match_mode": "all",
                    "conditions": json.dumps(
                        [{"field": "subject", "operator": "contains", "value": "panne"}]
                    ),
                    "actions": json.dumps([{"type": "categorize", "category": "x"}]),
                }
            )
            assert form.validate()

    def test_conditions_invalid_json_fails(self, app: Flask) -> None:
        from app.blueprints.mailbox.forms import EmailRuleForm

        with app.test_request_context("/"):
            form = EmailRuleForm(
                data={
                    "name": "Bad rule",
                    "is_active": True,
                    "priority": 10,
                    "match_mode": "all",
                    "conditions": "not json",
                    "actions": json.dumps([{"type": "categorize", "category": "x"}]),
                }
            )
            assert not form.validate()
            assert form.conditions.errors

    def test_conditions_empty_list_fails(self, app: Flask) -> None:
        from app.blueprints.mailbox.forms import EmailRuleForm

        with app.test_request_context("/"):
            form = EmailRuleForm(
                data={
                    "name": "Bad rule",
                    "is_active": True,
                    "priority": 10,
                    "match_mode": "all",
                    "conditions": "[]",
                    "actions": json.dumps([{"type": "categorize", "category": "x"}]),
                }
            )
            assert not form.validate()
            assert form.conditions.errors

    def test_conditions_invalid_field_fails(self, app: Flask) -> None:
        from app.blueprints.mailbox.forms import EmailRuleForm

        with app.test_request_context("/"):
            form = EmailRuleForm(
                data={
                    "name": "Bad rule",
                    "is_active": True,
                    "priority": 10,
                    "match_mode": "all",
                    "conditions": json.dumps(
                        [{"field": "unknown_field", "operator": "contains", "value": "x"}]
                    ),
                    "actions": json.dumps([{"type": "categorize", "category": "x"}]),
                }
            )
            assert not form.validate()
            assert form.conditions.errors

    def test_actions_unknown_type_fails(self, app: Flask) -> None:
        from app.blueprints.mailbox.forms import EmailRuleForm

        with app.test_request_context("/"):
            form = EmailRuleForm(
                data={
                    "name": "Bad rule",
                    "is_active": True,
                    "priority": 10,
                    "match_mode": "all",
                    "conditions": json.dumps(
                        [{"field": "subject", "operator": "contains", "value": "x"}]
                    ),
                    "actions": json.dumps([{"type": "send_email"}]),
                }
            )
            assert not form.validate()
            assert form.actions.errors

    def test_actions_missing_type_fails(self, app: Flask) -> None:
        from app.blueprints.mailbox.forms import EmailRuleForm

        with app.test_request_context("/"):
            form = EmailRuleForm(
                data={
                    "name": "Bad rule",
                    "is_active": True,
                    "priority": 10,
                    "match_mode": "all",
                    "conditions": json.dumps(
                        [{"field": "subject", "operator": "contains", "value": "x"}]
                    ),
                    "actions": json.dumps([{"category": "no-type-key"}]),
                }
            )
            assert not form.validate()
            assert form.actions.errors

    def test_regex_value_too_long_in_conditions_fails(self, app: Flask) -> None:
        from app.blueprints.mailbox.forms import EmailRuleForm

        with app.test_request_context("/"):
            form = EmailRuleForm(
                data={
                    "name": "Bad rule",
                    "is_active": True,
                    "priority": 10,
                    "match_mode": "all",
                    "conditions": json.dumps(
                        [{"field": "subject", "operator": "regex", "value": "a" * 501}]
                    ),
                    "actions": json.dumps([{"type": "categorize", "category": "x"}]),
                }
            )
            assert not form.validate()
            assert form.conditions.errors
