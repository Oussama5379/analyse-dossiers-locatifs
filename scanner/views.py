import fitz  # PyMuPDF
import logging
import os
from openai import OpenAI, APITimeoutError, RateLimitError
from pydantic import BaseModel, Field, ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

client = OpenAI(
    api_key=os.environ.get("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
    timeout=30,
    max_retries=1,
)


class AnalyseLLM(BaseModel):
    """Schéma attendu de la réponse du LLM."""
    hors_sujet: bool = False
    candidat: str | None = None
    revenus_mensuels: int | None = Field(default=None, ge=0)
    documents_trouves: list[str] = []
    documents_manquants: list[str] = []


class AnalyzeDossierView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        loyer_vise_str = request.data.get('loyer')
        documents = request.FILES.getlist('documents')

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
            # ÉTAPE 1 : Extraction du texte des PDF
            dossier_text = ""
            for document in documents:
                doc_bytes = document.read()
                try:
                    pdf = fitz.open(stream=doc_bytes, filetype="pdf")
                except Exception:
                    return Response(
                        {"error": f"Le fichier '{document.name}' n'est pas un PDF valide."},
                        status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
                    )

                texte_extrait = ""
                for page in pdf:
                    texte_extrait += page.get_text()
                pdf.close()

                if len(texte_extrait.strip()) < 10:
                    return Response(
                        {"error": f"Le document '{document.name}' est illisible (scan sans OCR)."},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY
                    )

                dossier_text += f"\n--- Document : {document.name} ---\n{texte_extrait}\n"

            # ÉTAPE 2 : Appel au LLM
            prompt = f"""
            Tu es un expert immobilier. Analyse le texte extrait de ces documents locatifs :

            {dossier_text}

            Ta tâche :
            1. Vérifie si les documents sont pertinents (bulletin de salaire, pièce d'identité, contrat, etc.). Si le contenu est totalement hors sujet (ex: recette de cuisine, article de presse), mets "hors_sujet" à true.
            2. Extrais le nom complet du candidat.
            3. Extrais le revenu mensuel net (uniquement des nombres entiers, sans devise).
            4. Liste les documents trouvés (ex: ["carte d'identité", "bulletin de paie"]).
            5. Liste les documents manquants classiques pour une location.

            Tu dois OBLIGATOIREMENT répondre avec cet objet JSON strict :
            {{
                "hors_sujet": false,
                "candidat": "Nom Prénom",
                "revenus_mensuels": 3000,
                "documents_trouves": ["carte d'identité"],
                "documents_manquants": ["bulletin de paie"]
            }}
            """

            try:
                completion = client.chat.completions.create(
                    model=GROQ_MODEL,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "Tu es un assistant qui renvoie uniquement du JSON valide."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0
                )
                resultat_ia = AnalyseLLM.model_validate_json(
                    completion.choices[0].message.content
                )

            except APITimeoutError:
                logger.warning("Timeout de l'API LLM")
                return Response(
                    {"error": "L'IA n'a pas répondu à temps. Veuillez réessayer."},
                    status=status.HTTP_504_GATEWAY_TIMEOUT
                )
            except RateLimitError:
                logger.warning("Rate limit de l'API LLM")
                return Response(
                    {"error": "Service IA temporairement surchargé. Veuillez réessayer."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )
            except ValidationError:
                logger.exception("Réponse LLM invalide")
                return Response(
                    {"error": "L'IA a renvoyé une réponse invalide. Veuillez réessayer."},
                    status=status.HTTP_502_BAD_GATEWAY
                )
            except Exception:
                logger.exception("Erreur d'appel au LLM")
                return Response(
                    {"error": "Erreur de communication avec l'IA."},
                    status=status.HTTP_502_BAD_GATEWAY
                )

            # Document hors sujet
            if resultat_ia.hors_sujet:
                return Response(
                    {"error": "Le dossier a été refusé car les documents soumis ne sont pas des justificatifs locatifs valides."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ÉTAPE 3 : Règle métier
            revenus = resultat_ia.revenus_mensuels or 0
            couvre_3x_loyer = revenus >= (3 * loyer_vise)

            resume = {
                "candidat": resultat_ia.candidat,
                "revenus_mensuels": revenus,
                "loyer_vise": loyer_vise,
                "couvre_3x_loyer": couvre_3x_loyer,
                "documents_trouves": resultat_ia.documents_trouves,
                "documents_manquants": resultat_ia.documents_manquants,
            }

            return Response({"status": "success", "resume": resume}, status=status.HTTP_200_OK)

        except Exception:
            logger.exception("Erreur interne")
            return Response(
                {"error": "Erreur interne du serveur."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )