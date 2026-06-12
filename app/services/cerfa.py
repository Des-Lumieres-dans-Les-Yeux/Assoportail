"""CERFA 11580*04 generation by filling the official AcroForm PDF.

The official tax-receipt form (``cerfa_11580_04.pdf``, downloaded from
impots.gouv.fr) is a fillable PDF with named AcroForm fields. We fill those
fields directly with :mod:`pypdf` — no DOCX template, no LibreOffice — for
cash donations from individuals (``particulier``) and corporate sponsorship
(``mecena``); only the ticked checkboxes differ between the two.

In-kind donations (``nature``) are NOT receipted on the official CERFA form
but on a dedicated attestation letter (DOCX template stored in the DB,
mail-merged and rendered to PDF via LibreOffice) — see
:func:`build_cerfa_nature_pdf`. :func:`generate_cerfa_pdf` dispatches between
the two.

NOTE: the official form's internal field names are misleading — the field
named ``Adresse`` on page 1 is actually the "Nom ou dénomination" line, etc.
The mapping below was established by inspecting each widget's page and
coordinates, so trust the constants here, not the raw field names.
"""

from __future__ import annotations

import io
import re
import textwrap
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from app.models.config import AssociationConfig

if TYPE_CHECKING:
    from app.models.treasury import Transaction

TEMPLATE_PATH = Path(__file__).parent / "assets" / "cerfa_11580_04.pdf"

# Page-1 "Cochez la case concernée" — maps a stored category key to the exact
# AcroForm checkbox field name. Keys must match AssociationConfig.cerfa_org_category.
ORG_CATEGORY_FIELDS: dict[str, str] = {
    "rup": "Association ou fondation reconnue dutilité publique par décret en date du",
    "fondation_universitaire": (
        "Fondation universitaire ou fondation partenariale mentionnées "
        "respectivement aux articles L 71912 et"
    ),
    "fondation_entreprise": "Fondation dentreprise",
    "oeuvre": "Oeuvre ou organisme dintérêt général",
    "musee": "Musée de France",
    "enseignement_sup": (
        "Etablissement denseignement supérieur ou denseignement artistique "
        "public ou privé dintérêt général à"
    ),
    "creation_entreprises": (
        "Organisme ayant pour objectif exclusif de participer financièrement "
        "à la création dentreprises"
    ),
    "alsace_moselle": (
        "Association cultuelle ou de bienfaisance et établissement public reconnus dAlsaceMoselle"
    ),
    "festivals": "Organisme ayant pour activité principale lorganisation de festivals",
    "aide_alimentaire": (
        "Association fournissant gratuitement une aide alimentaire ou des "
        "soins médicaux à des personnes en"
    ),
    "recherche": "Etablissement de recherche public ou privé dintérêt général à but non lucratif",
}

# "Le bénéficiaire certifie ... la réduction d'impôt prévue à l'article (3)"
CGI_FIELDS = {"200": "200 du CGI", "238 bis": "238 bis du CGI", "978": "978 du CGI"}

# "Date et signature" box on page 2 (PDF points): (x0, y0, x1, y1).
SIGNATURE_BOX = (372.0, 28.0, 519.0, 86.0)
# Page index (0-based) the signature box sits on.
SIGNATURE_PAGE = 1


