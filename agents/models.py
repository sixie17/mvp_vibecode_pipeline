from django.db import models


class AgentRun(models.Model):
    """A single invocation of a LangChain agent/chain.

    Kept lightweight on purpose — the detailed step-by-step trace lives in
    LangSmith (see `langsmith_run_id`); this row is just a local record of
    what was asked and what came back.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    prompt = models.TextField()
    response = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)
    langsmith_run_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'AgentRun({self.id}, {self.status})'
