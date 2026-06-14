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
import logging
import re
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from app.models.config import AssociationConfig

if TYPE_CHECKING:
    from app.models.treasury import Transaction

logger = logging.getLogger(__name__)

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
    "fondation_patrimoine": (
        "Fondation du patrimoine ou fondation ou association qui affecte "
        "irrévocablement les dons à la Fondation du"
    ),
    "recherche": "Etablissement de recherche public ou privé dintérêt général à but non lucratif",
    "entreprise_insertion": (
        "Entreprise dinsertion ou entreprise de travail temporaire dinsertion "
        "articles L 51325 et L 51326 du"
    ),
    "association_intermediaire": "Association intermédiaire article L51327 du code du travail",
    "ateliers_insertion": "Ateliers et chantiers dinsertion article L513215 du code du travail",
    "entreprises_adaptees": "Entreprises adaptées article L521313 du code du travail",
    "anr": "Agence nationale de la recherche ANR",
    "recherche_agree": "Société ou organisme agrée de recherche scientifique ou technique 2",
    "autres": "Autres organisme",
}


# "Le bénéficiaire certifie ... la réduction d'impôt prévue à l'article (3)"
CGI_FIELDS = {"200": "200 du CGI", "238 bis": "238 bis du CGI", "978": "978 du CGI"}

# "En cas de don en numéraire, mode de versement du don" — maps the stored
# payment-mode key to the exact AcroForm checkbox field name. Keys must match
# the choices of TransactionForm.donor_payment_mode.
PAYMENT_MODE_FIELDS = {
    "especes": "Remise despèces",
    "cheque": "Chèque",
    "virement": "Virement prélèvement carte bancaire",
}

# "Date et signature" box on page 2 (PDF points): (x0, y0, x1, y1).
SIGNATURE_BOX = (372.0, 28.0, 519.0, 86.0)
# Page index (0-based) the signature box sits on.
SIGNATURE_PAGE = 1

# Bundled TrueType font used to raster field text. We do NOT rely on AcroForm
# fields for display: many viewers (PDF.js, Drive preview…) regenerate field
# appearances with their own font, corrupting accents and character widths.
# Instead we draw every value as a crisp raster image and flatten the form —
# the result renders identically everywhere. DejaVu Sans covers full Unicode.
FONT_PATH = Path(__file__).parent / "assets" / "DejaVuSans.ttf"
_TEXT_SUPERSAMPLE = 3  # render at 3× then place at 1× → ~216 DPI, crisp output


def _image_overlay_page(img):
    """Save an RGB PIL image as a 1-page PDF; return (page, width_pt, height_pt)."""
    from pypdf import PdfReader

    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=72.0)  # 1 px = 1 pt
    buf.seek(0)
    page = PdfReader(buf).pages[0]
    return page, float(page.mediabox.width), float(page.mediabox.height)


def _place_overlay(writer, page_idx: int, overlay_page, scale: float, tx: float, ty: float):
    from pypdf import Transformation

    writer.pages[page_idx].merge_transformed_page(
        overlay_page, Transformation().scale(scale, scale).translate(tx, ty)
    )


def _overlay_text(
    writer, page_idx: int, rect, text: str, *, max_pt: float = 10.5, pad: float = 2.0
):
    """Draw *text* as a raster image stamped into *rect* (left-aligned, v-centered)."""
    text = (text or "").strip()
    if not text:
        return
    from PIL import Image, ImageDraw, ImageFont

    x0, y0, x1, y1 = rect
    box_w, box_h = x1 - x0, y1 - y0
    font_pt = min(max_pt, box_h * 0.72)
    s = _TEXT_SUPERSAMPLE
    font = bbox = None
    for _ in range(4):  # shrink to fit the box width
        font = ImageFont.truetype(str(FONT_PATH), max(1, round(font_pt * s)))
        bbox = font.getbbox(text)
        text_w_pt = (bbox[2] - bbox[0]) / s
        if text_w_pt <= box_w - 2 * pad or font_pt <= 5:
            break
        font_pt *= (box_w - 2 * pad) / text_w_pt
    bx0, by0, bx1, by1 = bbox
    img = Image.new("RGB", (max(1, bx1 - bx0 + 2), max(1, by1 - by0 + 2)), (255, 255, 255))
    ImageDraw.Draw(img).text((1 - bx0, 1 - by0), text, font=font, fill=(0, 0, 0))
    overlay, _pw, ph = _image_overlay_page(img)
    ty = y0 + (box_h - ph / s) / 2
    _place_overlay(writer, page_idx, overlay, 1.0 / s, x0 + pad, ty)


