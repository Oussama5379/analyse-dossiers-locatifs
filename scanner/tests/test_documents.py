"""Tests de scanner.documents (détection du format, texte natif, repli OCR)."""
import io
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from scanner.documents import DocumentIllisible, DocumentInvalide, extraire_texte
from scanner.tests.helpers import (TEXTE_LONG, creer_pdf_bytes, creer_png_bytes,
                                   pdf_mixte_bytes, pdf_scanne_bytes)


class TestExtractionDocuments(SimpleTestCase):

    @patch("scanner.documents.ocr_image")
    def test_pdf_natif_n_utilise_pas_l_ocr(self, mock_ocr):
        texte = extraire_texte("b.pdf", creer_pdf_bytes(TEXTE_LONG))
        self.assertIn("3200", texte)
        mock_ocr.assert_not_called()

    @patch("scanner.documents.ocr_image", return_value="TEXTE LU PAR OCR 3200 EUR")
    def test_pdf_scanne_declenche_l_ocr(self, mock_ocr):
        texte = extraire_texte("scan.pdf", pdf_scanne_bytes())
        self.assertIn("LU PAR OCR", texte)
        mock_ocr.assert_called_once()

    @patch("scanner.documents.ocr_image", return_value="TEXTE OCR PAGE DEUX 3200 EUR")
    def test_pdf_mixte_ocr_uniquement_sur_la_page_scannee(self, mock_ocr):
        texte = extraire_texte("mixte.pdf", pdf_mixte_bytes())
        self.assertIn("Jean Dupont", texte)          # page 1 : texte natif
        self.assertIn("PAGE DEUX", texte)            # page 2 : OCR
        mock_ocr.assert_called_once()

    @patch("scanner.documents.ocr_image", return_value="LU PAR OCR DANS UN JPEG")
    def test_image_jpeg_acceptee(self, mock_ocr):
        buf = io.BytesIO()
        Image.new("RGB", (80, 80), "white").save(buf, format="JPEG")
        self.assertIn("JPEG", extraire_texte("photo.jpg", buf.getvalue()))
        mock_ocr.assert_called_once()

    @patch("scanner.documents.ocr_image")
    def test_png_tronque_est_invalide(self, mock_ocr):
        corrompu = creer_png_bytes()[:60]           # en-tête valide, corps coupé
        with self.assertRaises(DocumentInvalide):
            extraire_texte("casse.png", corrompu)
        mock_ocr.assert_not_called()

    def test_texte_tronque_a_max_chars(self):
        with patch("scanner.documents.MAX_CHARS", 40):
            texte = extraire_texte("b.pdf", creer_pdf_bytes(TEXTE_LONG))
        self.assertLessEqual(len(texte), 40)

    @patch("scanner.documents.ocr_image", return_value="")
    def test_scan_sans_texte_est_illisible(self, _m):
        with self.assertRaises(DocumentIllisible):
            extraire_texte("scan.pdf", pdf_scanne_bytes())
