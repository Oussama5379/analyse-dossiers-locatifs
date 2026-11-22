import re
import unicodedata

from .schemas import PieceExtraite

TYPES_REQUIS = ["piece_identite", "bulletin_salaire", "avis_imposition"]


def _mots_du_nom(nom: str) -> frozenset[str]:
    """Mots d'un nom, sans accents ni casse ni ponctuation, indépendamment de l'ordre.
    'LAMBERT Marie' == 'Marie Lambert' == 'Éric-Jean' -> {'eric', 'jean'}."""
    ascii_ = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return frozenset(re.findall(r"[a-z0-9]+", ascii_.lower()))


def _noms_compatibles(a: frozenset[str], b: frozenset[str]) -> bool:
    """Même personne si tous les mots de l'un figurent dans l'autre
    (une carte d'identité liste souvent plusieurs prénoms, un bulletin un seul)."""
    return a <= b or b <= a


def _noms_incoherents(noms: list[str]) -> bool:
    mots = [m for m in (_mots_du_nom(n) for n in noms) if m]
    return any(not _noms_compatibles(a, b) for i, a in enumerate(mots) for b in mots[i + 1:])


def construire_resume(pieces: list[tuple[str, PieceExtraite]], loyer: float) -> dict:
    """pieces = liste de (nom_fichier, PieceExtraite). Fonction pure, facile à tester."""
    reconnues = [(f, p) for f, p in pieces if p.type != "autre"]
    ignorees = [f for f, p in pieces if p.type == "autre"]

    types_trouves = {p.type for _, p in reconnues}
    manquants = [t for t in TYPES_REQUIS if t not in types_trouves]

    # Règle : revenu = moyenne des bulletins où un revenu a été extrait
    revenus = [
        p.revenu_mensuel_net for _, p in reconnues
        if p.type == "bulletin_salaire" and p.revenu_mensuel_net is not None
    ]
    revenu = round(sum(revenus) / len(revenus)) if revenus else None
    couvre = None if revenu is None else revenu >= 3 * loyer

    avertissements = []
    if revenu is None:
        avertissements.append("Aucun revenu n'a pu être extrait : couverture du loyer indéterminée.")
    if _noms_incoherents([p.nom for _, p in reconnues if p.nom]):
        avertissements.append("Les noms diffèrent d'un document à l'autre.")
    if ignorees:
        avertissements.append("Certains documents ont été ignorés (type non reconnu).")

    return {
        "candidat": next((p.nom for _, p in reconnues if p.nom), None),
        "revenus_mensuels": revenu,
        "loyer_vise": loyer,
        "couvre_3x_loyer": couvre,
        "documents_trouves": [{"fichier": f, "type": p.type} for f, p in reconnues],
        "documents_manquants": manquants,
        "documents_ignores": ignorees,
        "avertissements": avertissements,
    }