def _overlay_check(writer, page_idx: int, rect):
    """Stamp an "X" mark centered in a checkbox *rect*."""
    from PIL import Image, ImageDraw, ImageFont

    x0, y0, x1, y1 = rect
    side = min(x1 - x0, y1 - y0)
    s = _TEXT_SUPERSAMPLE
    font = ImageFont.truetype(str(FONT_PATH), max(1, round(side * 0.9 * s)))
    bx0, by0, bx1, by1 = font.getbbox("X")
    img = Image.new("RGB", (max(1, bx1 - bx0 + 2), max(1, by1 - by0 + 2)), (255, 255, 255))
    ImageDraw.Draw(img).text((1 - bx0, 1 - by0), "X", font=font, fill=(0, 0, 0))
    overlay, pw, ph = _image_overlay_page(img)
    cx = x0 + (x1 - x0 - pw / s) / 2
    cy = y0 + (y1 - y0 - ph / s) / 2
    _place_overlay(writer, page_idx, overlay, 1.0 / s, cx, cy)


def _field_rects(writer) -> dict:
    """Map each AcroForm field name to (page_index, (x0, y0, x1, y1)) in PDF points."""
    rects: dict = {}
    for pidx, page in enumerate(writer.pages):
        for a in page.get("/Annots") or []:
            o = a.get_object()
            if o.get("/Subtype") != "/Widget":
                continue
            parent = o.get("/Parent")
            name = o.get("/T") or (parent.get_object().get("/T") if parent else None)
            if name is None:
                continue
            r = [float(v) for v in o["/Rect"]]
            x0, x1 = sorted((r[0], r[2]))
            y0, y1 = sorted((r[1], r[3]))
            rects[str(name)] = (pidx, (x0, y0, x1, y1))
    return rects


def _flatten_form(writer) -> None:
    """Remove all widget annotations and the AcroForm so nothing re-renders."""
    from pypdf.generic import ArrayObject, NameObject

    for page in writer.pages:
        if "/Annots" in page:
            page[NameObject("/Annots")] = ArrayObject()
    root = writer._root_object
    if "/AcroForm" in root:
        del root[NameObject("/AcroForm")]


def _receipt_id(transaction: Transaction) -> str:
    """Return the receipt number — the assigned sequential one, or a fallback.

    ``issue_cerfa`` assigns ``cerfa_number`` before generation; the fallback only
    applies to ad-hoc previews of a transaction that was never formally issued.
    """
    return transaction.cerfa_number or f"DON-{transaction.date.year}-{transaction.id:05d}"


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
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(signature_bytes))
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")

        overlay, pw, ph = _image_overlay_page(img)
        if pw <= 0 or ph <= 0:
            return
        x0, y0, x1, y1 = SIGNATURE_BOX
        box_w, box_h = x1 - x0, y1 - y0
        scale = min(box_w / pw, box_h / ph)
        tx = x0 + (box_w - pw * scale) / 2
        ty = y0 + (box_h - ph * scale) / 2
        _place_overlay(writer, SIGNATURE_PAGE, overlay, scale, tx, ty)
    except Exception:
        logger.warning("CERFA signature overlay skipped", exc_info=True)


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
    receipt_id = _receipt_id(transaction)
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
    values["date5"] = date_str  # date next to "Date et signature"

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
    # Mode de versement du don numéraire — coche espèces / chèque / virement-CB
    if mode_field := PAYMENT_MODE_FIELDS.get(transaction.donor_payment_mode or ""):
        checks.append(mode_field)

    reader = PdfReader(str(TEMPLATE_PATH))
    writer = PdfWriter()
    writer.append(reader)
    rects = _field_rects(writer)

    # Raster every value into its field rect, then tick the checkboxes. We bypass
    # interactive AcroForm fields entirely (see FONT_PATH note) for viewer-proof
    # output.
    for name, text in values.items():
        loc = rects.get(name)
        if loc and text:
            _overlay_text(writer, loc[0], loc[1], text)
    for name in checks:
        loc = rects.get(name)
        if loc:
            _overlay_check(writer, loc[0], loc[1])

    if cfg.signature:
        _stamp_signature(writer, cfg.signature)

    _flatten_form(writer)

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

    receipt_id = _receipt_id(transaction)
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


