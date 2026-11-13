#!/usr/bin/env python3
"""One-off helper: append UI refresh msgids to ar/messages.po (idempotent marker)."""
from pathlib import Path

MARKER = "# --- UI refresh (navigation + shared chrome) ---"
BLOCK = """
# --- UI refresh (navigation + shared chrome) ---
msgid "Work"
msgstr "العمل"

msgid "Discover"
msgstr "اكتشاف"

msgid "Dashboard"
msgstr "لوحة التحكم"

msgid "Email Words"
msgstr "كلمات البريد الإلكتروني"

msgid "File Management and Analysis"
msgstr "إدارة وتحليل الملفات"

msgid "Comprehensive analysis, classification, and insights"
msgstr "تحليل شامل وتصنيف ورؤى"

msgid "Search all data: files, categories, keywords, sources, sides, words, titles..."
msgstr "البحث في كل البيانات: ملفات، فئات، كلمات مفتاحية، مصادر، جوانب، كلمات، عناوين..."

msgid "Sort By"
msgstr "ترتيب حسب"

msgid "ID (Ascending)"
msgstr "المعرّف (تصاعدي)"

msgid "ID (Descending)"
msgstr "المعرّف (تنازلي)"

msgid "Name (A-Z)"
msgstr "الاسم (أ–ي)"

msgid "Name (Z-A)"
msgstr "الاسم (ي–أ)"

msgid "File Count (High to Low)"
msgstr "عدد الملفات (من الأعلى إلى الأدنى)"

msgid "File Count (Low to High)"
msgstr "عدد الملفات (من الأدنى إلى الأعلى)"

msgid "Relations"
msgstr "العلاقات"

msgid "Titles"
msgstr "العناوين"

msgid "Geolocation"
msgstr "المواقع الجغرافية"

msgid "View Original"
msgstr "عرض الأصل"

msgid "Export Extracted Text"
msgstr "تصدير النص المستخرج"

msgid "Download Original"
msgstr "تنزيل الأصل"

msgid "Original"
msgstr "الأصل"

msgid "Extracted"
msgstr "المستخرج"

msgid "Metadata & lineage"
msgstr "البيانات الوصفية والسلالة"

msgid "Analyst assignment"
msgstr "تعيين المحلل"

msgid "Assignment, search & file navigation"
msgstr "التعيين والبحث والتنقل بين الملفات"

msgid "In-document search"
msgstr "البحث داخل المستند"

msgid "Document analysis"
msgstr "تحليل المستند"

msgid "File Library"
msgstr "مكتبة الملفات"

msgid "Horizon & Signal Explorer"
msgstr "الأفق ومستكشف الإشارات"

msgid "Analyst Categories"
msgstr "فئات المحلل"

msgid "SYLTHARAE System"
msgstr "نظام SYLTHARAE"

msgid "Ingest"
msgstr "الاستيعاب"

msgid "Processing"
msgstr "المعالجة"

msgid "Analyze"
msgstr "التحليل"

msgid "Classify"
msgstr "التصنيف"

msgid "Report"
msgstr "التقارير"

msgid "Operate"
msgstr "التشغيل"

msgid "Administration"
msgstr "الإدارة"

msgid "Settings"
msgstr "الإعدادات"

msgid "Security"
msgstr "الأمان"

msgid "Search"
msgstr "بحث"

msgid "Sources"
msgstr "المصادر"

msgid "Sides"
msgstr "الجوانب"

msgid "Keywords"
msgstr "الكلمات المفتاحية"

msgid "Words"
msgstr "الكلمات"

msgid "Categories"
msgstr "الفئات"

msgid "Notifications"
msgstr "الإشعارات"

msgid "Monitoring"
msgstr "المراقبة"

msgid "Import Center"
msgstr "مركز الاستيراد"

msgid "Reports"
msgstr "التقارير"

msgid "Jobs"
msgstr "المهام"
"""

ROOT = Path(__file__).resolve().parents[1]
PO = ROOT / "translations" / "ar" / "LC_MESSAGES" / "messages.po"


def main() -> None:
    text = PO.read_text(encoding="utf-8")
    if MARKER in text:
        print("already present")
        return
    PO.write_text(text.rstrip() + "\n" + BLOCK + "\n", encoding="utf-8")
    print("appended")


if __name__ == "__main__":
    main()
