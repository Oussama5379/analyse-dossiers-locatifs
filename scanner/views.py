import math

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .analysis import construire_resume
from .llm import analyser_piece, LLMTimeout, LLMSurcharge, LLMReponseInvalide, LLMError
from .pdf import extraire_texte, PdfInvalide, PdfIllisible


def _erreur(message, code):
    return Response({"error": message}, status=code)


class AnalyzeDossierView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        documents = request.FILES.getlist("documents")
        try:
            loyer = float(request.data.get("loyer", ""))
        except ValueError:
            return _erreur("Le loyer doit être un nombre valide.", status.HTTP_400_BAD_REQUEST)

        if not math.isfinite(loyer) or loyer <= 0:
            return _erreur("Le loyer doit être un nombre positif.", status.HTTP_400_BAD_REQUEST)
        if not documents:
            return _erreur("Au moins un document (PDF) est requis.", status.HTTP_400_BAD_REQUEST)

        pieces = []
        try:
            for doc in documents:
                texte = extraire_texte(doc.name, doc.read())
                pieces.append((doc.name, analyser_piece(texte)))
        except PdfInvalide as e:
            return _erreur(f"Le fichier '{e}' n'est pas un PDF valide.", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        except PdfIllisible as e:
            return _erreur(f"Le document '{e}' est illisible (scan sans OCR).", status.HTTP_422_UNPROCESSABLE_ENTITY)
        except LLMTimeout:
            return _erreur("L'IA n'a pas répondu à temps. Veuillez réessayer.", status.HTTP_504_GATEWAY_TIMEOUT)
        except LLMSurcharge:
            return _erreur("Service IA temporairement surchargé. Veuillez réessayer.", status.HTTP_503_SERVICE_UNAVAILABLE)
        except LLMReponseInvalide:
            return _erreur("L'IA a renvoyé une réponse invalide. Veuillez réessayer.", status.HTTP_502_BAD_GATEWAY)
        except LLMError:
            return _erreur("Erreur de communication avec l'IA.", status.HTTP_502_BAD_GATEWAY)

        if all(p.type == "autre" for _, p in pieces):
            return _erreur("Aucun document ne ressemble à un justificatif locatif.", status.HTTP_422_UNPROCESSABLE_ENTITY)

        return Response({"status": "success", "resume": construire_resume(pieces, loyer)})