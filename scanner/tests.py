from django.test import SimpleTestCase
from scanner.schemas import PieceExtraite
from scanner.analysis import construire_resume
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

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