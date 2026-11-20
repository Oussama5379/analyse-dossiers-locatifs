from django.test import SimpleTestCase
from scanner.schemas import PieceExtraite
from scanner.analysis import construire_resume
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
import io
from unittest.mock import MagicMock, patch

import pymupdf
from openai import APITimeoutError, RateLimitError
from PIL import Image

from scanner.ocr import OcrIndisponible, OcrTimeout

class TestAnalyseLogiqueMetier(SimpleTestCase):
    """Test de la Zone A : la logique de calcul pur, sans base de données."""

    def test_dossier_parfait_couvre_loyer(self):
        """Cas nominal : tout est là, les noms correspondent, l'argent suffit."""
        pieces = [
            ("cni.pdf", PieceExtraite(type="piece_identite", nom="Jean Dupont")),
            ("salaire1.pdf", PieceExtraite(type="bulletin_salaire", nom="Jean Dupont", revenu_mensuel_net=3000)),
            ("impots.pdf", PieceExtraite(type="avis_imposition", nom="jean dupont")), # casse différente pour tester la robustesse
        ]
        
        resultat = construire_resume(pieces, loyer=1000.0)
        
        self.assertEqual(resultat["candidat"], "Jean Dupont")
        self.assertEqual(resultat["revenus_mensuels"], 3000)
        self.assertTrue(resultat["couvre_3x_loyer"])
        self.assertEqual(len(resultat["documents_manquants"]), 0)
        self.assertEqual(len(resultat["avertissements"]), 0)

    def test_calcul_moyenne_salaires(self):
        """Règle métier : le revenu est la moyenne des bulletins."""
        pieces = [
            ("salaire_mars.pdf", PieceExtraite(type="bulletin_salaire", revenu_mensuel_net=2000)),
            ("salaire_avril.pdf", PieceExtraite(type="bulletin_salaire", revenu_mensuel_net=4000)),
        ]
        resultat = construire_resume(pieces, loyer=1000.0)
        
        self.assertEqual(resultat["revenus_mensuels"], 3000) # (2000+4000)/2
        self.assertTrue(resultat["couvre_3x_loyer"]) # 3000 >= 3000

    def test_revenu_insuffisant(self):
        """Cas où le salaire < 3x le loyer."""
        pieces = [
            ("salaire.pdf", PieceExtraite(type="bulletin_salaire", revenu_mensuel_net=2999))
        ]
        resultat = construire_resume(pieces, loyer=1000.0)
        
        self.assertFalse(resultat["couvre_3x_loyer"])

    def test_detection_noms_differents(self):
        """Anti-fraude : les noms ne matchent pas entre l'ID et la fiche de paie."""
        pieces = [
            ("cni.pdf", PieceExtraite(type="piece_identite", nom="Jean Dupont")),
            ("salaire.pdf", PieceExtraite(type="bulletin_salaire", nom="Paul Martin", revenu_mensuel_net=2000)),
        ]
        resultat = construire_resume(pieces, loyer=500.0)
        
        self.assertIn("Les noms diffèrent d'un document à l'autre.", resultat["avertissements"])

    def test_documents_incomplets_et_ignores(self):
        """Test des listes de documents manquants et ignorés."""
        pieces = [
            ("cni.pdf", PieceExtraite(type="piece_identite", nom="Jean")),
            ("facture.pdf", PieceExtraite(type="autre", nom="Jean")),
        ]
        resultat = construire_resume(pieces, loyer=500.0)
        
        self.assertIn("bulletin_salaire", resultat["documents_manquants"])
        self.assertIn("avis_imposition", resultat["documents_manquants"])
        self.assertIn("facture.pdf", resultat["documents_ignores"])
        self.assertIsNone(resultat["revenus_mensuels"])
        self.assertIsNone(resultat["couvre_3x_loyer"])
        self.assertTrue(any("couverture du loyer indéterminée" in adv for adv in resultat["avertissements"]))

