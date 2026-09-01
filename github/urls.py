from django.urls import path

from .views import GitHubWebhookView

app_name = 'github'

urlpatterns = [
    path('webhook/', GitHubWebhookView.as_view(), name='webhook'),
]
