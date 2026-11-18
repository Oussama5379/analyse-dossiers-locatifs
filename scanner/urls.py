from django.urls import path
from .views import AnalyzeDossierView

urlpatterns = [
    path('analyze/', AnalyzeDossierView.as_view(), name='analyze-dossier'),
]