def _split_street(addr: str | None) -> tuple[str, str]:
    """Split a street address into (number, street name) for the N° / Rue fields."""
    addr = (addr or "").strip()
    m = re.match(r"^(\d+\s*(?:bis|ter|quater)?)\s+(.*)$", addr, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", addr


def _stamp_signature(writer, signature_bytes: bytes) -> None:
    """Overlay the signature image inside the CERFA "Date et signature" box.

    Best-effort: any failure is logged and ignored so receipt generation never
    breaks because of a bad signature image. Transparency is flattened onto
    white (the signature box is blank white space, so this is invisible).
    """
    import logging

    try:
        from PIL import Image
        from pypdf import PdfReader, Transformation

        img = Image.open(io.BytesIO(signature_bytes))
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")

        img_buf = io.BytesIO()
        img.save(img_buf, format="PDF", resolution=72.0)
        img_buf.seek(0)
        overlay = PdfReader(img_buf).pages[0]

        pw = float(overlay.mediabox.width)
        ph = float(overlay.mediabox.height)
        if pw <= 0 or ph <= 0:
            return

        x0, y0, x1, y1 = SIGNATURE_BOX
        box_w, box_h = x1 - x0, y1 - y0
        scale = min(box_w / pw, box_h / ph)
        tw, th = pw * scale, ph * scale
        tx = x0 + (box_w - tw) / 2
        ty = y0 + (box_h - th) / 2

        ctm = Transformation().scale(scale, scale).translate(tx, ty)
        writer.pages[SIGNATURE_PAGE].merge_transformed_page(overlay, ctm)
    except Exception:
        logging.getLogger(__name__).warning("CERFA signature overlay skipped", exc_info=True)


def _amount_words(amount: Decimal) -> str:
    """French words for a monetary amount, e.g. 150.50 → 'Cent cinquante euros et …'."""
    try:
        from num2words import num2words

        euros = int(amount)
        cents = round(int((amount - euros) * 100))
        parts = [num2words(euros, lang="fr") + (" euro" if euros <= 1 else " euros")]
        if cents > 0:
            parts.append(
                "et " + num2words(cents, lang="fr") + (" centime" if cents <= 1 else " centimes")
            )
        return " ".join(parts).capitalize()
    except Exception:
        return f"{amount:.2f} €"


def build_cerfa_pdf(
    transaction: Transaction, cfg: AssociationConfig | None = None
) -> tuple[bytes, str]:
    """Fill the official CERFA 11580*04 form for ``transaction``.

    Returns ``(pdf_bytes, filename)``. Raises ``RuntimeError`` if the
    association configuration is incomplete.
    """
    from pypdf import PdfReader, PdfWriter

    cfg = cfg or AssociationConfig.get()
    if not cfg.name:
        raise RuntimeError("Configuration de l'association incomplète (nom manquant).")

    donation_type = transaction.donation_type or "particulier"
    receipt_id = f"DON-{transaction.date.year}-{transaction.id:05d}"
    amount: Decimal = transaction.amount
    date_str = transaction.date.strftime("%d/%m/%Y")

    n_num, street = _split_street(cfg.address)

    # ---- Bénéficiaire (page 1) — the association ----
    values: dict[str, str] = {
        "Numéro dordre du reçu": receipt_id,
        "Adresse": cfg.name,  # mislabeled field: this is the "Nom ou dénomination" line
        "N": n_num,
        "Rue": street,
        "Code Postal": cfg.zip_code or "",
        "Commune": cfg.city or "",
    }
    # Objet — wraps the association purpose over up to 3 lines
    if cfg.purpose:
        for i, line in enumerate(textwrap.wrap(cfg.purpose.strip(), width=95)[:3], start=1):
            values[f"Objet {i}"] = line

    # ---- Donateur (page 2) ----
    values["Nom"] = transaction.donor_name or ""
    # For corporate sponsorship the donor is a legal entity → no first name.
    if donation_type != "mecena":
        values["Prénoms"] = transaction.donor_first_name or ""
    values["Adresse_2"] = transaction.donor_address or ""
    values["Code Postal_2"] = transaction.donor_zip or ""
    values["Commune_2"] = transaction.donor_city or ""
    values["Euros"] = f"{amount:.2f}".replace(".", ",")
    values["Somme en toutes lettres"] = _amount_words(amount)
    values["date4"] = date_str  # Date du versement ou du don

    # ---- Checkboxes (value '/On' ticks them) ----
    checks: list[str] = []

    # Page-1 organism category
    category = cfg.cerfa_org_category or "oeuvre"
    if cat_field := ORG_CATEGORY_FIELDS.get(category):
        checks.append(cat_field)

    # CGI article
    if donation_type == "mecena":
        cgi_field = "238 bis du CGI"
    else:
        cgi_field = CGI_FIELDS.get((cfg.cgi_article or "200").strip(), "200 du CGI")
    checks.append(cgi_field)

    # Forme du don — manual donations
    checks.append("Déclaration de don manuel")
    # Nature du don — cash (in-kind donations use the dedicated letter, not this form)
    checks.append("Numéraire")

    field_values = {**values, **{name: "/On" for name in checks}}

    reader = PdfReader(str(TEMPLATE_PATH))
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values, auto_regenerate=False)
    # Make viewers render the filled values / ticks (esp. checkboxes).
    writer.set_need_appearances_writer(True)

    if cfg.signature:
        _stamp_signature(writer, cfg.signature)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), f"{receipt_id}.pdf"


