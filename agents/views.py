from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AgentRun
from .services import run_prompt


class AgentRunView(APIView):
    """POST {"prompt": "..."} -> runs it through the LangChain chain.

    Every call is traced in LangSmith (see agents/services.py) and recorded
    locally as an AgentRun so the pipeline has an auditable history.
    """

    def post(self, request: Request) -> Response:
        prompt = request.data.get('prompt', '').strip()
        if not prompt:
            return Response({'detail': 'prompt is required'}, status=400)

        run = AgentRun.objects.create(prompt=prompt)
        try:
            result = run_prompt(prompt)
        except Exception as exc:
            run.status = AgentRun.Status.FAILED
            run.error = str(exc)
            run.save(update_fields=['status', 'error', 'updated_at'])
            return Response({'detail': 'agent run failed', 'error': str(exc)}, status=502)

        run.response = result.text
        run.status = AgentRun.Status.SUCCESS
        run.langsmith_run_id = result.langsmith_run_id or ''
        run.save(update_fields=['response', 'status', 'langsmith_run_id', 'updated_at'])

        return Response({
            'id': run.id,
            'response': result.text,
            'langsmith_run_id': result.langsmith_run_id,
        })
