"""Fonctions utilitaires partagées par les tests (aucun test ici)."""
import io

import pymupdf
from PIL import Image
from unittest.mock import MagicMock

TEXTE_LONG = "Bulletin de paie de Jean Dupont, net a payer 3200 euros, periode octobre"


def creer_pdf_bytes(texte: str, mot_de_passe: str | None = None) -> bytes:
    """Génère un vrai PDF en mémoire via PyMuPDF."""
    doc = pymupdf.open()
    page = doc.new_page()
    if texte:
        page.insert_text((72, 72), texte)
    buf = io.BytesIO()
    if mot_de_passe:
        doc.save(
            buf,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw=mot_de_passe,
            user_pw=mot_de_passe,
        )
    else:
        doc.save(buf)
    doc.close()
    return buf.getvalue()


def creer_png_bytes() -> bytes:
    """Génère une vraie petite image PNG blanche en mémoire."""
    img = Image.new("RGB", (100, 100), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def fausse_reponse_groq(contenu_json: str) -> MagicMock:
    """Simule la structure de retour de client.chat.completions.create()."""
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = contenu_json
    return completion


def pdf_scanne_bytes() -> bytes:
    """PDF dont la page est une image : aucun texte natif (comme un vrai scan)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=creer_png_bytes())
    data = doc.tobytes()
    doc.close()
    return data


def pdf_mixte_bytes() -> bytes:
    """Page 1 en texte natif, page 2 en image seule."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), TEXTE_LONG)
    doc.new_page().insert_image(pymupdf.paper_rect("a4"), stream=creer_png_bytes())
    data = doc.tobytes()
    doc.close()
    return data

