# Guide de test de l'API

Ce guide permet de vérifier l'API à la main avec les documents fictifs du dossier `samples/`, puis de lancer les tests automatiques.

- **Partie 3** (cas d'erreur) et **partie 5** (tests automatiques) fonctionnent **sans clé Groq** et ne consomment aucun quota. Commencer par là.
- **Parties 2 et 4** appellent le LLM : il faut une clé `GROQ_API_KEY` valide dans `.env`.

## Avant de commencer

1. Démarrer l'API (voir le README) : `docker compose up --build` ou `python manage.py runserver`.
2. Ouvrir un terminal **à la racine du projet** (les chemins `samples/...` sont relatifs).
3. Conventions des commandes ci-dessous :
   - `-H "Accept: application/json; indent=4"` demande au serveur un **JSON indenté**, lisible champ par champ.
   - `-w "\nHTTP %{http_code}\n"` affiche le code de retour après la réponse.
   - Sous PowerShell, écrire `curl.exe` à la place de `curl` (`curl` y est un alias d'une autre commande).

Exemple de sortie lisible :

```text
{
    "status": "success",
    "resume": {
        "candidat": "LAMBERT Marie",
        "revenus_mensuels": 3200,
        "loyer_vise": 900.0,
        "couvre_3x_loyer": true,
        ...
    }
}
HTTP 200
```

Vérification rapide que le serveur répond (le code `400` est normal : il manque les paramètres) :

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/
```

## 1. Les documents de test

Tous les documents sont **fictifs** (candidate : Marie Lambert, net avant impôt : **3 200 EUR**). Ils se régénèrent avec `python samples/generate_samples.py`.

| Fichier                | Contenu                                                 | Ce qu'il teste                                        |
| :--------------------- | :------------------------------------------------------ | :---------------------------------------------------- |
| `bulletin_salaire.pdf` | Bulletin de paie, PDF avec texte                        | Chemin normal, sans OCR                               |
| `bulletin_scan.png`    | Le même bulletin en image                               | OCR sur une image                                     |
| `bulletin_scan.pdf`    | Le même bulletin, PDF sans texte (scan)                 | Repli OCR sur un PDF                                  |
| `bulletin_photo.jpg`   | Le même bulletin « photographié » (penché, flou, ombré) | OCR sur une image dégradée                            |
| `piece_identite.pdf`   | Pièce d'identité fictive                                | Reconnaissance d'une identité                         |
| `avis_imposition.pdf`  | Avis d'impôt fictif                                     | Reconnaissance d'un avis d'imposition                 |
| `dossier_mixte.pdf`    | Page 1 : identité (texte) ; page 2 : bulletin (scan)    | Extraction de texte natif **et** OCR dans un même PDF |
| `recette_crepes.pdf`   | Recette de cuisine                                      | Document hors sujet                                   |
| `pdf_protege.pdf`      | Bulletin protégé par mot de passe                       | PDF chiffré                                           |
| `page_blanche.png`     | Image blanche                                           | Document sans texte                                   |
| `faux_fichier.png`     | Texte déguisé en `.png`                                 | Format invalide                                       |

## 2. Scénarios nominaux (LLM requis)

Le nom peut s'écrire différemment selon le modèle et le document (« Marie LAMBERT », « LAMBERT Marie »...) : c'est normal, et cela ne doit pas produire d'avertissement. Le reste est attendu à l'identique.

### 2.1 Dossier complet, revenus suffisants

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/piece_identite.pdf" -F "documents=@samples/bulletin_scan.png" -F "documents=@samples/avis_imposition.pdf"
```

**Attendu** : `200`, `revenus_mensuels: 3200`, `couvre_3x_loyer: true` (3 200 ≥ 3 × 900), `documents_manquants: []`, trois pièces dans `documents_trouves` (`piece_identite`, `bulletin_salaire`, `avis_imposition`), `avertissements: []`.

### 2.2 Même dossier, loyer trop élevé

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=1200" -F "documents=@samples/piece_identite.pdf" -F "documents=@samples/bulletin_scan.png" -F "documents=@samples/avis_imposition.pdf"
```

**Attendu** : `200`, `couvre_3x_loyer: false` (3 200 < 3 × 1 200 = 3 600). Le reste est identique à 2.1.

### 2.3 Le même bulletin sous quatre formes

Envoyer chacun de ces fichiers **seul** :

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/bulletin_salaire.pdf"
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/bulletin_scan.png"
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/bulletin_scan.pdf"
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/bulletin_photo.jpg"
```

**Attendu pour chacun** : `200`, `bulletin_salaire`, `revenus_mensuels: 3200`, `couvre_3x_loyer: true`, `documents_manquants: ["piece_identite", "avis_imposition"]`.

Si le résultat diffère d'un format à l'autre, c'est l'OCR qui a mal lu : c'est le but du test. Une vraie photo de téléphone sera plus difficile que `bulletin_photo.jpg` ; elle peut légitimement donner `422` si elle est illisible.

### 2.4 Dossier avec un document hors sujet

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/piece_identite.pdf" -F "documents=@samples/bulletin_scan.png" -F "documents=@samples/avis_imposition.pdf" -F "documents=@samples/recette_crepes.pdf"
```

**Attendu** : `200` (le dossier n'est **pas** rejeté), `documents_ignores: ["recette_crepes.pdf"]`, un avertissement « Certains documents ont été ignorés ».

### 2.5 Dossier sans bulletin de salaire

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/piece_identite.pdf" -F "documents=@samples/avis_imposition.pdf"
```

**Attendu** : `200`, `revenus_mensuels: null`, **`couvre_3x_loyer: null`** (inconnu, pas `false`), `documents_manquants: ["bulletin_salaire"]`, un avertissement « couverture du loyer indéterminée ».

### 2.6 Uniquement un document hors sujet

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/recette_crepes.pdf"
```

**Attendu** : `422`, « Aucun document ne ressemble à un justificatif locatif. »

### 2.7 PDF mixte (texte + scan)

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/dossier_mixte.pdf"
```

**Attendu** : `200`. Ce fichier est **un seul document contenant deux pièces** : le LLM n'en renvoie qu'une, donc le type retenu peut être `bulletin_salaire` ou `piece_identite`. Ce test vérifie que l'extraction (texte natif page 1, OCR page 2) réussit, pas la classification. Une extraction réussie se voit si le revenu 3 200 est trouvé.

## 3. Cas d'erreur (sans LLM, sans quota)

Ces erreurs sont détectées **avant** tout appel à Groq : elles fonctionnent même sans clé.

| Commande (après `curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/`) | Attendu                                                                                                 |
| :------------------------------------------------------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------------------------------ |
| `-F "documents=@samples/bulletin_salaire.pdf"` (loyer absent)                                                                         | `400` « Le loyer doit être un nombre valide. »                                                          |
| `-F "loyer=abc" -F "documents=@samples/bulletin_salaire.pdf"`                                                                         | `400` « Le loyer doit être un nombre valide. »                                                          |
| `-F "loyer=0" -F "documents=@samples/bulletin_salaire.pdf"`                                                                           | `400` « Le loyer doit être un nombre positif. »                                                         |
| `-F "loyer=-5" -F "documents=@samples/bulletin_salaire.pdf"` (idem avec `nan`)                                                        | `400` « Le loyer doit être un nombre positif. »                                                         |
| `-F "loyer=900"` (aucun fichier)                                                                                                      | `400` « Au moins un document (PDF, PNG ou JPEG) est requis. »                                           |
| `-F "loyer=900" -F "documents=@samples/faux_fichier.png"`                                                                             | `415` « ... n'est pas un PDF ou une image (PNG, JPEG) valide. »                                         |
| `-F "loyer=900" -F "documents=@samples/page_blanche.png"`                                                                             | `422` « ... est illisible (aucun texte exploitable, même après OCR, ou PDF protégé par mot de passe). » |
| `-F "loyer=900" -F "documents=@samples/pdf_protege.pdf"`                                                                              | `422` (même message)                                                                                    |

L'envoi du fichier `page_blanche.png` lance réellement l'OCR : si vous obtenez `503` au lieu de `422`, Tesseract ou la langue `fra` est absent (voir le dépannage).

### Trop de fichiers (7 au lieu de 6 maximum)

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/bulletin_salaire.pdf" -F "documents=@samples/bulletin_salaire.pdf" -F "documents=@samples/bulletin_salaire.pdf" -F "documents=@samples/bulletin_salaire.pdf" -F "documents=@samples/bulletin_salaire.pdf" -F "documents=@samples/bulletin_salaire.pdf" -F "documents=@samples/bulletin_salaire.pdf"
```

**Attendu** : `400` « Maximum 6 documents autorisés par dossier. »

### Fichier de plus de 5 Mo

Créer un faux fichier de 6 Mo (il n'a pas besoin d'être un vrai PDF : la taille est contrôlée en premier), l'envoyer, puis le supprimer :

```bash
python -c "open('gros.pdf','wb').write(b'0'*(6*1024*1024))"
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@gros.pdf"
```

**Attendu** : `413` « Le fichier 'gros.pdf' dépasse 5 Mo. » Supprimer ensuite `gros.pdf` (`del gros.pdf` sous Windows, `rm gros.pdf` sous Linux/macOS) et ne pas le commiter.

## 4. Pannes d'infrastructure (à simuler en local)

Modifier le `.env`, **redémarrer le serveur**, tester, puis remettre le `.env` d'origine.

**Clé Groq manquante** : renommer ou vider `GROQ_API_KEY`, puis envoyer `bulletin_salaire.pdf` :

```bash
curl -w "\nHTTP %{http_code}\n" -H "Accept: application/json; indent=4" -X POST http://127.0.0.1:8000/api/analyze/ -F "loyer=900" -F "documents=@samples/bulletin_salaire.pdf"
```

**Attendu** : `503` « Service d'analyse IA indisponible. » (le serveur démarre quand même).

**Tesseract introuvable** : mettre une valeur fausse à `TESSERACT_CMD` (par exemple `C:\inexistant\tesseract.exe`), puis envoyer `bulletin_scan.png` :

**Attendu** : `503` « Service de reconnaissance de texte indisponible. » Un PDF avec texte (`bulletin_salaire.pdf`) continue de fonctionner, car il n'utilise pas l'OCR.

**Quota Groq dépassé** : l'offre gratuite limite le débit (notamment en tokens par minute). Si plusieurs dossiers sont enchaînés très vite, une réponse `503` « temporairement surchargé » est normale : attendre une minute.

Les timeouts (`504`) et les réponses invalides du LLM (`502`) sont difficiles à provoquer à la main : ils sont couverts par les tests automatiques.

## 5. Tests automatiques

```bash
python manage.py test                                # en local
docker compose run --rm api python manage.py test    # dans Docker
```

Tous les tests doivent afficher `OK`. Ils ne nécessitent **ni clé Groq, ni réseau, ni Tesseract** (le LLM et l'OCR sont simulés ; un test d'OCR réel est ignoré automatiquement si Tesseract est absent). Pour un seul fichier :

```bash
python manage.py test scanner.tests.test_ocr
```

## 6. Dépannage

| Symptôme                                              | Cause probable                                                                                                                                                                                                                    |
| :---------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `curl: (26) Failed to open/read local data from file` | Commande lancée hors de la racine du projet, ou fichier de `samples/` absent. Régénérer avec `python samples/generate_samples.py`.                                                                                                |
| Code `000`                                            | Le serveur n'est pas démarré, ou mauvais port.                                                                                                                                                                                    |
| `503` sur `page_blanche.png` ou un scan               | Tesseract ou la langue `fra` manquant. Vérifier : `tesseract --list-langs` (doit afficher `fra` et `eng`). En local sous Windows, vérifier `TESSERACT_CMD` dans `.env`. Dans Docker, cette variable ne doit **pas** être définie. |
| `503` « Service d'analyse IA indisponible »           | `GROQ_API_KEY` absente ou vide dans `.env` (avec Docker, relancer `docker compose up` après modification).                                                                                                                        |
| `503` « temporairement surchargé »                    | Quota Groq atteint : attendre une minute.                                                                                                                                                                                         |
| `502` répété                                          | Le modèle a renvoyé une réponse inexploitable ; réessayer. Vérifier que `GROQ_MODEL` existe encore dans l'offre gratuite de Groq.                                                                                                 |
| `curl` ne fait rien d'utile sous PowerShell           | Utiliser `curl.exe`.                                                                                                                                                                                                              |

Avec un client graphique (Postman, Insomnia) : requête `POST` en `form-data`, champ `loyer` de type _Text_, champ `documents` de type _File_ (à répéter pour plusieurs fichiers).
