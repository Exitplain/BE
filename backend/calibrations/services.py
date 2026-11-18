from .models import CalibrationRevision


def current(project):
    """현재 보정 (revision, params). 한 번도 적용하지 않았으면 (0, {}) = 기본 모형."""
    latest = CalibrationRevision.objects.filter(project=project).order_by("-revision").first()
    return (latest.revision, latest.params) if latest else (0, {})
