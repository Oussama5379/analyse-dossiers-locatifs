import math

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .analysis import construire_resume
from .llm import analyser_piece, LLMConfiguration, LLMTimeout, LLMSurcharge, LLMReponseInvalide, LLMError
from .documents import extraire_texte, DocumentInvalide, DocumentIllisible
from .ocr import OcrIndisponible, OcrTimeout

MAX_FICHIERS = 6
MAX_TAILLE_FICHIER = 5 * 1024 * 1024  # 5 Mo

def _erreur(message, code):
    return Response({"error": message}, status=code)


class AnalyzeDossierView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        documents = request.FILES.getlist("documents")
        try:
            loyer = float(request.data.get("loyer", ""))
        except (ValueError, TypeError):
            return _erreur("Le loyer doit être un nombre valide.", status.HTTP_400_BAD_REQUEST)

        if not math.isfinite(loyer) or loyer <= 0:
            return _erreur("Le loyer doit être un nombre positif.", status.HTTP_400_BAD_REQUEST)
        
        if not documents:
            return _erreur("Au moins un document (PDF, PNG ou JPEG) est requis.", status.HTTP_400_BAD_REQUEST)
        
        if len(documents) > MAX_FICHIERS:
            return _erreur(
                f"Maximum {MAX_FICHIERS} documents autorisés par dossier.",
                status.HTTP_400_BAD_REQUEST,
            )

        # On pèse tous les fichiers avant d'en ouvrir un seul
        for doc in documents:
            if doc.size is not None and doc.size > MAX_TAILLE_FICHIER:
                return _erreur(
                    f"Le fichier '{doc.name}' dépasse 5 Mo.",
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )


        # Seulement si TOUS les fichiers ont passé les tests de base
        pieces = []
        try:
            for doc in documents:
                texte = extraire_texte(doc.name, doc.read())
                pieces.append((doc.name, analyser_piece(texte)))
                
        except DocumentInvalide as e:
            return _erreur(f"Le fichier '{e}' n'est pas un PDF ou une image (PNG, JPEG) valide.", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        except DocumentIllisible as e:
            return _erreur(f"Le document '{e}' est illisible (aucun texte exploitable, même après OCR, ou PDF protégé par mot de passe).", status.HTTP_422_UNPROCESSABLE_ENTITY)
        except OcrTimeout:
            return _erreur("La reconnaissance du texte a pris trop de temps.", status.HTTP_504_GATEWAY_TIMEOUT)
        except OcrIndisponible:
            return _erreur("Service de reconnaissance de texte indisponible.", status.HTTP_503_SERVICE_UNAVAILABLE)
        except LLMTimeout:
            return _erreur("L'IA n'a pas répondu à temps. Veuillez réessayer.", status.HTTP_504_GATEWAY_TIMEOUT)
        except LLMSurcharge:
            return _erreur("Service IA temporairement surchargé. Veuillez réessayer.", status.HTTP_503_SERVICE_UNAVAILABLE)
        except LLMReponseInvalide:
            return _erreur("L'IA a renvoyé une réponse invalide. Veuillez réessayer.", status.HTTP_502_BAD_GATEWAY)
        except LLMConfiguration:
            return _erreur("Service d'analyse IA indisponible.", status.HTTP_503_SERVICE_UNAVAILABLE)
        except LLMError:
            return _erreur("Erreur de communication avec l'IA.", status.HTTP_502_BAD_GATEWAY)

        if all(p.type == "autre" for _, p in pieces):
            return _erreur("Aucun document ne ressemble à un justificatif locatif.", status.HTTP_422_UNPROCESSABLE_ENTITY)

        return Response({"status": "success", "resume": construire_resume(pieces, loyer)})