# ---------------------------------------------------------------------------
# Issuance orchestration: number assignment, Drive archival, generation
# ---------------------------------------------------------------------------


def assign_cerfa_number(transaction: Transaction) -> str:
    """Assign a sequential per-year receipt number if not already set; return it.

    Numbers reset each calendar year of the donation date and are never reused.
    A unique constraint on ``cerfa_number`` guards against concurrent assignment;
    on collision we retry inside a SAVEPOINT so only the failed assignment is
    rolled back — never the caller's other pending changes.
    """
    if transaction.cerfa_number:
        return transaction.cerfa_number

    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.models.treasury import Transaction as Txn

    year = transaction.date.year
    prefix = f"DON-{year}-"
    for _ in range(8):
        existing = db.session.scalars(
            db.select(Txn.cerfa_number).where(Txn.cerfa_number.like(f"{prefix}%"))
        ).all()
        # Only parse well-formed "DON-AAAA-NNNNN" suffixes; ignore anything else
        # (defensive against hand-edited values) instead of crashing on int().
        seqs = [int(s) for n in existing if (s := n.rsplit("-", 1)[-1]).isdigit()]
        candidate = f"{prefix}{max(seqs, default=0) + 1:05d}"
        transaction.cerfa_number = candidate
        try:
            with db.session.begin_nested():  # SAVEPOINT — isolates this flush
                db.session.flush()
            return candidate
        except IntegrityError:
            transaction.cerfa_number = None
    raise RuntimeError("Impossible d'attribuer un numéro de reçu (collisions répétées).")


def archive_cerfa_to_drive(transaction: Transaction, pdf_bytes: bytes, filename: str) -> None:
    """Upload the receipt PDF to Drive under Comptabilité/Reçus fiscaux/<year>.

    No-op if already archived or if no Shared Drive is configured. Best-effort:
    failures are logged, not raised (generation must still succeed offline).
    """
    if transaction.cerfa_drive_file_id:
        return
    try:
        from flask import current_app

        from app.services.drive import DriveService

        if not current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
            return
        drive_svc = DriveService.from_db()
        fid, wlink = drive_svc.upload_file(
            pdf_bytes, filename, "application/pdf", "cerfa", year=transaction.date.year
        )
        transaction.cerfa_drive_file_id = fid
        transaction.cerfa_drive_web_link = wlink
    except Exception as exc:
        logger.warning("Drive archival of CERFA failed: %s", exc)


def issue_cerfa(
    transaction: Transaction, cfg: AssociationConfig | None = None
) -> tuple[bytes, str]:
    """Issue the receipt: assign a number, generate the PDF, archive it to Drive.

    Idempotent — re-issuing reuses the same number and Drive file. Commits the
    resulting ``cerfa_number`` / ``cerfa_generated_at`` / Drive references.
    Returns ``(pdf_bytes, filename)``.
    """
    from app.extensions import db

    cfg = cfg or AssociationConfig.get()
    assign_cerfa_number(transaction)
    pdf_bytes, filename = generate_cerfa_pdf(transaction, cfg)
    if transaction.cerfa_generated_at is None:
        transaction.cerfa_generated_at = datetime.now(UTC)
    archive_cerfa_to_drive(transaction, pdf_bytes, filename)
    db.session.commit()
    return pdf_bytes, filename
