import fitz

MAX_CHARS = 8000  # on tronque pour limiter la taille du prompt


class PdfInvalide(Exception):
    pass


class PdfIllisible(Exception):
    pass


def extraire_texte(nom: str, contenu: bytes) -> str:
    try:
        pdf = fitz.open(stream=contenu, filetype="pdf")
        try:
            texte = "".join(page.get_text() for page in pdf)
        finally:
            pdf.close()
    except Exception as e:
        raise PdfInvalide(nom) from e

    if len(texte.strip()) < 10:
        raise PdfIllisible(nom)  # scan sans OCR
    return texte[:MAX_CHARS]