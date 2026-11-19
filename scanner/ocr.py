import os

import pytesseract
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()

OCR_LANGUES = os.environ.get("OCR_LANGUES", "fra+eng")
OCR_TIMEOUT_S = 30
MAX_COTE_PX = 3500

if os.environ.get("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]


class OcrIndisponible(Exception):
    """Tesseract absent ou langue manquante : problème de configuration serveur."""


class OcrTimeout(Exception):
    """L'OCR a pris trop de temps."""


def _preparer(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)  # photos de téléphone : corrige l'orientation
    if img.mode in ("RGBA", "LA", "P"):
        # un PNG transparent deviendrait noir sur noir en niveaux de gris
        img = img.convert("RGBA")
        fond = Image.new("RGBA", img.size, (255, 255, 255, 255))
        fond.alpha_composite(img)
        img = fond
    img = ImageOps.autocontrast(img.convert("L"))
    img.thumbnail((MAX_COTE_PX, MAX_COTE_PX))  # réduit seulement, borne le temps de calcul
    return img


def ocr_image(img: Image.Image) -> str:
    try:
        return pytesseract.image_to_string(
            _preparer(img), lang=OCR_LANGUES, timeout=OCR_TIMEOUT_S
        )
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as e:
        # exécutable introuvable, ou langue « fra » non installée
        raise OcrIndisponible() from e
    except RuntimeError as e:
        # pytesseract lève RuntimeError("Tesseract process timeout") ; il faut garder
        # cet except APRÈS TesseractError, qui est lui aussi un RuntimeError
        raise OcrTimeout() from e