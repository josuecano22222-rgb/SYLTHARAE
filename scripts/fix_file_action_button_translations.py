"""One-shot: clear fuzzy flags and set vetted msgstr for file-action icon tooltips."""

from __future__ import annotations

from pathlib import Path

from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parent.parent
MSGIDS = [
    "Analyze selected files",
    "View categories, category terms, and keywords for the selection",
    "Export Content",
    "Export the original files of the selection as one ZIP",
    "Export Originals",
    "Export selected filenames to CSV",
    "Filenames CSV",
    "Export selected filenames to Excel",
    "Filenames Excel",
    "Choose a folder (or create a subfolder) for extracted text",
    "Choose a folder (or create a subfolder) for original files",
    "Rename…",
    "Open the original file in its native application",
    "Locate the containing folder",
    "Locate",
    "Details…",
    "Loaded names",
    "Delete selected files",
    "Filter by one or more file types",
    "File Actions",
    "Save As",
    "Open in native application",
    "Locate folder",
    "Analytical Report",
]

TRANSLATIONS: dict[str, dict[str, str]] = {
    "ar": {
        "Analyze selected files": "تحليل الملفات المحددة",
        "View categories, category terms, and keywords for the selection": "عرض الفئات ومصطلحات الفئات والكلمات المفتاحية للملفات المحددة",
        "Export Content": "تصدير المحتوى",
        "Export the original files of the selection as one ZIP": "تصدير الملفات الأصلية للتحديد كملف ZIP واحد",
        "Export Originals": "تصدير الملفات الأصلية",
        "Export selected filenames to CSV": "تصدير أسماء الملفات المحددة إلى CSV",
        "Filenames CSV": "أسماء الملفات (CSV)",
        "Export selected filenames to Excel": "تصدير أسماء الملفات المحددة إلى Excel",
        "Filenames Excel": "أسماء الملفات (Excel)",
        "Choose a folder (or create a subfolder) for extracted text": "اختر مجلدًا (أو أنشئ مجلدًا فرعيًا) للنص المستخرج",
        "Choose a folder (or create a subfolder) for original files": "اختر مجلدًا (أو أنشئ مجلدًا فرعيًا) للملفات الأصلية",
        "Rename…": "إعادة تسمية…",
        "Open the original file in its native application": "فتح الملف الأصلي في تطبيقه الأصلي",
        "Locate the containing folder": "تحديد المجلد الحاوي",
        "Locate": "تحديد",
        "Details…": "التفاصيل…",
        "Loaded names": "الأسماء المحمّلة",
        "Delete selected files": "حذف الملفات المحددة",
        "Filter by one or more file types": "تصفية حسب نوع ملف واحد أو أكثر",
        "File Actions": "إجراءات الملف",
        "Save As": "حفظ باسم",
        "Open in native application": "فتح في التطبيق الأصلي",
        "Locate folder": "تحديد المجلد",
        "Analytical Report": "تقرير تحليلي",
    },
    "he": {
        "Analyze selected files": "ניתוח הקבצים שנבחרו",
        "View categories, category terms, and keywords for the selection": "הצג קטגוריות, מונחי קטגוריה ומילות מפתח עבור הבחירה",
        "Export Content": "ייצוא תוכן",
        "Export the original files of the selection as one ZIP": "ייצוא הקבצים המקוריים של הבחירה כקובץ ZIP אחד",
        "Export Originals": "ייצוא מקוריים",
        "Export selected filenames to CSV": "ייצוא שמות הקבצים שנבחרו ל-CSV",
        "Filenames CSV": "שמות קבצים (CSV)",
        "Export selected filenames to Excel": "ייצוא שמות הקבצים שנבחרו ל-Excel",
        "Filenames Excel": "שמות קבצים (Excel)",
        "Choose a folder (or create a subfolder) for extracted text": "בחר תיקייה (או צור תיקיית משנה) לטקסט שחולץ",
        "Choose a folder (or create a subfolder) for original files": "בחר תיקייה (או צור תיקיית משנה) לקבצים המקוריים",
        "Rename…": "שינוי שם…",
        "Open the original file in its native application": "פתח את הקובץ המקורי ביישום המקורי שלו",
        "Locate the containing folder": "אתר את התיקייה המכילה",
        "Locate": "אתר",
        "Details…": "פרטים…",
        "Loaded names": "שמות שנטענו",
        "Delete selected files": "מחק קבצים שנבחרו",
        "Filter by one or more file types": "סנן לפי סוג קובץ אחד או יותר",
        "File Actions": "פעולות קובץ",
        "Save As": "שמור בשם",
        "Open in native application": "פתיחה ביישום המקורי",
        "Locate folder": "אתר תיקייה",
        "Analytical Report": "דוח אנליטי",
    },
    "fa": {
        "Analyze selected files": "تحلیل فایل‌های انتخاب‌شده",
        "View categories, category terms, and keywords for the selection": "نمایش دسته‌ها، اصطلاحات دسته و کلیدواژه‌ها برای انتخاب",
        "Export Content": "صدور محتوا",
        "Export the original files of the selection as one ZIP": "صدور فایل‌های اصلی انتخاب‌شده در یک ZIP",
        "Export Originals": "صدور فایل‌های اصلی",
        "Export selected filenames to CSV": "صدور نام فایل‌های انتخاب‌شده به CSV",
        "Filenames CSV": "نام فایل‌ها (CSV)",
        "Export selected filenames to Excel": "صدور نام فایل‌های انتخاب‌شده به Excel",
        "Filenames Excel": "نام فایل‌ها (Excel)",
        "Choose a folder (or create a subfolder) for extracted text": "پوشه‌ای (یا زیرپوشه) برای متن استخراج‌شده انتخاب یا بسازید",
        "Choose a folder (or create a subfolder) for original files": "پوشه‌ای (یا زیرپوشه) برای فایل‌های اصلی انتخاب یا بسازید",
        "Rename…": "تغییر نام…",
        "Open the original file in its native application": "باز کردن فایل اصلی در برنامهٔ بومی آن",
        "Locate the containing folder": "یافتن پوشهٔ حاوی",
        "Locate": "یافتن",
        "Details…": "جزئیات…",
        "Loaded names": "نام‌های بارگذاری‌شده",
        "Delete selected files": "حذف فایل‌های انتخاب‌شده",
        "Filter by one or more file types": "فیلتر بر اساس یک یا چند نوع فایل",
        "File Actions": "اقدامات فایل",
        "Save As": "ذخیره به نام",
        "Open in native application": "باز کردن در برنامهٔ بومی",
        "Locate folder": "یافتن پوشه",
        "Analytical Report": "گزارش تحلیلی",
    },
    "hr": {
        "Analyze selected files": "Analiziraj odabrane datoteke",
        "View categories, category terms, and keywords for the selection": "Prikaži kategorije, pojmove kategorija i ključne riječi za odabir",
        "Export Content": "Izvezi sadržaj",
        "Export the original files of the selection as one ZIP": "Izvezi izvorne datoteke odabira u jedan ZIP",
        "Export Originals": "Izvezi izvornike",
        "Export selected filenames to CSV": "Izvezi nazive odabranih datoteka u CSV",
        "Filenames CSV": "Nazivi datoteka (CSV)",
        "Export selected filenames to Excel": "Izvezi nazive odabranih datoteka u Excel",
        "Filenames Excel": "Nazivi datoteka (Excel)",
        "Choose a folder (or create a subfolder) for extracted text": "Odaberi mapu (ili stvori podmapu) za izdvojeni tekst",
        "Choose a folder (or create a subfolder) for original files": "Odaberi mapu (ili stvori podmapu) za izvorne datoteke",
        "Rename…": "Preimenuj…",
        "Open the original file in its native application": "Otvori izvornu datoteku u izvornoj aplikaciji",
        "Locate the containing folder": "Pronađi sadržavajuću mapu",
        "Locate": "Pronađi",
        "Details…": "Detalji…",
        "Loaded names": "Učitani nazivi",
        "Delete selected files": "Izbriši odabrane datoteke",
        "Filter by one or more file types": "Filtriraj prema jednoj ili više vrsta datoteka",
        "File Actions": "Radnje datoteke",
        "Save As": "Spremi kao",
        "Open in native application": "Otvori u izvornoj aplikaciji",
        "Locate folder": "Pronađi mapu",
        "Analytical Report": "Analitičko izvješće",
    },
}


def main() -> None:
    for lang, fixes in TRANSLATIONS.items():
        path = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
        with path.open("rb") as f:
            catalog = read_po(f, locale=lang)
        for msgid, msgstr in fixes.items():
            msg = catalog.get(msgid)
            if msg is None:
                raise SystemExit(f"[{lang}] missing msgid: {msgid}")
            msg.string = msgstr
            msg.flags.discard("fuzzy")
        with path.open("wb") as f:
            write_po(f, catalog, width=88)
        print(f"updated {lang}")


if __name__ == "__main__":
    main()
