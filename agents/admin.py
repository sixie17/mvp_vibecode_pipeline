from django.contrib import admin

from .models import AgentRun


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'created_at', 'langsmith_run_id')
    list_filter = ('status',)
    readonly_fields = ('created_at', 'updated_at')
