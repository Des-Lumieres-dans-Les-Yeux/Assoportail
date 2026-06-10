"""Tombola file parser — CSV and XLSX to list of ParsedRow."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass


@dataclass
class ParsedRow:
    email: str
    last_name: str | None
    first_name: str | None
    phone: str | None
    ticket_number: int | None
    order_ref: str | None


def _detect_columns(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for i, h in enumerate(headers):
        if not h:
            continue
        hl = str(h).lower().strip()
        if "email" in hl and "email" not in mapping:
            mapping["email"] = i
        if (
            ("nom" in hl or "name" in hl)
            and "prénom" not in hl
            and "prenom" not in hl
            and "first" not in hl
            and "last_name" not in mapping
        ):
            mapping["last_name"] = i
        if ("prénom" in hl or "prenom" in hl or "first" in hl) and "first_name" not in mapping:
            mapping["first_name"] = i
        if (
            any(x in hl for x in ("téléphone", "telephone", "phone", "portable", "mobile"))
            and "phone" not in mapping
        ):
            mapping["phone"] = i
        if (
            ("billet" in hl or "ticket" in hl)
            and any(x in hl for x in ("num", "n°", "#"))
            and "ticket_number" not in mapping
        ):
            mapping["ticket_number"] = i
        if ("référence" in hl or "reference" in hl or "réf" in hl) and "order_ref" not in mapping:
            mapping["order_ref"] = i
    return mapping


def _coerce_row(raw: list, col: dict[str, int]) -> ParsedRow | None:
    def get(key: str) -> str | None:
        idx = col.get(key)
        if idx is None or idx >= len(raw):
            return None
        v = raw[idx]
        return str(v).strip() if v is not None and str(v).strip() else None

    email = get("email")
    if not email:
        return None

    ticket_number: int | None = None
    ticket_raw = get("ticket_number")
    if ticket_raw is not None:
        try:
            ticket_number = int(float(ticket_raw))
        except (ValueError, TypeError):
            pass

    return ParsedRow(
        email=email.lower(),
        last_name=get("last_name"),
        first_name=get("first_name"),
        phone=get("phone"),
        ticket_number=ticket_number,
        order_ref=get("order_ref"),
    )


def parse_csv(data: bytes) -> list[ParsedRow]:
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        return []
    col = _detect_columns(rows[0])
    if "email" not in col:
        return []
    return [r for raw in rows[1:] if (r := _coerce_row(raw, col)) is not None]


def parse_xlsx(data: bytes) -> list[ParsedRow]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    col = _detect_columns([str(h) if h is not None else "" for h in rows[0]])
    if "email" not in col:
        return []
    return [r for raw in rows[1:] if (r := _coerce_row(list(raw), col)) is not None]


def parse_file(filename: str, data: bytes) -> list[ParsedRow]:
    if filename.lower().endswith(".xlsx"):
        return parse_xlsx(data)
    return parse_csv(data)
