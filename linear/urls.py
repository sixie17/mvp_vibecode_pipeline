from django.urls import path

from .views import LinearWebhookView

app_name = 'linear'

urlpatterns = [
    path('webhook/', LinearWebhookView.as_view(), name='webhook'),
]
