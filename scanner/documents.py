import io

import pymupdf
from PIL import Image

from .ocr import ocr_image, OcrIndisponible, OcrTimeout

MAX_CHARS = 8000          # texte envoyé au LLM
MIN_CHARS = 10            # en dessous : document illisible, même après OCR
PAGE_MIN_CHARS = 30       # en dessous : on tente l'OCR sur cette page
MAX_PAGES_OCR = 5         # l'OCR est lent : on le borne
OCR_DPI = 300
MAX_PIXELS_IMAGE = 40_000_000
MAX_COTE_PX = 3500


class DocumentInvalide(Exception):
    """Ni un PDF, ni une image PNG/JPEG lisible."""


class DocumentIllisible(Exception):
    """Format valide mais aucun texte exploitable, même après OCR."""


def _detecter_format(contenu: bytes) -> str | None:
    if b"%PDF-" in contenu[:1024]:
        return "pdf"
    if contenu.startswith(b"\x89PNG\r\n\x1a\n") or contenu.startswith(b"\xff\xd8\xff"):
        return "image"
    return None


def _ocr_page(page) -> str:
    rect = page.rect
    zoom = min(OCR_DPI / 72, MAX_COTE_PX / max(rect.width, rect.height))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return ocr_image(img)


def _texte_pdf(nom: str, contenu: bytes) -> str:
    try:
        pdf = pymupdf.open(stream=contenu, filetype="pdf")
    except Exception as e:
        raise DocumentInvalide(nom) from e

    try:
        morceaux, pages_ocr = [], 0
        for page in pdf:
            texte = page.get_text()
            if len(texte.strip()) < PAGE_MIN_CHARS and pages_ocr < MAX_PAGES_OCR:
                pages_ocr += 1
                texte_ocr = _ocr_page(page)
                if len(texte_ocr.strip()) > len(texte.strip()):
                    texte = texte_ocr
            morceaux.append(texte)
            if sum(len(m) for m in morceaux) >= MAX_CHARS:
                break
        return "\n".join(morceaux)
    except (OcrIndisponible, OcrTimeout):
        raise
    except Exception as e:  # PDF corrompu ou protégé par mot de passe
        raise DocumentInvalide(nom) from e
    finally:
        pdf.close()


def _texte_image(nom: str, contenu: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(contenu))
        if img.width * img.height > MAX_PIXELS_IMAGE:
            raise DocumentInvalide(nom)
        img.load()
    except DocumentInvalide:
        raise
    except Exception as e:  # image corrompue, tronquée, bombe de décompression
        raise DocumentInvalide(nom) from e
    return ocr_image(img)


def extraire_texte(nom: str, contenu: bytes) -> str:
    format_ = _detecter_format(contenu)
    if format_ == "pdf":
        texte = _texte_pdf(nom, contenu)
    elif format_ == "image":
        texte = _texte_image(nom, contenu)
    else:
        raise DocumentInvalide(nom)

    if len(texte.strip()) < MIN_CHARS:
        raise DocumentIllisible(nom)
    return texte[:MAX_CHARS]