class TestValidationEntreeAPI(SimpleTestCase):
    """Test de la Zone B : validation des entrées HTTP avant tout traitement lourd."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("analyze-dossier")

    def test_loyer_manquant_ou_invalide(self):
        """Loyer absent, non numérique ou envoyé sous forme de fichier (TypeError)."""
        # 1. Loyer absent
        res_absent = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(res_absent.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nombre valide", res_absent.data["error"])

        # 2. Loyer non numérique
        res_texte = self.client.post(self.url, {"loyer": "mille"}, format="multipart")
        self.assertEqual(res_texte.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. Fichier envoyé à la place du champ texte loyer -> déclenche TypeError dans float()
        faux_loyer = SimpleUploadedFile("loyer.txt", b"1000")
        res_fichier = self.client.post(self.url, {"loyer": faux_loyer}, format="multipart")
        self.assertEqual(res_fichier.status_code, status.HTTP_400_BAD_REQUEST)

    def test_loyer_negatif_zero_ou_non_fini(self):
        """Rejet des montants <= 0, NaN ou infinis."""
        for valeur_invalide in ["0", "-500", "nan", "inf", "-inf"]:
            with self.subTest(loyer=valeur_invalide):
                res = self.client.post(self.url, {"loyer": valeur_invalide}, format="multipart")
                self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("positif", res.data["error"])

    def test_aucun_fichier(self):
        """Rejet si aucun document n'est joint."""
        res = self.client.post(self.url, {"loyer": "1000"}, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Au moins un document", res.data["error"])

    def test_trop_de_fichiers(self):
        """Rejet au-delà de MAX_DOCUMENTS (6)."""
        fichiers = [
            SimpleUploadedFile(f"doc_{i}.pdf", b"%PDF-1.4 contenu")
            for i in range(7)
        ]
        res = self.client.post(
            self.url,
            {"loyer": "1000", "documents": fichiers},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Maximum 6 documents", res.data["error"])

    def test_fichier_trop_lourd(self):
        """Rejet HTTP 413 si un fichier dépasse 5 Mo."""
        fichier_valide = SimpleUploadedFile("ok.pdf", b"%PDF-1.4 petit")
        fichier_lourd = SimpleUploadedFile("gros.pdf", b"0" * (5 * 1024 * 1024 + 1))

        res = self.client.post(
            self.url,
            {"loyer": "1000", "documents": [fichier_valide, fichier_lourd]},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        self.assertIn("dépasse 5 Mo", res.data["error"])

def _creer_pdf_bytes(texte: str, mot_de_passe: str | None = None) -> bytes:
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


def _creer_png_bytes() -> bytes:
    """Génère une vraie petite image PNG blanche en mémoire."""
    img = Image.new("RGB", (100, 100), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fausse_reponse_groq(contenu_json: str) -> MagicMock:
    """Simule la structure de retour de client.chat.completions.create()."""
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = contenu_json
    return completion


class TestIntegrationEtErreursAPI(SimpleTestCase):
    """Zones C & D : formats de fichiers, pannes OCR/LLM mockées et parcours complet."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("analyze-dossier")

    # --- 1. Gestion des formats et documents ---

    def test_format_fichier_invalide_415(self):
        """Un fichier texte déguisé en PDF est rejeté en 415."""
        faux_pdf = SimpleUploadedFile("faux.pdf", b"Ceci n'est pas un PDF")
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [faux_pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_pdf_protege_par_mot_de_passe_422(self):
        """Un PDF chiffré est détecté via needs_pass et rejeté en 422 (pas 415)."""
        pdf_chiffre = _creer_pdf_bytes("Bulletin confidentiel", mot_de_passe="secret123")
        fichier = SimpleUploadedFile("protege.pdf", pdf_chiffre)

        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [fichier]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("protégé par mot de passe", res.data["error"])

    @patch("scanner.documents.ocr_image", return_value="   ")
    def test_document_illisible_apres_ocr_422(self, mock_ocr):
        """Une image blanche sans texte lisible renvoie 422."""
        image_vide = SimpleUploadedFile("blanche.png", _creer_png_bytes())
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [image_vide]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        mock_ocr.assert_called_once()

    # --- 2. Pannes OCR ---

    @patch("scanner.documents.ocr_image", side_effect=OcrIndisponible())
    def test_ocr_indisponible_503(self, _mock_ocr):
        """Si Tesseract est absent, l'API renvoie 503."""
        image = SimpleUploadedFile("scan.png", _creer_png_bytes())
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [image]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @patch("scanner.documents.ocr_image", side_effect=OcrTimeout())
    def test_ocr_timeout_504(self, _mock_ocr):
        """Si Tesseract dépasse le délai, l'API renvoie 504."""
        image = SimpleUploadedFile("scan.png", _creer_png_bytes())
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [image]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_504_GATEWAY_TIMEOUT)

    # --- 3. Pannes et configuration LLM ---

    @patch("scanner.llm._client", None)
    @patch.dict("os.environ", {"GROQ_API_KEY": ""}, clear=False)
    def test_cle_api_groq_manquante_503(self):
        """Sans GROQ_API_KEY, l'API renvoie proprement 503 sans crasher."""
        pdf = SimpleUploadedFile(
            "salaire.pdf",
            _creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
        )
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("indisponible", res.data["error"])

    @patch("scanner.llm.get_client")
    def test_llm_surcharge_rate_limit_503(self, mock_get_client):
        """Une RateLimitError de Groq est traduite en 503."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RateLimitError(
            message="Rate limit", response=MagicMock(), body=None
        )
        mock_get_client.return_value = mock_client

        pdf = SimpleUploadedFile(
            "salaire.pdf",
            _creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
        )
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @patch("scanner.llm.get_client")
    def test_llm_timeout_504(self, mock_get_client):
        """Un timeout de Groq est traduit en 504."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APITimeoutError(
            request=MagicMock()
        )
        mock_get_client.return_value = mock_client

        pdf = SimpleUploadedFile(
            "salaire.pdf",
            _creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
        )
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_504_GATEWAY_TIMEOUT)

    @patch("scanner.llm.get_client")
    def test_llm_reponse_json_invalide_502(self, mock_get_client):
        """Un JSON mal formé ou hors schéma Pydantic renvoie 502."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fausse_reponse_groq(
            '{"type": "type_inconnu", "revenu_mensuel_net": -50}'
        )
        mock_get_client.return_value = mock_client

        pdf = SimpleUploadedFile(
            "salaire.pdf",
            _creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
        )
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch("scanner.llm.get_client")
    def test_aucun_justificatif_locatif_reconnu_422(self, mock_get_client):
        """Si tous les documents sont classés 'autre', l'API renvoie 422."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fausse_reponse_groq(
            '{"type": "autre", "nom": null, "revenu_mensuel_net": null}'
        )
        mock_get_client.return_value = mock_client

        pdf = SimpleUploadedFile(
            "recette.pdf",
            _creer_pdf_bytes("Recette de tarte aux pommes traditionnelle française"),
        )
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("justificatif locatif", res.data["error"])

    # --- 4. Happy Path complet (PDF + Image OCR + LLM) ---

    @patch("scanner.documents.ocr_image")
    @patch("scanner.llm.get_client")
    def test_parcours_complet_nominal_200(self, mock_get_client, mock_ocr):
        """Test bout-en-bout : 1 PDF natif (bulletin) + 1 image PNG scannée (CNI)."""
        mock_ocr.return_value = "CARTE NATIONALE D'IDENTITE NOM: DUPONT PRENOM: JEAN"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _fausse_reponse_groq(
                '{"type": "bulletin_salaire", "nom": "Jean Dupont", "revenu_mensuel_net": 3300}'
            ),
            _fausse_reponse_groq(
                '{"type": "piece_identite", "nom": "Jean Dupont", "revenu_mensuel_net": null}'
            ),
        ]
        mock_get_client.return_value = mock_client

        pdf_salaire = SimpleUploadedFile(
            "bulletin_mars.pdf",
            _creer_pdf_bytes("Bulletin de salaire Mars - Jean Dupont - Net à payer: 3300 EUR"),
        )
        png_cni = SimpleUploadedFile("cni.png", _creer_png_bytes())

        res = self.client.post(
            self.url,
            {"loyer": "1000", "documents": [pdf_salaire, png_cni]},
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "success")

        resume = res.data["resume"]
        self.assertEqual(resume["candidat"], "Jean Dupont")
        self.assertEqual(resume["revenus_mensuels"], 3300)
        self.assertTrue(resume["couvre_3x_loyer"])
        self.assertEqual(resume["documents_manquants"], ["avis_imposition"])
        self.assertEqual(resume["avertissements"], [])