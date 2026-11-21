# Analyse de dossiers locatifs

API REST (Django + Django REST Framework) qui analyse les pièces d'un dossier de location (pièce d'identité, bulletins de salaire, avis d'imposition) et renvoie un résumé : ce qui a été trouvé, ce qui manque, et si les revenus couvrent environ 3 fois le loyer.

**Pipeline** : texte natif du PDF (PyMuPDF) → repli OCR (Tesseract) pour les scans et les photos → **un appel LLM (Groq) par document** pour classer la pièce et extraire nom et revenu → règles métier en Python pur.

---

## Lancer le projet

### Avec Docker (recommandé)

Prérequis : Docker et une clé Groq gratuite (<https://console.groq.com>). L'image embarque Python 3.12 et Tesseract avec les langues `fra` et `eng`.

```bash
cp .env.example .env          # Windows (cmd) : copy .env.example .env
# puis renseigner GROQ_API_KEY dans .env
docker compose up --build
```

L'API écoute sur `http://localhost:8000/api/analyze/`.

### Sans Docker

Prérequis : Python 3.12+ et Tesseract avec les langues `fra` et `eng`.

- Windows : installateur de Tesseract (cocher _French_ dans les données de langue)
- Debian/Ubuntu : `sudo apt install tesseract-ocr tesseract-ocr-fra`
- macOS : `brew install tesseract tesseract-lang`

```bash
python -m venv venv
# Windows : venv\Scripts\activate    |    Linux/macOS : source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Windows (cmd) : copy .env.example .env
python manage.py runserver
```

### Variables d'environnement (`.env`)

| Variable        | Rôle                                                                                                                     | Défaut               |
| :-------------- | :----------------------------------------------------------------------------------------------------------------------- | :------------------- |
| `GROQ_API_KEY`  | Clé API Groq (**requise**)                                                                                               | —                    |
| `GROQ_MODEL`    | Modèle utilisé                                                                                                           | `openai/gpt-oss-20b` |
| `OCR_LANGUES`   | Langues Tesseract                                                                                                        | `fra+eng`            |
| `TESSERACT_CMD` | Chemin de l'exécutable, seulement si Tesseract n'est pas dans le `PATH` (ex. Windows). **À ne pas définir dans Docker.** | —                    |

---

## Essayer l'API

Le dossier `samples/` contient des documents **fictifs** (PDF avec texte, scan en PNG, photo en JPEG, PDF mixte, et des cas d'erreur).

```bash
curl -X POST http://localhost:8000/api/analyze/ \
  -F "loyer=900" \
  -F "documents=@samples/piece_identite.pdf" \
  -F "documents=@samples/bulletin_scan.png" \
  -F "documents=@samples/avis_imposition.pdf"
```

_(Sous PowerShell, utiliser `curl.exe` : `curl` y est un alias d'une autre commande.)_

Exemple de réponse `200` (le nom exact dépend du modèle) :

```json
{
  "status": "success",
  "resume": {
    "candidat": "Marie LAMBERT",
    "revenus_mensuels": 3200,
    "loyer_vise": 900.0,
    "couvre_3x_loyer": true,
    "documents_trouves": [
      { "fichier": "piece_identite.pdf", "type": "piece_identite" },
      { "fichier": "bulletin_scan.png", "type": "bulletin_salaire" },
      { "fichier": "avis_imposition.pdf", "type": "avis_imposition" }
    ],
    "documents_manquants": [],
    "documents_ignores": [],
    "avertissements": []
  }
}
```

---

## API

### `POST /api/analyze/` (`multipart/form-data`)

- **`loyer`** (nombre, requis) : loyer mensuel visé, strictement positif.
- **`documents`** (fichiers, requis) : de 1 à 6 fichiers **PDF, PNG ou JPEG**, 5 Mo maximum chacun. Les PDF peuvent être natifs ou scannés.

### Règles métier

- **Documents manquants** : calculés en code (identité, bulletin de salaire, avis d'imposition), jamais par le LLM.
- **Revenu mensuel** : moyenne arrondie des revenus extraits des bulletins de salaire.
- **`couvre_3x_loyer`** : `true` si `revenus_mensuels >= 3 × loyer`. **`null` si aucun revenu n'a pu être extrait** (on ne sait pas, ce n'est pas un « non »), avec un avertissement.
- **Avertissements** : revenu introuvable, noms différents entre les documents, documents ignorés.
- **Document hors sujet** : ignoré et listé dans `documents_ignores`. Le dossier n'est rejeté (422) que si _aucun_ document n'est reconnu.

### Codes de retour

| Code  | Signification                                                                                                    |
| :---- | :--------------------------------------------------------------------------------------------------------------- |
| `200` | Analyse réussie.                                                                                                 |
| `400` | Loyer absent, invalide ou négatif ; aucun fichier ; plus de 6 fichiers.                                          |
| `413` | Un fichier dépasse 5 Mo.                                                                                         |
| `415` | Fichier qui n'est ni un PDF ni une image PNG/JPEG valide (ou corrompu).                                          |
| `422` | Document illisible (scan sans texte même après OCR, PDF protégé par mot de passe) ou aucun justificatif reconnu. |
| `502` | Réponse invalide du modèle (JSON incorrect, hors schéma, vide) ou autre erreur de l'API IA.                      |
| `503` | Service indisponible : clé API manquante, quota Groq atteint, Tesseract ou langue `fra` absents.                 |
| `504` | Délai dépassé (OCR ou appel IA).                                                                                 |

---

## Choix techniques

- **Un appel LLM par document**, pas un seul sur le dossier concaténé : on sait quelle pièce est quoi, un document hors sujet n'invalide pas les autres, et la sortie est plus simple à valider.
- **Le LLM classe et extrait, le code décide.** Les règles (pièces requises, moyenne, seuil 3×) sont déterministes et testables sans réseau.
- **Groq + `openai/gpt-oss-20b`** via le SDK OpenAI : offre gratuite, rapide, suffisant pour classer et extraire. Le modèle est configurable (`GROQ_MODEL`).
- **Sortie du LLM validée avec Pydantic** (type de pièce dans une liste fermée, revenu entier borné). Rien n'est utilisé tel quel.
- **OCR en repli seulement** : le texte natif est utilisé d'abord ; Tesseract (`fra+eng`) ne traite que les pages quasi vides (maximum 5 pages par PDF). Les images sont réorientées (EXIF), mises en niveaux de gris et réduites à 3500 px.
- **Bornes partout** : 6 fichiers, 5 Mo, 40 millions de pixels par image, 8000 caractères envoyés au LLM par document, timeouts de 30 s sur l'OCR et l'appel IA.
- **Prompt défensif** : le texte du document est délimité et présenté comme une donnée, pas comme une instruction. Cela réduit l'injection de prompt sans l'éliminer.
- **Aucun document n'est stocké** par l'application, et les logs ne contiennent pas le contenu des documents (les erreurs de validation du LLM ne journalisent que le champ et le type d'erreur).

## Gestion des erreurs : les cas traités

1. **Document illisible ou hors sujet** : un scan sans texte passe par l'OCR ; s'il reste vide → `422`. Un PDF chiffré → `422`, un fichier qui n'est pas un PDF ou une image → `415`. Un document hors sujet est ignoré et signalé.
2. **LLM indisponible** : timeout → `504`, quota dépassé ou clé manquante → `503`, autre erreur → `502`. Le détail interne n'est jamais renvoyé au client.
3. **LLM qui renvoie n'importe quoi** : JSON malformé, mauvais type, valeur hors bornes, contenu vide ou liste de choix vide → `502`, jamais une erreur `500`.

L'OCR a ses propres erreurs : Tesseract absent ou langue manquante → `503`, délai dépassé → `504`.

---

## Tests

```bash
python manage.py test                                # en local
docker compose run --rm api python manage.py test    # dans Docker
```

Les tests ne nécessitent **ni clé Groq, ni réseau, ni Tesseract** : le LLM et l'OCR sont mockés (un test d'OCR réel est ignoré automatiquement si Tesseract est absent). Ils couvrent les règles métier, la traduction des erreurs Tesseract, l'extraction (texte natif, scan, PDF mixte), les réponses invalides du LLM et les codes HTTP de l'endpoint.

## Limites connues et pistes

- **L'OCR peut mal lire un chiffre sans lever d'erreur** (3 400 lu 3 480). Piste : vérifier que le montant extrait figure bien dans le texte source et signaler les montants issus de l'OCR.
- **Appels LLM séquentiels** : n documents = n appels. Piste : parallélisation ou traitement asynchrone.
- **Règles simplifiées** : la moyenne des bulletins ne vérifie ni la période ni la personne (un garant serait mélangé au candidat), et la moyenne est arrondie avant la comparaison avec le seuil. La comparaison des noms est basique (casse uniquement, pas d'accents ni d'inversion nom/prénom).
- **Formats** : PDF, PNG et JPEG uniquement (pas HEIC, WebP ni TIFF) ; pas de redressement des photos penchées ni d'écriture manuscrite.
- **Données personnelles** : le texte des pièces d'identité et des bulletins est envoyé à un service tiers (Groq). En production, il faudrait un accord de traitement, un hébergement adapté ou un modèle local (point RGPD).
- **Pas d'authentification ni de limitation de débit**, et `runserver` n'est pas un serveur de production. Une limite de taille de requête devrait aussi être configurée sur le serveur web.
- **Évaluation de la qualité d'extraction** : pas de jeu de tests réels annotés pour mesurer la précision du modèle.

---

## Structure

```text
├── backend/                # Configuration Django
├── scanner/
│   ├── views.py            # Endpoint, validation des entrées, traduction des erreurs en codes HTTP
│   ├── documents.py        # Détection du format, texte natif (PyMuPDF), repli OCR
│   ├── ocr.py              # Prétraitement d'image et wrapper Tesseract
│   ├── llm.py              # Client Groq, prompt, validation de la réponse
│   ├── schemas.py          # Schéma Pydantic d'une pièce
│   ├── analysis.py         # Règles métier pures
│   └── tests/              # helpers, test_analysis, test_ocr, test_documents, test_api
├── samples/                # Documents fictifs et script de génération
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── requirements.txt
```
