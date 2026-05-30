from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404


def favicon_view(request):
    candidates = (
        settings.BASE_DIR / 'static' / 'images' / 'favicon.ico',
        settings.STATIC_ROOT / 'images' / 'favicon.ico',
    )
    for path in candidates:
        if path.is_file():
            return FileResponse(path.open('rb'), content_type='image/vnd.microsoft.icon')
    raise Http404
