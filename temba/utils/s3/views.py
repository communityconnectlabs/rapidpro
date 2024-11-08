from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from temba.utils.s3 import private_file_storage


class PrivateFileCallbackView(View):
    """
    When we use the AWS S3 to send attachments, we send relative URLs which user later GETs to get the
    file content.
    """

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    @staticmethod
    def get(request, *args, **kwargs):
        if getattr(settings, "AWS_S3_ENABLED", False):
            try:
                return FileResponse(private_file_storage.open(kwargs["file_path"]))
            except FileNotFoundError:
                raise Http404
        else:
            try:
                return FileResponse(default_storage.open(kwargs["file_path"]))
            except FileNotFoundError:
                raise Http404
