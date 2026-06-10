"""CSV import/export utilities for machines, centers, and members."""

from __future__ import annotations

import csv
import io
from typing import Any

# ---------------------------------------------------------------------------
# Machine CSV
# ---------------------------------------------------------------------------

MACHINE_EXPORT_FIELDS = [
    "internal_number",
    "model",
    "manufacturer",
    "serial_number",
    "year",
    "status",
    "notes",
]

MACHINE_IMPORT_FIELDS = MACHINE_EXPORT_FIELDS  # same columns expected on import


def export_machines_csv(machines: list[Any]) -> str:
    """Serialize a list of Machine objects to a CSV string."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MACHINE_EXPORT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for m in machines:
        writer.writerow(
            {
                "internal_number": m.internal_number or "",
                "model": m.model,
                "manufacturer": m.manufacturer,
                "serial_number": m.serial_number or "",
                "year": m.year or "",
                "status": m.status.value if hasattr(m.status, "value") else m.status,
                "notes": m.notes or "",
            }
        )
    return buf.getvalue()


def parse_machines_csv(file_data: bytes) -> tuple[list[dict], list[str]]:
    """Parse CSV bytes into a list of machine dicts and a list of error messages.

    Returns (rows, errors). rows are validated dicts ready for model creation.
    errors is a list of human-readable error strings.
    """
    errors: list[str] = []
    rows: list[dict] = []

    try:
        text = file_data.decode("utf-8-sig")  # handle BOM from Excel
    except UnicodeDecodeError:
        return [], ["Impossible de décoder le fichier. Utilisez l'encodage UTF-8."]

    if not text.strip():
        return [], ["Fichier CSV vide."]

    # Delimiter detection
    try:
        dialect = csv.Sniffer().sniff(text[:1024], delimiters=",;\t")
    except csv.Error:
        dialect = "excel"  # fallback to default

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        return [], ["Fichier CSV sans en-têtes."]

    missing = [f for f in ("model", "manufacturer") if f not in reader.fieldnames]
    if missing:
        return [], [f"Colonnes obligatoires manquantes : {', '.join(missing)}."]

    for i, row in enumerate(reader, start=2):
        line_errors = []

        model = row.get("model", "").strip()
        manufacturer = row.get("manufacturer", "").strip()
        if not model:
            line_errors.append("colonne « model » vide")
        if not manufacturer:
            line_errors.append("colonne « manufacturer » vide")

        year_raw = row.get("year", "").strip()
        year = None
        if year_raw:
            if year_raw.isdigit() and 1900 <= int(year_raw) <= 2100:
                year = int(year_raw)
            else:
                line_errors.append(f"année invalide : {year_raw!r}")

        if line_errors:
            errors.append(f"Ligne {i} : {'; '.join(line_errors)}.")
            continue

        rows.append(
            {
                "internal_number": row.get("internal_number", "").strip() or None,
                "model": model,
                "manufacturer": manufacturer,
                "serial_number": row.get("serial_number", "").strip() or None,
                "year": year,
                "status": row.get("status", "").strip() or "stock",
                "notes": row.get("notes", "").strip() or None,
            }
        )

    return rows, errors


# ---------------------------------------------------------------------------
# Center CSV
# ---------------------------------------------------------------------------

CENTER_EXPORT_FIELDS = [
    "name",
    "address",
    "city",
    "zip_code",
    "status",
    "notes",
    "contact_name",
    "contact_role",
    "contact_email",
    "contact_phone",
]

CENTER_IMPORT_FIELDS = CENTER_EXPORT_FIELDS


def export_centers_csv(centers: list[Any]) -> str:
    """Serialize a list of Center objects (with contacts) to a CSV string.

    Each center may produce multiple rows if it has multiple contacts.
    Centers with no contacts produce one row with empty contact columns.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CENTER_EXPORT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for c in centers:
        base = {
            "name": c.name,
            "address": c.address or "",
            "city": c.city,
            "zip_code": c.zip_code,
            "status": c.status.value if hasattr(c.status, "value") else c.status,
            "notes": c.notes or "",
        }
        if c.contacts:
            for contact in c.contacts:
                writer.writerow(
                    {
                        **base,
                        "contact_name": contact.name,
                        "contact_role": contact.role or "",
                        "contact_email": contact.email or "",
                        "contact_phone": contact.phone or "",
                    }
                )
        else:
            writer.writerow(
                {
                    **base,
                    "contact_name": "",
                    "contact_role": "",
                    "contact_email": "",
                    "contact_phone": "",
                }
            )
    return buf.getvalue()


