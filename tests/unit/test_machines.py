"""Unit tests for machine management — CRUD, installations, maintenance."""

from datetime import date, timedelta
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.center import Center, CenterStatus
from app.models.machine import Machine, MachineInstallation, MachineStatus, MaintenanceRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_machine(app: Flask, *, model: str = "Medieval Madness", status: str = "stock") -> int:
    with app.app_context():
        m = Machine(model=model, manufacturer="Williams", status=status)
        _db.session.add(m)
        _db.session.commit()
        return m.id


def _make_center(app: Flask, *, name: str = "CHU Nord", city: str = "Paris") -> int:
    with app.app_context():
        c = Center(name=name, city=city, zip_code="75001", status=CenterStatus.ACTIVE)
        _db.session.add(c)
        _db.session.commit()
        return c.id


def _install(app: Flask, machine_id: int, center_id: int) -> int:
    with app.app_context():
        inst = MachineInstallation(
            machine_id=machine_id,
            center_id=center_id,
            installed_at=date.today() - timedelta(days=10),
        )
        machine = _db.session.get(Machine, machine_id)
        machine.status = MachineStatus.INSTALLED
        _db.session.add(inst)
        _db.session.commit()
        return inst.id


# ---------------------------------------------------------------------------
# Machine CRUD
# ---------------------------------------------------------------------------


