from .schemas import PieceExtraite

TYPES_REQUIS = ["piece_identite", "bulletin_salaire", "avis_imposition"]


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
    noms = {p.nom.strip().lower() for _, p in reconnues if p.nom}
    if len(noms) > 1:
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