def parse_centers_csv(file_data: bytes) -> tuple[list[dict], list[str]]:
    """Parse CSV bytes into center dicts grouped by name.

    Multiple rows with the same name are merged into one center with multiple contacts.
    Returns (centers, errors) where each center dict has a "contacts" key (list of dicts).
    """
    errors: list[str] = []

    try:
        text = file_data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["Impossible de décoder le fichier. Utilisez l'encodage UTF-8."]

    if not text.strip():
        return [], ["Fichier CSV vide."]

    # Delimiter detection
    try:
        dialect = csv.Sniffer().sniff(text[:1024], delimiters=",;\t")
    except csv.Error:
        dialect = "excel"

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        return [], ["Fichier CSV sans en-têtes."]

    missing = [f for f in ("name", "city", "zip_code") if f not in reader.fieldnames]
    if missing:
        return [], [f"Colonnes obligatoires manquantes : {', '.join(missing)}."]

    # Group rows by center name (case-insensitive)
    centers_by_name: dict[str, dict] = {}

    for i, row in enumerate(reader, start=2):
        name = row.get("name", "").strip()
        city = row.get("city", "").strip()
        zip_code = row.get("zip_code", "").strip()

        if not name:
            errors.append(f"Ligne {i} : colonne « name » vide.")
            continue
        if not city:
            errors.append(f"Ligne {i} : colonne « city » vide.")
            continue
        if not zip_code:
            errors.append(f"Ligne {i} : colonne « zip_code » vide.")
            continue

        key = name.lower()
        if key not in centers_by_name:
            centers_by_name[key] = {
                "name": name,
                "address": row.get("address", "").strip() or None,
                "city": city,
                "zip_code": zip_code,
                "status": row.get("status", "").strip() or "prospect",
                "notes": row.get("notes", "").strip() or None,
                "contacts": [],
            }

        contact_name = row.get("contact_name", "").strip()
        if contact_name:
            centers_by_name[key]["contacts"].append(
                {
                    "name": contact_name,
                    "role": row.get("contact_role", "").strip() or None,
                    "email": row.get("contact_email", "").strip().lower() or None,
                    "phone": row.get("contact_phone", "").strip() or None,
                }
            )

    return list(centers_by_name.values()), errors


# ---------------------------------------------------------------------------
# Member CSV
# ---------------------------------------------------------------------------

MEMBER_EXPORT_FIELDS = [
    "first_name",
    "last_name",
    "email",
    "phone",
    "address",
    "gender",
    "role",
]

MEMBER_IMPORT_FIELDS = MEMBER_EXPORT_FIELDS


def export_members_csv(members: list[Any]) -> str:
    """Serialize a list of User objects to a CSV string."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MEMBER_EXPORT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for m in members:
        writer.writerow(
            {
                "first_name": m.first_name,
                "last_name": m.last_name,
                "email": m.email,
                "phone": m.phone or "",
                "address": m.address or "",
                "gender": m.gender.value if hasattr(m.gender, "value") else (m.gender or ""),
                "role": m.role.value if hasattr(m.role, "value") else m.role,
            }
        )
    return buf.getvalue()


def parse_members_csv(file_data: bytes) -> tuple[list[dict], list[str]]:
    """Parse CSV bytes into a list of member dicts and a list of error messages.

    Returns (rows, errors). rows are validated dicts ready for model creation.
    """
    errors: list[str] = []
    rows: list[dict] = []

    try:
        text = file_data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["Impossible de décoder le fichier. Utilisez l'encodage UTF-8."]

    if not text.strip():
        return [], ["Fichier CSV vide."]

    try:
        dialect = csv.Sniffer().sniff(text[:1024], delimiters=",;\t")
    except csv.Error:
        dialect = "excel"

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        return [], ["Fichier CSV sans en-têtes."]

    missing = [f for f in ("first_name", "last_name", "email") if f not in reader.fieldnames]
    if missing:
        return [], [f"Colonnes obligatoires manquantes : {', '.join(missing)}."]

    for i, row in enumerate(reader, start=2):
        line_errors = []

        first_name = row.get("first_name", "").strip()
        last_name = row.get("last_name", "").strip()
        email = row.get("email", "").strip().lower()

        if not first_name:
            line_errors.append("colonne « first_name » vide")
        if not last_name:
            line_errors.append("colonne « last_name » vide")
        if not email:
            line_errors.append("colonne « email » vide")

        if line_errors:
            errors.append(f"Ligne {i} : {'; '.join(line_errors)}.")
            continue

        role = row.get("role", "").strip().lower() or "member"
        if role not in ("member", "bureau"):
            errors.append(f"Ligne {i} : rôle invalide « {role} » (member ou bureau attendu).")
            continue

        rows.append(
            {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": row.get("phone", "").strip() or None,
                "address": row.get("address", "").strip() or None,
                "gender": row.get("gender", "").strip() or "not_specified",
                "role": role,
            }
        )

    return rows, errors