def _convert_docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Convert DOCX bytes to PDF using LibreOffice headless (used for nature receipts)."""
    import os
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "input.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        subprocess.run(  # noqa: S603
            [  # noqa: S607
                "soffice",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                tmpdir,
                docx_path,
            ],
            check=True,
            timeout=60,
            capture_output=True,
        )
        with open(os.path.join(tmpdir, "input.pdf"), "rb") as f:
            return f.read()


def build_cerfa_nature_pdf(
    transaction: Transaction, cfg: AssociationConfig | None = None
) -> tuple[bytes, str]:
    """Generate the in-kind ("don en nature") receipt from the custom DOCX letter.

    In-kind donations are not receipted on the official CERFA form but on a
    dedicated attestation letter (association letterhead, declared value,
    signature) uploaded by the bureau and stored in
    ``AssociationConfig.cerfa_tpl_nature``. We mail-merge it and render to PDF.
    """
    import os
    import tempfile

    from mailmerge import MailMerge

    cfg = cfg or AssociationConfig.get()
    tpl_data = cfg.cerfa_tpl_nature
    if tpl_data is None:
        raise RuntimeError(
            "Modèle « don en nature » non configuré. Téléversez-le dans Trésorerie › Configuration."
        )

    receipt_id = f"DON-{transaction.date.year}-{transaction.id:05d}"
    merge_data = {
        "IDRECU": receipt_id,
        "Date": transaction.date.strftime("%d/%m/%Y"),
        "Prénom": transaction.donor_first_name or "",
        "Nom": transaction.donor_name or "",
        "Adresse_de_livraison": transaction.donor_address or "",
        "Ville_de_livraison": transaction.donor_city or "",
        "Code_postal_de_livraison": transaction.donor_zip or "",
        "Courriel": transaction.donor_email or "",
        "Don": transaction.donor_description or "",
    }

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(tpl_data)
        template_path = tmp.name
    try:
        with MailMerge(template_path) as document:
            document.merge(**merge_data)
            buf = io.BytesIO()
            document.write(buf)
    finally:
        os.unlink(template_path)

    pdf_bytes = _convert_docx_to_pdf(buf.getvalue())
    return pdf_bytes, f"{receipt_id}.pdf"


def generate_cerfa_pdf(
    transaction: Transaction, cfg: AssociationConfig | None = None
) -> tuple[bytes, str]:
    """Produce the appropriate receipt PDF for a donation transaction.

    Dispatches by donation type:
      * ``nature``                → custom attestation letter (DOCX → PDF)
      * ``particulier`` / ``mecena`` → official CERFA 11580*04 (filled AcroForm)
    """
    cfg = cfg or AssociationConfig.get()
    if (transaction.donation_type or "particulier") == "nature":
        return build_cerfa_nature_pdf(transaction, cfg)
    return build_cerfa_pdf(transaction, cfg)
