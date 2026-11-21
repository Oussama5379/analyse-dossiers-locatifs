"""Tests de scanner.ocr (traduction des erreurs Tesseract)."""
import os
import shutil
from unittest import skipUnless
from unittest.mock import patch

import pytesseract
from django.test import SimpleTestCase
from PIL import Image, ImageDraw, ImageFont

from scanner.ocr import OcrIndisponible, OcrTimeout, ocr_image


class TestOcrTraduitLesErreursTesseract(SimpleTestCase):
    """Les tests d'API mockent ocr_image : cette classe teste la traduction
    des erreurs de pytesseract, qui n'est couverte nulle part ailleurs."""

    def setUp(self):
        self.img = Image.new("RGB", (50, 50), "white")

    @patch("scanner.ocr.pytesseract.image_to_string",
           side_effect=pytesseract.TesseractNotFoundError())
    def test_tesseract_introuvable(self, _m):
        with self.assertRaises(OcrIndisponible):
            ocr_image(self.img)

    @patch("scanner.ocr.pytesseract.image_to_string",
           side_effect=pytesseract.TesseractError(1, "Failed loading language 'fra'"))
    def test_langue_manquante(self, _m):
        with self.assertRaises(OcrIndisponible):
            ocr_image(self.img)

    @patch("scanner.ocr.pytesseract.image_to_string",
           side_effect=RuntimeError("Tesseract process timeout"))
    def test_timeout(self, _m):
        with self.assertRaises(OcrTimeout):
            ocr_image(self.img)

    @skipUnless(shutil.which("tesseract") or os.environ.get("TESSERACT_CMD"),
                "Tesseract non installé")
    @patch("scanner.ocr.OCR_LANGUES", "eng")  # eng est toujours installé avec Tesseract
    def test_ocr_reel_lit_un_montant(self):
        img = Image.new("RGB", (900, 200), "white")
        ImageDraw.Draw(img).text((30, 60), "NET A PAYER 3200 EUR",
                                 fill="black", font=ImageFont.load_default(size=48))
        self.assertIn("3200", ocr_image(img))
