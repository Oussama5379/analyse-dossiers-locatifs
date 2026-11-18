from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status

class AnalyzeDossierView(APIView):
    # Permet à l'API d'accepter des fichiers 
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        # 1. Récupération des données envoyées par le client
        loyer_vise_str = request.data.get('loyer')
        documents = request.FILES.getlist('documents')

        # 2. Validation de base
        if not loyer_vise_str or not documents:
            return Response(
                {"error": "Le loyer et au moins un document (PDF) sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            loyer_vise = float(loyer_vise_str)
        except ValueError:
            return Response(
                {"error": "Le loyer doit être un nombre valide."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # TODO : Étape 1 - Parcourir les documents et extraire le texte avec PyMuPDF (fitz)
            # -> Gestion de l'erreur : PDF illisible ou vide
            
            # TODO : Étape 2 - Envoyer le texte au LLM avec un prompt précis
            # -> Gestion de l'erreur : Document hors sujet ou hallucination du LLM
            
            # TODO : Étape 3 - Règle métier : vérifier si les revenus >= 3 * loyer_vise

            # Réponse fictive en attendant l'implémentation de la logique
            return Response({
                "status": "success",
                "message": "Fichiers reçus avec succès. Logique IA à venir.",
                "loyer_vise": loyer_vise,
                "nombre_fichiers": len(documents)
            }, status=status.HTTP_200_OK)

        except Exception as e:
            # Gestion d'erreur globale au cas où le code plante de manière inattendue
            return Response(
                {"error": f"Erreur interne du serveur : {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )