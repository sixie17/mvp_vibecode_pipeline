from django.urls import path

from .views import AgentRunView

app_name = 'agents'

urlpatterns = [
    path('run/', AgentRunView.as_view(), name='run'),
]
