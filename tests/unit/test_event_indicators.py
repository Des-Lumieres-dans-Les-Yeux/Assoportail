from datetime import UTC, date, datetime, time

import pytest

from app.extensions import db as _db
from app.models.event import (
    Event,
    EventSlot,
    EventVolunteer,
    SlotAvailability,
    SlotAvailabilityStatus,
    VolunteerSlotAvailability,
)
from app.models.user import User


@pytest.fixture
def sample_event(app, bureau_user):
    with app.app_context():
        event = Event(
            title="Test Event",
            event_date=datetime(2026, 5, 10, 9, 0, tzinfo=UTC),
            created_by_id=bureau_user.id,
        )
        _db.session.add(event)
        _db.session.commit()
        return event.id


def test_unique_participants_count(app, sample_event, member_user, bureau_user):
    with app.app_context():
        event = _db.session.get(Event, sample_event)
        member = _db.session.get(User, member_user.id)

        # 1. Add to attendees
        event.attendees.append(member)
        _db.session.commit()
        assert event.unique_participants_count == 1

        # 2. Add another member via slot (not in attendees)
        bureau = _db.session.get(User, bureau_user.id)
        slot = EventSlot(
            event_id=event.id,
            slot_date=date(2026, 5, 10),
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        _db.session.add(slot)
        _db.session.commit()

        avail = SlotAvailability(
            slot_id=slot.id, user_id=bureau.id, status=SlotAvailabilityStatus.PRESENT
        )
        _db.session.add(avail)
        _db.session.commit()

        # member (attendee) + bureau (slot) = 2
        assert event.unique_participants_count == 2

        # 3. Add a volunteer
        volunteer = EventVolunteer(
            event_id=event.id,
            name="Vol",
            email="vol@test.com",
            personal_token="tok1",
            confirmed=True,
        )
        _db.session.add(volunteer)
        _db.session.commit()

        # Should be 3 now (member + bureau + confirmed volunteer)
        assert event.unique_participants_count == 3

        # 4. Volunteer not confirmed shouldn't count
        volunteer2 = EventVolunteer(
            event_id=event.id,
            name="Vol2",
            email="vol2@test.com",
            personal_token="tok2",
            confirmed=False,
        )
        _db.session.add(volunteer2)
        _db.session.commit()
        assert event.unique_participants_count == 3


def test_unique_confirmed_count(app, sample_event, member_user, bureau_user):
    with app.app_context():
        event = _db.session.get(Event, sample_event)
        member = _db.session.get(User, member_user.id)
        bureau = _db.session.get(User, bureau_user.id)

        slot1 = EventSlot(
            event_id=event.id,
            slot_date=date(2026, 5, 10),
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        slot2 = EventSlot(
            event_id=event.id,
            slot_date=date(2026, 5, 10),
            start_time=time(13, 0),
            end_time=time(16, 0),
        )
        _db.session.add_all([slot1, slot2])
        _db.session.commit()

        # Member present in slot1
        avail1 = SlotAvailability(
            slot_id=slot1.id, user_id=member.id, status=SlotAvailabilityStatus.PRESENT
        )
        _db.session.add(avail1)
        _db.session.commit()
        assert event.unique_confirmed_count == 1

        # Same member present in slot2 -> still 1 unique confirmed
        avail2 = SlotAvailability(
            slot_id=slot2.id, user_id=member.id, status=SlotAvailabilityStatus.PRESENT
        )
        _db.session.add(avail2)
        _db.session.commit()
        assert event.unique_confirmed_count == 1

        # Another person (bureau) maybe in slot1 -> not confirmed (only 'present' counts)
        avail3 = SlotAvailability(
            slot_id=slot1.id, user_id=bureau.id, status=SlotAvailabilityStatus.MAYBE
        )
        _db.session.add(avail3)
        _db.session.commit()
        assert event.unique_confirmed_count == 1

        # Bureau present in slot2 -> 2 unique confirmed
        avail3.status = SlotAvailabilityStatus.PRESENT
        _db.session.commit()
        assert event.unique_confirmed_count == 2

        # Add confirmed volunteer present in slot1
        volunteer = EventVolunteer(
            event_id=event.id,
            name="Vol",
            email="vol@test.com",
            personal_token="tok1",
            confirmed=True,
        )
        _db.session.add(volunteer)
        _db.session.commit()

        va = VolunteerSlotAvailability(
            slot_id=slot1.id,
            volunteer_id=volunteer.id,
            status=SlotAvailabilityStatus.PRESENT,
        )
        _db.session.add(va)
        _db.session.commit()
        assert event.unique_confirmed_count == 3
