"""Association configuration — singleton model for legal/fiscal settings."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class AssociationConfig(db.Model):
    """Singleton row storing the association's legal identity for document generation.

    There is always exactly one row (id=1). Use ``AssociationConfig.get()``
    to retrieve or auto-create it.

    Attributes:
        name: Full legal name of the association.
        address: Street address.
        zip_code: Postal code.
        city: City.
        siret: SIRET number (14 digits, optional).
        rna: RNA registration number (W + 9 digits, optional).
        legal_form: Legal form, e.g. "Association loi 1901".
        purpose: Statutory purpose / object (objet social).
        cgi_article: CGI article applicable for tax receipts ("200" or "238 bis").
        representative_name: Full name of the signing representative.
        representative_title: Title of the representative (e.g. "Président(e)").
    """

    __tablename__ = "association_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    siret: Mapped[str | None] = mapped_column(String(14), nullable=True)
    rna: Mapped[str | None] = mapped_column(String(10), nullable=True)
    legal_form: Mapped[str | None] = mapped_column(
        String(100), nullable=True, default="Association loi 1901"
    )
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    cgi_article: Mapped[str] = mapped_column(String(20), nullable=False, default="200")
    # CERFA page-1 "Cochez la case concernée" — organism category key
    # (see app.services.cerfa.ORG_CATEGORY_FIELDS).
    cerfa_org_category: Mapped[str] = mapped_column(String(40), nullable=False, default="oeuvre")
    representative_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    representative_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    km_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True, default=0.603)

    # Intro message printed on the QR cards placed inside the pinball machines.
    flipper_card_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Association logo (PNG/JPG) for certificates and official documents
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # Signature image (transparent PNG recommended) stamped on generated CERFA receipts
    signature: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # CERFA DOCX templates stored as binary blobs (nullable = not uploaded yet)
    cerfa_tpl_particulier: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cerfa_tpl_entreprise: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cerfa_tpl_nature: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    @classmethod
    def get(cls) -> AssociationConfig:
        """Return the singleton config row, creating it with defaults if absent."""
        cfg = db.session.get(cls, 1)
        if cfg is None:
            cfg = cls(id=1)
            db.session.add(cfg)
            db.session.flush()
        return cfg

    def __repr__(self) -> str:
        return f"<AssociationConfig name={self.name!r}>"
