"""Manual analyst i18n overrides (merged with obsolete .po entries at fill time)."""

# pylint: disable=line-too-long

EXTRA: dict[str, dict[str, str]] = {
    "ar": {
        "Apply Filters": "تطبيق عوامل التصفية",
    },
    "he": {
        "Apply Filters": "החל מסננים",
        'Delete analyst category "{name}"? Its assignments are removed and affected files return to uncategorized status.': (
            'למחוק את קטגוריית האנליסט "{name}"? ההקצאות יוסרו והקבצים יחזרו ללא קטגוריה.'
        ),
    },
    "fa": {
        "Apply Filters": "اعمال فیلترها",
        'Delete analyst category "{name}"? Its assignments are removed and affected files return to uncategorized status.': (
            'دسته تحلیلگر «{name}» حذف شود؟ تخصیص‌ها حذف و فایل‌ها بدون دسته می‌شوند.'
        ),
    },
    "hr": {
        "Apply Filters": "Primijeni filtre",
    },
}
