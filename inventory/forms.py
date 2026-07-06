from django import forms
from django.conf import settings

from .models import ImportBatch
from .services.spreadsheet_loader import SUPPORTED_EXTENSIONS


class ExcelImportForm(forms.Form):
    IMPORT_TYPE_CHOICES = ImportBatch.IMPORT_TYPE_CHOICES

    import_type = forms.ChoiceField(choices=IMPORT_TYPE_CHOICES)
    excel_file = forms.FileField(required=True)

    def clean_excel_file(self):
        excel_file = self.cleaned_data['excel_file']
        name = (excel_file.name or '').lower()
        if not name.endswith(SUPPORTED_EXTENSIONS):
            allowed = ', '.join(SUPPORTED_EXTENSIONS)
            raise forms.ValidationError(f'Only {allowed} files are allowed.')
        max_size = getattr(settings, 'IMPORT_MAX_UPLOAD_SIZE_BYTES', 5 * 1024 * 1024 * 1024)
        if excel_file.size > max_size:
            max_gb = getattr(settings, 'IMPORT_MAX_UPLOAD_SIZE_GB', 5)
            raise forms.ValidationError(f'File size must not exceed {max_gb} GB.')
        return excel_file
