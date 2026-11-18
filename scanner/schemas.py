from typing import Literal
from pydantic import BaseModel, Field


class PieceExtraite(BaseModel):
    type: Literal["bulletin_salaire", "avis_imposition", "piece_identite", "autre"]
    nom: str | None = None
    revenu_mensuel_net: int | None = Field(default=None, ge=0)