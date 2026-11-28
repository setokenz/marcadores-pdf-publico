import pytest


@pytest.fixture(autouse=True)
def sin_intervalo_entre_envios(monkeypatch):
    """En las pruebas no se separan los envíos (salvo en la que lo comprueba)."""
    from marcadores import ocr_gemini

    monkeypatch.setattr(ocr_gemini, "INTERVALO_MINIMO", 0)
