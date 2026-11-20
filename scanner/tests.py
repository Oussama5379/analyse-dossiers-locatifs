from django.test import SimpleTestCase
from scanner.schemas import PieceExtraite
from scanner.analysis import construire_resume

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