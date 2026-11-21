"""Tests de l'endpoint POST /api/analyze/ (validation, pannes OCR/LLM mockées, parcours complet)."""
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from openai import APITimeoutError, RateLimitError
from rest_framework import status
from rest_framework.test import APIClient

from scanner.ocr import OcrIndisponible, OcrTimeout
from scanner.tests.helpers import (TEXTE_LONG, creer_pdf_bytes, creer_png_bytes,
                                   fausse_reponse_groq)


class TestValidationEntreeAPI(SimpleTestCase):
    """Validation des entrées HTTP (loyer, nombre et taille des fichiers) avant tout traitement."""

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


class TestIntegrationEtErreursAPI(SimpleTestCase):
    """Formats de fichiers, pannes OCR et LLM (mockées) et parcours complet de l'API."""

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
        pdf_chiffre = creer_pdf_bytes("Bulletin confidentiel", mot_de_passe="secret123")
        fichier = SimpleUploadedFile("protege.pdf", pdf_chiffre)

        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [fichier]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("protégé par mot de passe", res.data["error"])

    @patch("scanner.documents.ocr_image", return_value="   ")
    def test_document_illisible_apres_ocr_422(self, mock_ocr):
        """Une image blanche sans texte lisible renvoie 422."""
        image_vide = SimpleUploadedFile("blanche.png", creer_png_bytes())
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [image_vide]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        mock_ocr.assert_called_once()

    # --- 2. Pannes OCR ---

    @patch("scanner.documents.ocr_image", side_effect=OcrIndisponible())
    def test_ocr_indisponible_503(self, _mock_ocr):
        """Si Tesseract est absent, l'API renvoie 503."""
        image = SimpleUploadedFile("scan.png", creer_png_bytes())
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [image]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @patch("scanner.documents.ocr_image", side_effect=OcrTimeout())
    def test_ocr_timeout_504(self, _mock_ocr):
        """Si Tesseract dépasse le délai, l'API renvoie 504."""
        image = SimpleUploadedFile("scan.png", creer_png_bytes())
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
            creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
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
            creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
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
            creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
        )
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_504_GATEWAY_TIMEOUT)

    @patch("scanner.llm.get_client")
    def test_llm_reponse_json_invalide_502(self, mock_get_client):
        """Un JSON mal formé ou hors schéma Pydantic renvoie 502."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fausse_reponse_groq(
            '{"type": "type_inconnu", "revenu_mensuel_net": -50}'
        )
        mock_get_client.return_value = mock_client

        pdf = SimpleUploadedFile(
            "salaire.pdf",
            creer_pdf_bytes("Bulletin de paie Jean Dupont Net à payer 3200 euros"),
        )
        res = self.client.post(
            self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch("scanner.llm.get_client")
    def test_aucun_justificatif_locatif_reconnu_422(self, mock_get_client):
        """Si tous les documents sont classés 'autre', l'API renvoie 422."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fausse_reponse_groq(
            '{"type": "autre", "nom": null, "revenu_mensuel_net": null}'
        )
        mock_get_client.return_value = mock_client

        pdf = SimpleUploadedFile(
            "recette.pdf",
            creer_pdf_bytes("Recette de tarte aux pommes traditionnelle française"),
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
            fausse_reponse_groq(
                '{"type": "bulletin_salaire", "nom": "Jean Dupont", "revenu_mensuel_net": 3300}'
            ),
            fausse_reponse_groq(
                '{"type": "piece_identite", "nom": "Jean Dupont", "revenu_mensuel_net": null}'
            ),
        ]
        mock_get_client.return_value = mock_client

        pdf_salaire = SimpleUploadedFile(
            "bulletin_mars.pdf",
            creer_pdf_bytes("Bulletin de salaire Mars - Jean Dupont - Net à payer: 3300 EUR"),
        )
        png_cni = SimpleUploadedFile("cni.png", creer_png_bytes())

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


class TestLLMRenvoieNImporteQuoi(SimpleTestCase):

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("analyze-dossier")

    def _post_bulletin(self):
        pdf = SimpleUploadedFile("b.pdf", creer_pdf_bytes(TEXTE_LONG))
        return self.client.post(self.url, {"loyer": "1000", "documents": [pdf]}, format="multipart")

    @patch("scanner.llm.get_client")
    def test_contenus_invalides_donnent_502(self, mock_get_client):
        invalides = [
            "pas du json",
            "",
            None,                                                           # content vide
            "[]",                                                           # liste au lieu d'un objet
            '{"revenu_mensuel_net": 3000}',                                 # champ type absent
            '{"type": "bulletin_salaire", "revenu_mensuel_net": "3 000 €"}',  # mauvais type
            '{"type": "bulletin_salaire", "revenu_mensuel_net": -1}',       # hors borne
        ]
        for contenu in invalides:
            with self.subTest(contenu=contenu):
                mock_get_client.return_value.chat.completions.create.return_value = \
                    fausse_reponse_groq(contenu)
                self.assertEqual(self._post_bulletin().status_code, 502)

    @patch("scanner.llm.get_client")
    def test_choices_vide_donne_502(self, mock_get_client):
        completion = MagicMock()
        completion.choices = []
        mock_get_client.return_value.chat.completions.create.return_value = completion
        self.assertEqual(self._post_bulletin().status_code, 502)

    @patch("scanner.llm.get_client")
    def test_erreur_inattendue_502_sans_fuite_de_detail(self, mock_get_client):
        mock_get_client.return_value.chat.completions.create.side_effect = \
            Exception("secret-interne-xyz")
        res = self._post_bulletin()
        self.assertEqual(res.status_code, 502)
        self.assertNotIn("secret-interne-xyz", res.content.decode())

    @patch("scanner.llm.get_client")
    def test_les_logs_ne_contiennent_pas_les_valeurs_recues(self, mock_get_client):
        """RGPD : une réponse invalide ne doit pas écrire un nom de candidat dans les logs."""
        mock_get_client.return_value.chat.completions.create.return_value = fausse_reponse_groq(
            '{"type": "bulletin_salaire", "nom": "Marie Lambert", "revenu_mensuel_net": "abc"}'
        )
        with self.assertLogs("scanner.llm", level="WARNING") as logs:
            self.assertEqual(self._post_bulletin().status_code, 502)
        sortie = "\n".join(logs.output)
        self.assertNotIn("Marie", sortie)
        self.assertNotIn("abc", sortie)


class TestDossierAvecDocumentHorsSujet(SimpleTestCase):

    @patch("scanner.llm.get_client")
    def test_un_hors_sujet_est_ignore_sans_rejeter_le_dossier(self, mock_get_client):
        mock_get_client.return_value.chat.completions.create.side_effect = [
            fausse_reponse_groq('{"type": "bulletin_salaire", "nom": "Jean Dupont", "revenu_mensuel_net": 3300}'),
            fausse_reponse_groq('{"type": "autre", "nom": null, "revenu_mensuel_net": null}'),
        ]
        bulletin = SimpleUploadedFile("bulletin.pdf", creer_pdf_bytes(TEXTE_LONG))
        recette = SimpleUploadedFile("recette.pdf", creer_pdf_bytes("Recette des crepes pour quatre personnes"))
        res = APIClient().post(reverse("analyze-dossier"),
                               {"loyer": "1000", "documents": [bulletin, recette]}, format="multipart")
        self.assertEqual(res.status_code, 200)
        resume = res.data["resume"]
        self.assertEqual(resume["documents_ignores"], ["recette.pdf"])
        self.assertTrue(resume["couvre_3x_loyer"])
        self.assertTrue(any("ignorés" in a for a in resume["avertissements"]))