class TestMachineCreate:
    def test_bureau_can_create_machine(self, app: Flask, bureau_client: FlaskClient) -> None:
        response = bureau_client.post(
            "/machines/new",
            data={
                "model": "Twilight Zone",
                "manufacturer": "Williams",
                "serial_number": "TZ-001",
                "year": "1993",
                "status": "stock",
                "notes": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            m = _db.session.execute(
                _db.select(Machine).filter_by(serial_number="TZ-001")
            ).scalar_one_or_none()
            assert m is not None
            assert m.model == "Twilight Zone"
            assert m.status == MachineStatus.STOCK

    def test_member_cannot_create_machine(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/machines/new")
        assert response.status_code == 403

    def test_duplicate_serial_number_rejected(self, app: Flask, bureau_client: FlaskClient) -> None:
        _make_machine(app, model="Attack From Mars")
        with app.app_context():
            m = _db.session.execute(_db.select(Machine)).scalar_one()
            serial = "AFM-001"
            m.serial_number = serial
            _db.session.commit()

        response = bureau_client.post(
            "/machines/new",
            data={
                "model": "Another Machine",
                "manufacturer": "Williams",
                "serial_number": "AFM-001",
                "year": "",
                "status": "stock",
                "notes": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "déjà enregistré" in response.data.decode()


class TestMachineList:
    def test_member_can_view_machine_list(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/machines/")
        assert response.status_code == 200

    def test_machine_list_shows_machine_name(self, app: Flask, auth_client: FlaskClient) -> None:
        _make_machine(app, model="Addams Family")
        response = auth_client.get("/machines/")
        assert "Addams Family" in response.data.decode()


# ---------------------------------------------------------------------------
# Installation lifecycle
# ---------------------------------------------------------------------------


class TestMachineInstallation:
    def test_bureau_can_install_machine_at_center(
        self, app: Flask, bureau_client: FlaskClient
    ) -> None:
        mid = _make_machine(app)
        cid = _make_center(app)

        response = bureau_client.post(
            f"/machines/{mid}/install",
            data={
                "center_id": str(cid),
                "installed_at": date.today().strftime("%Y-%m-%d"),
                "notes": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            inst = _db.session.execute(
                _db.select(MachineInstallation).where(
                    MachineInstallation.machine_id == mid,
                    MachineInstallation.removed_at.is_(None),
                )
            ).scalar_one_or_none()
            assert inst is not None
            assert inst.center_id == cid
            machine = _db.session.get(Machine, mid)
            assert machine.status == MachineStatus.INSTALLED

    def test_cannot_double_install_machine(self, app: Flask, bureau_client: FlaskClient) -> None:
        mid = _make_machine(app)
        cid = _make_center(app)
        cid2 = _make_center(app, name="Clinique Sud")
        _install(app, mid, cid)

        response = bureau_client.post(
            f"/machines/{mid}/install",
            data={
                "center_id": str(cid2),
                "installed_at": date.today().strftime("%Y-%m-%d"),
                "notes": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        # The new flow stores the conflict in session and shows a confirmation modal
        # instead of a flash message, so we check for the modal trigger element.
        assert "confirmMoveModal" in response.data.decode()

        with app.app_context():
            count = _db.session.execute(
                _db.select(_db.func.count(MachineInstallation.id)).where(
                    MachineInstallation.machine_id == mid,
                    MachineInstallation.removed_at.is_(None),
                )
            ).scalar()
            assert count == 1

    def test_bureau_can_remove_installation(self, app: Flask, bureau_client: FlaskClient) -> None:
        mid = _make_machine(app)
        cid = _make_center(app)
        inst_id = _install(app, mid, cid)
        removal_date = date.today()

        response = bureau_client.post(
            f"/machines/{mid}/installations/{inst_id}/remove",
            data={"removed_at": removal_date.strftime("%Y-%m-%d")},
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            inst = _db.session.get(MachineInstallation, inst_id)
            assert inst.removed_at == removal_date
            machine = _db.session.get(Machine, mid)
            assert machine.status == MachineStatus.STOCK

    def test_machine_can_be_reinstalled_after_removal(
        self, app: Flask, bureau_client: FlaskClient
    ) -> None:
        """After removing a machine it can be installed at another center."""
        mid = _make_machine(app)
        cid = _make_center(app)
        cid2 = _make_center(app, name="EHPAD Est")
        inst_id = _install(app, mid, cid)

        # Remove from first center
        bureau_client.post(
            f"/machines/{mid}/installations/{inst_id}/remove",
            data={"removed_at": date.today().strftime("%Y-%m-%d")},
        )

        # Install at second center
        response = bureau_client.post(
            f"/machines/{mid}/install",
            data={
                "center_id": str(cid2),
                "installed_at": date.today().strftime("%Y-%m-%d"),
                "notes": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            active = _db.session.execute(
                _db.select(MachineInstallation).where(
                    MachineInstallation.machine_id == mid,
                    MachineInstallation.removed_at.is_(None),
                )
            ).scalar_one_or_none()
            assert active is not None
            assert active.center_id == cid2


# ---------------------------------------------------------------------------
# Maintenance cost tracking
# ---------------------------------------------------------------------------


class TestMaintenanceRecords:
    def test_bureau_can_add_maintenance_record(
        self, app: Flask, bureau_client: FlaskClient
    ) -> None:
        mid = _make_machine(app)

        response = bureau_client.post(
            f"/machines/{mid}/maintenance",
            data={
                "date": date.today().strftime("%Y-%m-%d"),
                "description": "Remplacement du flipper gauche",
                "cost": "45.00",
                "maintainer_name": "Jean Dupont",
                "source_task_id": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            record = _db.session.execute(
                _db.select(MaintenanceRecord).where(MaintenanceRecord.machine_id == mid)
            ).scalar_one_or_none()
            assert record is not None
            assert record.cost == Decimal("45.00")
            assert record.description == "Remplacement du flipper gauche"

    def test_maintenance_cost_totals_correctly(
        self, app: Flask, bureau_client: FlaskClient
    ) -> None:
        """Multiple maintenance records sum to the correct total."""
        mid = _make_machine(app)

        for cost in ("20.00", "35.50", "10.00"):
            bureau_client.post(
                f"/machines/{mid}/maintenance",
                data={
                    "date": date.today().strftime("%Y-%m-%d"),
                    "description": "Réparation",
                    "cost": cost,
                    "maintainer_name": "Intervenant",
                    "source_task_id": "",
                },
            )

        with app.app_context():
            records = _db.session.scalars(
                _db.select(MaintenanceRecord).where(MaintenanceRecord.machine_id == mid)
            ).all()
            total = sum(r.cost for r in records if r.cost)
            assert total == Decimal("65.50")

    def test_member_cannot_add_maintenance(self, app: Flask, auth_client: FlaskClient) -> None:
        mid = _make_machine(app)
        response = auth_client.post(
            f"/machines/{mid}/maintenance",
            data={
                "date": date.today().strftime("%Y-%m-%d"),
                "description": "test",
                "cost": "",
                "maintainer_name": "test",
                "source_task_id": "",
            },
        )
        assert response.status_code == 403
