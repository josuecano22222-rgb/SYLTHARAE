#!/usr/bin/env python3
"""Put every LEXIS Cosmos string into the locale catalogs.

The interface is one page plus one script, and it ships a lot of words: the
canvas, the sidebar, the viewer, the exports, the Manage sheet and the
keyboard-help dialog. Two things must be true for that page to be usable in
Arabic, Hebrew, Persian and Croatian, and this script enforces both:

1. **Completeness.** ``ROWS`` below holds one line per string with all four
   translations. ``check()`` re-derives the msgids from the template and the
   page script and refuses to run when a string has no row - a string added to
   the page without a translation fails here instead of shipping in English.
2. **No empty catalog entry.** Every entry written is a real translation, and
   the fuzzy flag is cleared, so ``tests/integration/test_translation_coverage``
   (which refuses an empty or fuzzy entry anywhere in a catalog) keeps passing.

The English catalog gets the source string as its own translation, the other
catalogs get their row, and the compiled ``.mo`` files are rebuilt afterwards -
gettext loads the ``.mo``, so a stale one would defeat the ``.po``.

Usage:
    python scripts/fill_lexis_cosmos_i18n.py           # apply and compile
    python scripts/fill_lexis_cosmos_i18n.py --check   # verify only
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set, Tuple

from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANGS = ("en", "ar", "he", "fa", "hr")
#: The catalogs that must actually differ from English (English is the source).
MAINTAINED = ("ar", "he", "fa", "hr")
TEMPLATE = ROOT / "templates" / "Lexis" / "cosmos.html"
SCRIPT = ROOT / "static" / "js" / "pages" / "lexis-cosmos-page.js"
#: The reference stamped on every appended entry, so a translator can see where
#: the string lives without running an extraction first.
REFERENCE = (
    "core/interfaces/registry.py templates/Lexis/cosmos.html "
    "static/js/pages/lexis-cosmos-page.js"
)

#: The registry entry itself (name, description) - the sidebar label and the
#: tooltip an operator reads before opening the page.
REGISTRY_ROWS: Tuple[Tuple[str, str, str, str, str], ...] = (
    (
        "LEXIS Cosmos",
        "لكسيس كوزموس",
        "לקסיס קוסמוס",
        "لکسیس کازموس",
        "LEXIS Kozmos",
    ),
    (
        "The taxonomy galaxy",
        "مجرة التصنيف",
        "גלקסיית הטקסונומיה",
        "کهکشان طبقه‌بندی",
        "Galaksija taksonomije",
    ),
    (
        "This term already lives in %(path)s. Add it here as well?",
        "هذا المصطلح موجود بالفعل في %(path)s. هل تريد إضافته هنا أيضًا؟",
        "המונח הזה כבר נמצא ב־%(path)s. להוסיף אותו גם כאן?",
        "این اصطلاح از قبل در %(path)s وجود دارد. آن را اینجا هم بیفزایید؟",
        "Ovaj pojam već postoji u %(path)s. Dodati ga i ovdje?",
    ),
    (
        "The taxonomy galaxy: root, word and keyword lenses, categories, "
        "subcategories and terms, with the file stack under each term, the "
        "scoped search sidebar, the document viewer and the taxonomy manager.",
        "مجرة التصنيف: الجذر وعدسات الكلمات والكلمات المفتاحية والفئات والفئات الفرعية "
        "والمصطلحات، مع رزمة الملفات تحت كل مصطلح، وشريط البحث المحدود النطاق، "
        "وعارض المستندات، ومدير التصنيف.",
        "גלקסיית הטקסונומיה: השורש, עדשות המילים ומילות המפתח, קטגוריות, "
        "תת־קטגוריות ומונחים, עם ערימת הקבצים תחת כל מונח, סרגל החיפוש המוגבל "
        "בהיקף, מציג המסמכים ומנהל הטקסונומיה.",
        "کهکشان طبقه‌بندی: ریشه، لنز واژه‌ها و کلیدواژه‌ها، دسته‌ها، زیردسته‌ها و "
        "اصطلاحات، همراه با پشته فایل‌های هر اصطلاح، نوار جستجوی محدود به دامنه، "
        "نمایشگر سند و مدیر طبقه‌بندی.",
        "Galaksija taksonomije: korijen, leće riječi i ključnih riječi, kategorije, "
        "potkategorije i pojmovi, s hrpom datoteka pod svakim pojmom, ograđenom "
        "pretragom u bočnoj traci, preglednikom dokumenata i upraviteljem taksonomije.",
    ),
)

#: english, arabic, hebrew, persian, croatian
ROWS: Tuple[Tuple[str, str, str, str, str], ...] = (
    ("Add a file record", "إضافة سجل ملف", "הוספת רשומת קובץ", "افزودن رکورد فایل", "Dodaj zapis datoteke"),
    ("Add category", "إضافة فئة", "הוספת קטגוריה", "افزودن دسته", "Dodaj kategoriju"),
    ("Add file", "إضافة ملف", "הוספת קובץ", "افزودن فایل", "Dodaj datoteku"),
    ("Add keyword", "إضافة كلمة مفتاحية", "הוספת מילת מפתח", "افزودن کلیدواژه", "Dodaj ključnu riječ"),
    ("Add subcategory", "إضافة فئة فرعية", "הוספת תת־קטגוריה", "افزودن زیردسته", "Dodaj potkategoriju"),
    ("Add term", "إضافة مصطلح", "הוספת מונח", "افزودن اصطلاح", "Dodaj pojam"),
    ("Add to basket", "إضافة إلى السلة", "הוספה לסל", "افزودن به سبد", "Dodaj u košaricu"),
    ("All file actions", "كل إجراءات الملفات", "כל פעולות הקובץ", "همه کنش‌های فایل", "Sve radnje s datotekom"),
    ("All file types", "كل أنواع الملفات", "כל סוגי הקבצים", "همه انواع فایل", "Sve vrste datoteka"),
    ("Apply this view", "تطبيق هذا العرض", "החלת תצוגה זו", "اعمال این نما", "Primijeni ovaj prikaz"),
    ("Basket", "السلة", "סל", "سبد", "Košarica"),
    ("Canvas tools", "أدوات اللوحة", "כלי הקנבס", "ابزارهای بوم", "Alati platna"),
    ("Categories, terms and keywords", "الفئات والمصطلحات والكلمات المفتاحية", "קטגוריות, מונחים ומילות מפתח", "دسته‌ها، اصطلاحات و کلیدواژه‌ها", "Kategorije, pojmovi i ključne riječi"),
    ("Categories, terms and keywords (CSV)", "الفئات والمصطلحات والكلمات المفتاحية (CSV)", "קטגוריות, מונחים ומילות מפתח (CSV)", "دسته‌ها، اصطلاحات و کلیدواژه‌ها (CSV)", "Kategorije, pojmovi i ključne riječi (CSV)"),
    ("Category created", "تم إنشاء الفئة", "הקטגוריה נוצרה", "دسته ایجاد شد", "Kategorija je stvorena"),
    ("Category deleted", "تم حذف الفئة", "הקטגוריה נמחקה", "دسته حذف شد", "Kategorija je izbrisana"),
    ("Category renamed", "تمت إعادة تسمية الفئة", "שם הקטגוריה שונה", "نام دسته تغییر کرد", "Kategorija je preimenovana"),
    ("Centre on the focus (L)", "التوسيط على موضع التركيز (L)", "מרכוז על המיקוד (L)", "وسط‌چین کردن روی کانون (L)", "Centriraj na fokus (L)"),
    ("Centre on the focused node", "التوسيط على العقدة المركّز عليها", "מרכוז על הצומת שבמיקוד", "وسط‌چین کردن روی گره کانونی", "Centriraj na fokusirani čvor"),
    ("Change the files", "تغيير الملفات", "שינוי הקבצים", "تغییر فایل‌ها", "Promijeni datoteke"),
    ("Clear dates", "مسح التواريخ", "ניקוי תאריכים", "پاک کردن تاریخ‌ها", "Očisti datume"),
    ("Clear file types", "مسح أنواع الملفات", "ניקוי סוגי קבצים", "پاک کردن انواع فایل", "Očisti vrste datoteka"),
    ("Clear side values", "مسح قيم الجوانب", "ניקוי ערכי הצד", "پاک کردن مقادیر طرف", "Očisti vrijednosti strane"),
    ("Clear sources", "مسح المصادر", "ניקוי מקורות", "پاک کردن منابع", "Očisti izvore"),
    ("Clear the basket", "إفراغ السلة", "ריקון הסל", "خالی کردن سبد", "Isprazni košaricu"),
    (
        "Click a file to open it · Shift-click for a range · Ctrl/⌘-click to toggle · ✎ on a card to edit it.",
        "انقر على ملف لفتحه · Shift-انقر لتحديد نطاق · Ctrl/⌘-انقر للتبديل · ✎ على البطاقة للتحرير.",
        "לחיצה על קובץ פותחת אותו · Shift־לחיצה בוחרת טווח · Ctrl/⌘־לחיצה מחליפה · ✎ על כרטיס לפתיחת עריכה.",
        "برای باز کردن فایل روی آن کلیک کنید · با Shift کلیک کنید تا بازه انتخاب شود · با Ctrl/⌘ کلیک کنید تا وضعیت تغییر کند · ✎ روی کارت برای ویرایش.",
        "Kliknite datoteku da je otvorite · Shift-klik za raspon · Ctrl/⌘-klik za prebacivanje · ✎ na kartici za uređivanje.",
    ),
    ("Close the deepest open level", "إغلاق أعمق مستوى مفتوح", "סגירת הרמה הפתוחה העמוקה ביותר", "بستن عمیق‌ترین سطح باز", "Zatvori najdublju otvorenu razinu"),
    ("Colour (optional)", "اللون (اختياري)", "צבע (אופציונלי)", "رنگ (اختیاری)", "Boja (neobavezno)"),
    ("Confirm", "تأكيد", "אישור", "تأیید", "Potvrdi"),
    ("Contents", "المحتويات", "תכנים", "محتواها", "Sadržaji"),
    ("Cosmos", "كوزموس", "קוסמוס", "کازموس", "Kozmos"),
    ("Cycle the depth", "تدوير العمق", "מעבר בין עומקים", "چرخش عمق", "Kruži dubinu"),
    ("Date range", "نطاق التاريخ", "טווח תאריכים", "بازه تاریخ", "Raspon datuma"),
    ("Delete files…", "حذف الملفات…", "מחיקת קבצים…", "حذف فایل‌ها…", "Izbriši datoteke…"),
    (
        "Delete the selected files? This cannot be undone.",
        "حذف الملفات المحددة؟ لا يمكن التراجع عن ذلك.",
        "למחוק את הקבצים שנבחרו? לא ניתן לבטל פעולה זו.",
        "فایل‌های انتخاب‌شده حذف شوند؟ این کار قابل بازگشت نیست.",
        "Izbrisati odabrane datoteke? Ovo se ne može poništiti.",
    ),
    (
        "Delete this category? Terms are kept, only the category is removed.",
        "حذف هذه الفئة؟ تبقى المصطلحات ويُزال هذا التصنيف فقط.",
        "למחוק את הקטגוריה? המונחים נשמרים, רק הקטגוריה מוסרת.",
        "این دسته حذف شود؟ اصطلاحات باقی می‌مانند و فقط دسته حذف می‌شود.",
        "Izbrisati ovu kategoriju? Pojmovi ostaju, uklanja se samo kategorija.",
    ),
    ("Delete this saved view?", "حذف هذا العرض المحفوظ؟", "למחוק את התצוגה השמורה?", "این نمای ذخیره‌شده حذف شود؟", "Izbrisati ovaj spremljeni prikaz?"),
    ("Depth", "العمق", "עומק", "عمق", "Dubina"),
    ("Direct terms", "المصطلحات المباشرة", "מונחים ישירים", "اصطلاحات مستقیم", "Izravni pojmovi"),
    ("Download the whole structure as Markdown", "تنزيل البنية كاملة بصيغة Markdown", "הורדת כל המבנה כ־Markdown", "بارگیری کل ساختار به‌صورت Markdown", "Preuzmi cijelu strukturu kao Markdown"),
    ("Drag to resize the sidebar · double-click to reset", "اسحب لتغيير حجم الشريط الجانبي · انقر نقرًا مزدوجًا لإعادة الضبط", "גרירה לשינוי רוחב הסרגל · לחיצה כפולה לאיפוס", "برای تغییر اندازه نوار کناری بکشید · برای بازنشانی دوبار کلیک کنید", "Povucite za promjenu širine trake · dvoklik za vraćanje"),
    ("Duplicate file", "تكرار الملف", "שכפול קובץ", "تکثیر فایل", "Dupliciraj datoteku"),
    ("Edit file", "تحرير الملف", "עריכת קובץ", "ویرایش فایل", "Uredi datoteku"),
    ("Edit keyword", "تحرير الكلمة المفتاحية", "עריכת מילת מפתח", "ویرایش کلیدواژه", "Uredi ključnu riječ"),
    ("Edit term", "تحرير المصطلح", "עריכת מונח", "ویرایش اصطلاح", "Uredi pojam"),
    ("Empty items", "العناصر الفارغة", "פריטים ריקים", "موارد خالی", "Prazne stavke"),
    ("Empty the basket", "إفراغ السلة", "ריקון הסל", "خالی کردن سبد", "Isprazni košaricu"),
    ("Every file is matched by at least one term.", "كل ملف مطابق بمصطلح واحد على الأقل.", "כל קובץ מותאם ללפחות מונח אחד.", "هر فایل با دست‌کم یک اصطلاح تطبیق داده شده است.", "Svaka datoteka odgovara barem jednom pojmu."),
    ("Every file that carries a taxonomy term.", "كل ملف يحمل مصطلحًا من التصنيف.", "כל קובץ הנושא מונח טקסונומיה.", "هر فایلی که یک اصطلاح طبقه‌بندی دارد.", "Svaka datoteka koja nosi pojam taksonomije."),
    ("Every term matches at least one file.", "كل مصطلح يطابق ملفًا واحدًا على الأقل.", "כל מונח מותאם ללפחות קובץ אחד.", "هر اصطلاح با دست‌کم یک فایل تطبیق دارد.", "Svaki pojam odgovara barem jednoj datoteci."),
    ("Everything in the current view", "كل ما في العرض الحالي", "כל מה שבתצוגה הנוכחית", "همه چیز در نمای کنونی", "Sve u trenutačnom prikazu"),
    ("Export the scope", "تصدير النطاق", "ייצוא ההיקף", "خروجی دامنه", "Izvezi opseg"),
    ("Exported", "تم التصدير", "יוצא", "خروجی گرفته شد", "Izvezeno"),
    ("Export…", "تصدير…", "ייצוא…", "خروجی…", "Izvezi…"),
    ("Extracted text bundle", "حزمة النص المستخرج", "חבילת הטקסט שחולץ", "بسته متن استخراج‌شده", "Paket izdvojenog teksta"),
    ("File added", "تمت إضافة الملف", "הקובץ נוסף", "فایل افزوده شد", "Datoteka je dodana"),
    ("File duplicated", "تم تكرار الملف", "הקובץ שוכפל", "فایل تکثیر شد", "Datoteka je duplicirana"),
    ("File record", "سجل الملف", "רשומת קובץ", "رکورد فایل", "Zapis datoteke"),
    ("File renamed", "تمت إعادة تسمية الملف", "שם הקובץ שונה", "نام فایل تغییر کرد", "Datoteka je preimenovana"),
    ("Files and details", "الملفات والتفاصيل", "קבצים ופרטים", "فایل‌ها و جزئیات", "Datoteke i pojedinosti"),
    ("Files and details (CSV)", "الملفات والتفاصيل (CSV)", "קבצים ופרטים (CSV)", "فایل‌ها و جزئیات (CSV)", "Datoteke i pojedinosti (CSV)"),
    ("Files by side", "الملفات حسب الجانب", "קבצים לפי צד", "فایل‌ها بر اساس طرف", "Datoteke po strani"),
    ("Files by type", "الملفات حسب النوع", "קבצים לפי סוג", "فایل‌ها بر اساس نوع", "Datoteke po vrsti"),
    ("Files deleted", "تم حذف الملفات", "הקבצים נמחקו", "فایل‌ها حذف شدند", "Datoteke su izbrisane"),
    ("Files moved", "تم نقل الملفات", "הקבצים הועברו", "فایل‌ها منتقل شدند", "Datoteke su premještene"),
    ("Files no term matches", "ملفات لا يطابقها أي مصطلح", "קבצים שאינם מותאמים לשום מונח", "فایل‌هایی که هیچ اصطلاحی با آن‌ها تطبیق ندارد", "Datoteke bez ijednog pojma"),
    ("Filter values", "تصفية القيم", "סינון ערכים", "فیلتر مقادیر", "Filtriraj vrijednosti"),
    ("Filter values…", "تصفية القيم…", "סינון ערכים…", "فیلتر مقادیر…", "Filtriraj vrijednosti…"),
    ("Find a term", "البحث عن مصطلح", "חיפוש מונח", "یافتن اصطلاح", "Nađi pojam"),
    ("Find a term…  ( / )", "البحث عن مصطلح…  ( / )", "חיפוש מונח…  ( / )", "یافتن اصطلاح…  ( / )", "Nađi pojam…  ( / )"),
    ("First page", "الصفحة الأولى", "העמוד הראשון", "صفحه نخست", "Prva stranica"),
    ("Fit", "ملاءمة", "התאמה", "جای‌گیری", "Prilagodi"),
    ("Fit the structure to the screen", "ملاءمة البنية إلى الشاشة", "התאמת המבנה למסך", "جای‌گیری ساختار در صفحه", "Prilagodi strukturu zaslonu"),
    ("Fit to screen (F)", "ملاءمة الشاشة (F)", "התאמה למסך (F)", "جای‌گیری در صفحه (F)", "Prilagodi zaslonu (F)"),
    ("Focus", "التركيز", "מיקוד", "کانون", "Fokus"),
    ("Focus the term finder", "الانتقال إلى باحث المصطلحات", "מיקוד מחפש המונחים", "رفتن به جستجوی اصطلاح", "Fokusiraj tražilicu pojmova"),
    ("From date", "من تاريخ", "מתאריך", "از تاریخ", "Od datuma"),
    ("Full screen (Shift+F)", "ملء الشاشة (Shift+F)", "מסך מלא (Shift+F)", "تمام‌صفحه (Shift+F)", "Cijeli zaslon (Shift+F)"),
    ("Galaxy", "المجرة", "גלקסיה", "کهکشان", "Galaksija"),
    ("Generated", "أُنشئ", "נוצר", "ایجادشده", "Generirano"),
    ("Hash", "البصمة", "גיבוב", "هش", "Otisak"),
    ("Hide filters", "إخفاء عوامل التصفية", "הסתרת מסננים", "پنهان کردن فیلترها", "Sakrij filtre"),
    ("Hits", "مرات الظهور", "התאמות", "تطابق‌ها", "Pogoci"),
    ("Keyboard shortcuts", "اختصارات لوحة المفاتيح", "קיצורי מקלדת", "میان‌برهای صفحه‌کلید", "Tipkovnički prečaci"),
    ("Keyboard shortcuts (?)", "اختصارات لوحة المفاتيح (؟)", "קיצורי מקלדת (?)", "میان‌برهای صفحه‌کلید (؟)", "Tipkovnički prečaci (?)"),
    ("Keyword added", "تمت إضافة الكلمة المفتاحية", "מילת המפתח נוספה", "کلیدواژه افزوده شد", "Ključna riječ je dodana"),
    ("Keyword expression", "تعبير الكلمة المفتاحية", "ביטוי מילת מפתח", "عبارت کلیدواژه", "Izraz ključne riječi"),
    ("Keyword expressions", "تعبيرات الكلمات المفتاحية", "ביטויי מילות מפתח", "عبارت‌های کلیدواژه", "Izrazi ključnih riječi"),
    ("Keyword galaxy", "مجرة الكلمات المفتاحية", "גלקסיית מילות המפתח", "کهکشان کلیدواژه‌ها", "Galaksija ključnih riječi"),
    ("Keyword removed", "تمت إزالة الكلمة المفتاحية", "מילת המפתח הוסרה", "کلیدواژه حذف شد", "Ključna riječ je uklonjena"),
    ("Keyword updated", "تم تحديث الكلمة المفتاحية", "מילת המפתח עודכנה", "کلیدواژه به‌روزرسانی شد", "Ključna riječ je ažurirana"),
    ("LEXIS", "لكسيس", "לקסיס", "لکسیس", "LEXIS"),
    ("LEXIS Cosmos", "لكسيس كوزموس", "לקסיס קוסמוס", "لکسیس کازموس", "LEXIS Cosmos"),
    ("LEXIS taxonomy", "تصنيف لكسيس", "טקסונומיית לקסיס", "طبقه‌بندی لکسیس", "LEXIS taksonomija"),
    ("Last page", "الصفحة الأخيرة", "העמוד האחרון", "صفحه آخر", "Posljednja stranica"),
    ("Lens", "العدسة", "עדשה", "لنز", "Leća"),
    ("Level", "مستوى", "רמה", "سطح", "Razina"),
    ("Lexicon", "المعجم", "לקסיקון", "واژه‌نامه", "Leksikon"),
    ("Library", "المكتبة", "ספרייה", "کتابخانه", "Knjižnica"),
    ("Main category", "الفئة الرئيسية", "קטגוריה ראשית", "دسته اصلی", "Glavna kategorija"),
    ("Manage taxonomy, files and views (M)", "إدارة التصنيف والملفات والعروض (M)", "ניהול טקסונומיה, קבצים ותצוגות (M)", "مدیریت طبقه‌بندی، فایل‌ها و نماها (M)", "Upravljaj taksonomijom, datotekama i prikazima (M)"),
    ("Matched", "المطابقات", "התאמות", "تطبیق‌ها", "Podudaranja"),
    ("Matching terms", "المصطلحات المطابقة", "מונחים מתאימים", "اصطلاحات منطبق", "Odgovarajući pojmovi"),
    ("Move to side", "النقل إلى جانب", "העברה לצד", "انتقال به طرف", "Premjesti na stranu"),
    ("Move to side…", "النقل إلى جانب…", "העברה לצד…", "انتقال به طرف…", "Premjesti na stranu…"),
    ("New keyword", "كلمة مفتاحية جديدة", "מילת מפתח חדשה", "کلیدواژه جدید", "Nova ključna riječ"),
    ("New main category", "فئة رئيسية جديدة", "קטגוריה ראשית חדשה", "دسته اصلی جدید", "Nova glavna kategorija"),
    ("New subcategory", "فئة فرعية جديدة", "תת־קטגוריה חדשה", "زیردسته جدید", "Nova potkategorija"),
    ("New word term", "مصطلح كلمة جديد", "מונח מילולי חדש", "اصطلاح واژه جدید", "Novi pojam riječi"),
    ("Next page", "الصفحة التالية", "העמוד הבא", "صفحه بعد", "Sljedeća stranica"),
    ("No activity has been recorded yet.", "لم يُسجَّل أي نشاط بعد.", "לא תועדה פעילות עדיין.", "هنوز هیچ فعالیتی ثبت نشده است.", "Aktivnost još nije zabilježena."),
    ("No files in this scope. Expand the structure or clear the filters.", "لا ملفات في هذا النطاق. وسّع البنية أو امسح عوامل التصفية.", "אין קבצים בהיקף זה. הרחיבו את המבנה או נקו את המסננים.", "در این دامنه فایلی نیست. ساختار را باز کنید یا فیلترها را پاک کنید.", "Nema datoteka u ovom opsegu. Proširite strukturu ili očistite filtre."),
    ("No files yet.", "لا ملفات بعد.", "אין עדיין קבצים.", "هنوز فایلی وجود ندارد.", "Još nema datoteka."),
    ("No matching terms.", "لا مصطلحات مطابقة.", "אין מונחים מתאימים.", "اصطلاح منطبقی نیست.", "Nema odgovarajućih pojmova."),
    ("No saved views yet.", "لا عروض محفوظة بعد.", "אין עדיין תצוגות שמורות.", "هنوز نمای ذخیره‌شده‌ای نیست.", "Još nema spremljenih prikaza."),
    ("No selection", "لا تحديد", "אין בחירה", "انتخابی نیست", "Nema odabira"),
    ("No sides are recorded yet.", "لم تُسجَّل جوانب بعد.", "לא תועדו צדדים עדיין.", "هنوز طرفی ثبت نشده است.", "Još nema zabilježenih strana."),
    ("No term matched this file.", "لم يطابق أي مصطلح هذا الملف.", "אף מונח לא תאם קובץ זה.", "هیچ اصطلاحی با این فایل تطبیق نداشت.", "Nijedan pojam ne odgovara ovoj datoteci."),
    ("No terms in the basket.", "لا مصطلحات في السلة.", "אין מונחים בסל.", "هیچ اصطلاحی در سبد نیست.", "Nema pojmova u košarici."),
    ("No terms in this subcategory.", "لا مصطلحات في هذه الفئة الفرعية.", "אין מונחים בתת־קטגוריה זו.", "هیچ اصطلاحی در این زیردسته نیست.", "Nema pojmova u ovoj potkategoriji."),
    ("No text was stored for this file.", "لم يُخزَّن نص لهذا الملف.", "לא נשמר טקסט לקובץ זה.", "متنی برای این فایل ذخیره نشده است.", "Za ovu datoteku nije spremljen tekst."),
    ("Nothing to export in this scope.", "لا شيء للتصدير في هذا النطاق.", "אין מה לייצא בהיקף זה.", "چیزی برای خروجی در این دامنه نیست.", "Nema što izvesti u ovom opsegu."),
    ("Nothing to open", "لا شيء لفتحه", "אין מה לפתוח", "چیزی برای باز کردن نیست", "Nema što otvoriti"),
    ("Nothing to redo", "لا شيء للإعادته", "אין מה לבצע מחדש", "چیزی برای تکرار نیست", "Nema što ponoviti"),
    ("Nothing to undo", "لا شيء للتراجع عنه", "אין מה לבטל", "چیزی برای بازگردانی نیست", "Nema što poništiti"),
    ("Only the selection", "التحديد فقط", "הבחירה בלבד", "فقط انتخاب‌شده‌ها", "Samo odabir"),
    ("Open Manage", "فتح الإدارة", "פתיחת ניהול", "باز کردن مدیریت", "Otvori upravljanje"),
    ("Open in the viewer", "الفتح في العارض", "פתיחה במציג", "باز کردن در نمایشگر", "Otvori u pregledniku"),
    ("Open one more level", "فتح مستوى إضافي", "פתיחת רמה נוספת", "باز کردن یک سطح بیشتر", "Otvori još jednu razinu"),
    ("Open the chart to a depth (D cycles)", "فتح المخطط إلى عمق معين (D للتدوير)", "פתיחת התרשים לעומק (D למעבר)", "باز کردن نمودار تا یک عمق (D برای چرخش)", "Otvori grafikon do dubine (D za kruženje)"),
    ("Original paths", "المسارات الأصلية", "נתיבים מקוריים", "مسیرهای اصلی", "Izvorni putovi"),
    ("Original paths (CSV)", "المسارات الأصلية (CSV)", "נתיבים מקוריים (CSV)", "مسیرهای اصلی (CSV)", "Izvorni putovi (CSV)"),
    ("Outline", "المخطط العام", "מתאר", "نمای کلی", "Skica"),
    ("Outline downloaded", "تم تنزيل المخطط العام", "המתאר הורד", "نمای کلی بارگیری شد", "Skica je preuzeta"),
    ("Overview and data health", "نظرة عامة وصحة البيانات", "סקירה ובריאות הנתונים", "نمای کلی و سلامت داده‌ها", "Pregled i zdravlje podataka"),
    ("Per page", "لكل صفحة", "לעמוד", "در هر صفحه", "Po stranici"),
    ("Please confirm", "يرجى التأكيد", "נא לאשר", "لطفاً تأیید کنید", "Potvrdite"),
    ("Previous page", "الصفحة السابقة", "העמוד הקודם", "صفحه قبل", "Prethodna stranica"),
    ("Print the structure", "طباعة البنية", "הדפסת המבנה", "چاپ ساختار", "Ispiši strukturu"),
    ("Print the taxonomy with the file count of every term. The browser print dialog opens next.", "اطبع التصنيف مع عدد ملفات كل مصطلح. ستُفتح بعد ذلك نافذة طباعة المتصفح.", "הדפסת הטקסונומיה עם מספר הקבצים של כל מונח. לאחר מכן ייפתח חלון ההדפסה של הדפדפן.", "طبقه‌بندی را همراه با تعداد فایل‌های هر اصطلاح چاپ کنید. سپس پنجره چاپ مرورگر باز می‌شود.", "Ispišite taksonomiju s brojem datoteka svakog pojma. Zatim se otvara dijalog za ispis preglednika."),
    ("Redo", "إعادة", "ביצוע מחדש", "تکرار", "Ponovi"),
    ("Redo the change", "إعادة التغيير", "ביצוע השינוי מחדש", "تکرار تغییر", "Ponovi promjenu"),
    ("Redone", "تمت الإعادة", "בוצע מחדש", "تکرار شد", "Ponovljeno"),
    ("Reload", "إعادة التحميل", "רענון", "بارگیری مجدد", "Ponovno učitaj"),
    ("Remove from basket", "الإزالة من السلة", "הסרה מהסל", "حذف از سبد", "Ukloni iz košarice"),
    ("Remove this filter", "إزالة هذا المرشح", "הסרת המסנן", "حذف این فیلتر", "Ukloni ovaj filtar"),
    ("Remove this term from the subcategory?", "إزالة هذا المصطلح من الفئة الفرعية؟", "להסיר מונח זה מתת־הקטגוריה?", "این اصطلاح از زیردسته حذف شود؟", "Ukloniti ovaj pojam iz potkategorije?"),
    ("Rename category", "إعادة تسمية الفئة", "שינוי שם קטגוריה", "تغییر نام دسته", "Preimenuj kategoriju"),
    ("Rename file", "إعادة تسمية الملف", "שינוי שם קובץ", "تغییر نام فایل", "Preimenuj datoteku"),
    ("Rename file…", "إعادة تسمية الملف…", "שינוי שם קובץ…", "تغییر نام فایل…", "Preimenuj datoteku…"),
    ("Rename view", "إعادة تسمية العرض", "שינוי שם תצוגה", "تغییر نام نما", "Preimenuj prikaz"),
    ("Request failed", "فشل الطلب", "הבקשה נכשלה", "درخواست ناموفق بود", "Zahtjev nije uspio"),
    ("Reset the zoom", "إعادة ضبط التكبير", "איפוס הזום", "بازنشانی بزرگ‌نمایی", "Vrati zum"),
    ("Reset to 100% (0)", "إعادة الضبط إلى 100% (0)", "איפוס ל־100% (0)", "بازنشانی به ۱۰۰٪ (۰)", "Vrati na 100% (0)"),
    ("Save the current view", "حفظ العرض الحالي", "שמירת התצוגה הנוכחית", "ذخیره نمای کنونی", "Spremi trenutačni prikaz"),
    ("Save view", "حفظ العرض", "שמירת תצוגה", "ذخیره نما", "Spremi prikaz"),
    ("Saved views", "العروض المحفوظة", "תצוגות שמורות", "نماهای ذخیره‌شده", "Spremljeni prikazi"),
    ("Search and filters", "البحث وعوامل التصفية", "חיפוש ומסננים", "جستجو و فیلترها", "Pretraga i filtri"),
    ("Search names, sources, sides and text…", "ابحث في الأسماء والمصادر والجوانب والنص…", "חיפוש שמות, מקורות, צדדים וטקסט…", "جستجوی نام‌ها، منابع، طرف‌ها و متن…", "Pretraži nazive, izvore, strane i tekst…"),
    ("Select a range of files", "تحديد نطاق من الملفات", "בחירת טווח קבצים", "انتخاب بازه‌ای از فایل‌ها", "Odaberi raspon datoteka"),
    ("Select at least one file.", "حدد ملفًا واحدًا على الأقل.", "יש לבחור לפחות קובץ אחד.", "دست‌کم یک فایل انتخاب کنید.", "Odaberite barem jednu datoteku."),
    ("Select every file in the list, or clear the selection", "تحديد كل ملفات القائمة أو إلغاء التحديد", "בחירת כל הקבצים ברשימה או ניקוי הבחירה", "انتخاب همه فایل‌های فهرست یا پاک کردن انتخاب", "Odaberi sve datoteke na popisu ili očisti odabir"),
    ("Select exactly one file to duplicate.", "حدد ملفًا واحدًا فقط للتكرار.", "יש לבחור קובץ אחד בדיוק לשכפול.", "برای تکثیر دقیقاً یک فایل انتخاب کنید.", "Odaberite točno jednu datoteku za dupliciranje."),
    ("Select exactly one file to rename.", "حدد ملفًا واحدًا فقط لإعادة التسمية.", "יש לבחור קובץ אחד בדיוק לשינוי שם.", "برای تغییر نام دقیقاً یک فایل انتخاب کنید.", "Odaberite točno jednu datoteku za preimenovanje."),
    ("Select file", "تحديد الملف", "בחירת קובץ", "انتخاب فایل", "Odaberi datoteku"),
    ("Show 1 level", "إظهار مستوى واحد", "הצגת רמה אחת", "نمایش ۱ سطح", "Prikaži 1 razinu"),
    ("Show 2 levels", "إظهار مستويين", "הצגת שתי רמות", "نمایش ۲ سطح", "Prikaži 2 razine"),
    ("Show 3 levels", "إظهار ثلاثة مستويات", "הצגת שלוש רמות", "نمایش ۳ سطح", "Prikaži 3 razine"),
    ("Show 4 levels", "إظهار أربعة مستويات", "הצגת ארבע רמות", "نمایش ۴ سطح", "Prikaži 4 razine"),
    ("Show all levels", "إظهار كل المستويات", "הצגת כל הרמות", "نمایش همه سطوح", "Prikaži sve razine"),
    ("Show filters", "إظهار عوامل التصفية", "הצגת מסננים", "نمایش فیلترها", "Prikaži filtre"),
    ("Show more", "إظهار المزيد", "הצגת עוד", "نمایش بیشتر", "Prikaži više"),
    ("Show or hide items that contain no files", "إظهار أو إخفاء العناصر التي لا تحتوي على ملفات", "הצגה או הסתרה של פריטים ללא קבצים", "نمایش یا پنهان کردن موارد بدون فایل", "Prikaži ili sakrij stavke bez datoteka"),
    ("Show or hide the filter cards", "إظهار أو إخفاء بطاقات التصفية", "הצגה או הסתרה של כרטיסי הסינון", "نمایش یا پنهان کردن کارت‌های فیلتر", "Prikaži ili sakrij kartice filtara"),
    ("Show or hide the sidebar", "إظهار أو إخفاء الشريط الجانبي", "הצגה או הסתרה של סרגל הצד", "نمایش یا پنهان کردن نوار کناری", "Prikaži ili sakrij bočnu traku"),
    ("Show or hide the sidebar (B)", "إظهار أو إخفاء الشريط الجانبي (B)", "הצגה או הסתרה של סרגל הצד (B)", "نمایش یا پنهان کردن نوار کناری (B)", "Prikaži ili sakrij bočnu traku (B)"),
    ("Sidebar", "الشريط الجانبي", "סרגל צד", "نوار کناری", "Bočna traka"),
    ("Snippet", "المقتطف", "קטע", "بریده", "Isječak"),
    ("Stored path", "المسار المخزَّن", "נתיב מאוחסן", "مسیر ذخیره‌شده", "Pohranjeni put"),
    ("Stored path (optional)", "المسار المخزَّن (اختياري)", "נתיב מאוחסן (אופציונלי)", "مسیر ذخیره‌شده (اختیاری)", "Pohranjeni put (neobavezno)"),
    ("Structure", "البنية", "מבנה", "ساختار", "Struktura"),
    ("Structure path", "مسار البنية", "נתיב המבנה", "مسیر ساختار", "Put strukture"),
    ("Structured (category folders)", "منظَّم (مجلدات الفئات)", "מובנה (תיקיות קטגוריה)", "ساختارمند (پوشه‌های دسته)", "Strukturirano (mape kategorija)"),
    ("Structured export (CSV)", "تصدير منظَّم (CSV)", "ייצוא מובנה (CSV)", "خروجی ساختارمند (CSV)", "Strukturirani izvoz (CSV)"),
    ("Subcategories", "الفئات الفرعية", "תת־קטגוריות", "زیردسته‌ها", "Potkategorije"),
    ("Subcategory", "الفئة الفرعية", "תת־קטגוריה", "زیردسته", "Potkategorija"),
    ("Taxonomy path", "مسار التصنيف", "נתיב הטקסונומיה", "مسیر طبقه‌بندی", "Put taksonomije"),
    ("Term ID", "معرّف المصطلح", "מזהה מונח", "شناسه اصطلاح", "ID pojma"),
    ("Term added", "تمت إضافة المصطلح", "המונח נוסף", "اصطلاح افزوده شد", "Pojam je dodan"),
    ("Term coverage", "تغطية المصطلحات", "כיסוי מונחים", "پوشش اصطلاحات", "Pokrivenost pojmova"),
    ("Term removed", "تمت إزالة المصطلح", "המונח הוסר", "اصطلاح حذف شد", "Pojam je uklonjen"),
    ("Term updated", "تم تحديث المصطلح", "המונח עודכן", "اصطلاح به‌روزرسانی شد", "Pojam je ažuriran"),
    ("Terms", "المصطلحات", "מונחים", "اصطلاحات", "Pojmovi"),
    ("Terms in basket", "المصطلحات في السلة", "מונחים בסל", "اصطلاحات در سبد", "Pojmovi u košarici"),
    ("Terms with no files", "مصطلحات بلا ملفات", "מונחים ללא קבצים", "اصطلاحات بدون فایل", "Pojmovi bez datoteka"),
    ("The basket overrides the focused node.", "السلة تتجاوز العقدة المركّز عليها.", "הסל גובר על הצומת שבמיקוד.", "سبد بر گره کانونی اولویت دارد.", "Košarica nadjačava fokusirani čvor."),
    ("The subcategory could not be resolved.", "تعذّر تحديد الفئة الفرعية.", "לא ניתן היה לזהות את תת־הקטגוריה.", "زیردسته قابل تشخیص نبود.", "Potkategoriju nije moguće odrediti."),
    ("The taxonomy is empty. Add a main category to begin.", "التصنيف فارغ. أضف فئة رئيسية للبدء.", "הטקסונומיה ריקה. הוסיפו קטגוריה ראשית כדי להתחיל.", "طبقه‌بندی خالی است. برای شروع یک دسته اصلی بیفزایید.", "Taksonomija je prazna. Dodajte glavnu kategoriju za početak."),
    ("The text is indexed against the taxonomy; existing words and phrases are counted.", "يُفهرَس النص مقابل التصنيف وتُحسب الكلمات والعبارات الموجودة.", "הטקסט נסרק מול הטקסונומיה והמילים והביטויים הקיימים נספרים.", "متن در برابر طبقه‌بندی نمایه می‌شود و واژه‌ها و عبارت‌های موجود شمرده می‌شوند.", "Tekst se indeksira prema taksonomiji i broje se postojeće riječi i izrazi."),
    ("This help", "هذه المساعدة", "עזרה זו", "این راهنما", "Ova pomoć"),
    ("This node cannot be edited here.", "لا يمكن تحرير هذه العقدة هنا.", "לא ניתן לערוך צומת זה כאן.", "این گره در اینجا قابل ویرایش نیست.", "Ovaj se čvor ovdje ne može uređivati."),
    ("To date", "إلى تاريخ", "עד תאריך", "تا تاریخ", "Do datuma"),
    ("Toggle one file", "تبديل ملف واحد", "החלפת קובץ אחד", "تغییر وضعیت یک فایل", "Prebaci jednu datoteku"),
    ("Uncategorised", "غير مصنَّف", "לא מסווג", "بدون دسته", "Nekategorizirano"),
    ("Undo", "تراجع", "ביטול", "بازگردانی", "Poništi"),
    ("Undo the last change", "التراجع عن آخر تغيير", "ביטול השינוי האחרון", "بازگردانی آخرین تغییر", "Poništi posljednju promjenu"),
    ("Undone", "تم التراجع", "בוטל", "بازگردانی شد", "Poništeno"),
    ("View applied", "تم تطبيق العرض", "התצוגה הוחלה", "نما اعمال شد", "Prikaz je primijenjen"),
    ("View deleted", "تم حذف العرض", "התצוגה נמחקה", "نما حذف شد", "Prikaz je izbrisan"),
    ("View name", "اسم العرض", "שם התצוגה", "نام نما", "Naziv prikaza"),
    ("View renamed", "تمت إعادة تسمية العرض", "שם התצוגה שונה", "نام نما تغییر کرد", "Prikaz je preimenovan"),
    ("View saved", "تم حفظ العرض", "התצוגה נשמרה", "نما ذخیره شد", "Prikaz je spremljen"),
    ("What to export", "ما الذي تريد تصديره", "מה לייצא", "چه چیزی خروجی گرفته شود", "Što izvesti"),
    ("Which files", "أي ملفات", "אילו קבצים", "کدام فایل‌ها", "Koje datoteke"),
    ("Why it matched", "سبب المطابقة", "מדוע נמצאה התאמה", "دلیل تطبیق", "Zašto se podudara"),
    ("Word", "كلمة", "מילה", "واژه", "Riječ"),
    ("Word galaxy", "مجرة الكلمات", "גלקסיית המילים", "کهکشان واژه‌ها", "Galaksija riječi"),
    ("Word terms", "مصطلحات الكلمات", "מונחי מילים", "اصطلاحات واژه", "Pojmovi riječi"),
    ("Words separated by spaces; the expression is stored as the sequence of its words.", "كلمات مفصولة بمسافات؛ يُخزَّن التعبير كتسلسل كلماته.", "מילים מופרדות ברווחים; הביטוי נשמר כרצף המילים שלו.", "واژه‌ها با فاصله جدا می‌شوند؛ عبارت به‌صورت دنباله واژه‌هایش ذخیره می‌شود.", "Riječi odvojene razmacima; izraz se sprema kao slijed svojih riječi."),
    ("Zoom", "التكبير", "זום", "بزرگ‌نمایی", "Zum"),
    ("Zoom in (+)", "تكبير (+)", "הגדלה (+)", "بزرگ‌نمایی (+)", "Povećaj (+)"),
    ("Zoom in and out", "التكبير والتصغير", "הגדלה והקטנה", "بزرگ و کوچک کردن", "Povećaj i smanji"),
    ("Zoom out (-)", "تصغير (-)", "הקטנה (-)", "کوچک‌نمایی (-)", "Smanji (-)"),
    ("chunks", "أجزاء", "קטעים", "قطعه‌ها", "dijelovi"),
    ("hidden", "مخفية", "מוסתרים", "پنهان", "skriveno"),
    ("hits", "مرات الظهور", "התאמות", "تطابق‌ها", "pogodaka"),
    ("nodes", "عقد", "צמתים", "گره‌ها", "čvorova"),
    ("on this page", "في هذه الصفحة", "בעמוד זה", "در این صفحه", "na ovoj stranici"),
    ("rows", "صفوف", "שורות", "ردیف‌ها", "redaka"),
    ("shown", "معروضة", "מוצגים", "نمایش‌داده‌شده", "prikazano"),
    ("subcategories", "فئات فرعية", "תת־קטגוריות", "زیردسته‌ها", "potkategorija"),
    ("table in Markdown", "جدول بصيغة Markdown", "טבלה ב־Markdown", "جدول در Markdown", "tablica u Markdownu"),
    ("terms", "مصطلحات", "מונחים", "اصطلاحات", "pojmova"),
    ("Colour", "اللون", "צבע", "رنگ", "Boja"),
    ("Colour saved", "تم حفظ اللون", "הצבע נשמר", "رنگ ذخیره شد", "Boja je spremljena"),
    ("Set the category colour", "تعيين لون الفئة", "קביעת צבע הקטגוריה", "تعیین رنگ دسته", "Postavi boju kategorije"),
    ("Write the colour as #rrggbb.", "اكتب اللون بالصيغة #rrggbb.", "כתבו את הצבע בתבנית #rrggbb.", "رنگ را به شکل #rrggbb بنویسید.", "Zapišite boju kao #rrggbb."),
    ("A colour must be written as #rrggbb.", "يجب كتابة اللون بالصيغة #rrggbb.", "יש לכתוב את הצבע בתבנית #rrggbb.", "رنگ باید به شکل #rrggbb نوشته شود.", "Boja se piše kao #rrggbb."),
    ("Categories, subcategories and terms with no files in scope are left out of the structure.", "تُستبعد من البنية الفئات والفئات الفرعية والمصطلحات التي لا تحتوي على ملفات ضمن النطاق.", "קטגוריות, תת־קטגוריות ומונחים בלי קבצים בתחום מושמטים מהמבנה.", "دسته‌ها، زیردسته‌ها و اصطلاحاتی که در این محدوده فایلی ندارند از ساختار حذف می‌شوند.", "Kategorije, potkategorije i pojmovi bez datoteka u opsegu izostavljeni su iz strukture."),
    ("Hidden", "مخفي", "מוסתר", "پنهان", "Skriveno"),
    ("Items with no files are hidden", "العناصر التي لا تحتوي على ملفات مخفية", "פריטים בלי קבצים מוסתרים", "موارد بدون فایل پنهان شدند", "Stavke bez datoteka su skrivene"),
    ("Items with no files are shown", "العناصر التي لا تحتوي على ملفات معروضة", "פריטים בלי קבצים מוצגים", "موارد بدون فایل نمایش داده شدند", "Stavke bez datoteka su prikazane"),
    ("Shown", "معروض", "מוצג", "نمایش‌داده‌شده", "Prikazano"),
    ("The focused item had no files, so the view moved up one level.", "لم يكن للعنصر المركّز ملفات، لذا انتقل العرض مستوى واحدًا إلى الأعلى.", "לפריט שבמיקוד לא היו קבצים, ולכן התצוגה עלתה רמה אחת.", "مورد کانونی فایلی نداشت، بنابراین نما یک سطح بالاتر رفت.", "Fokusirana stavka nije imala datoteka pa se prikaz pomaknuo jednu razinu više."),
    ("terms in the basket", "مصطلحات في السلة", "מונחים בסל", "اصطلاح در سبد", "pojmova u košarici"),
)


def lexis_msgids() -> List[str]:
    """Every string the page asks for, derived from the sources themselves."""
    template = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    found: List[str] = []

    def add(value: str) -> None:
        if value and value not in found:
            found.append(value)

    for _quote, raw in re.findall(r"""_\(\s*(['"])((?:\\.|(?!\1).)*)\1""", template, re.S):
        add(raw.replace("\\'", "'").replace('\\"', '"'))
    for _quote, raw in re.findall(r"""(?<![\w.])t\(\s*(['"])((?:\\.|(?!\1).)*)\1""", script, re.S):
        add(raw.replace("\\'", "'").replace('\\"', '"'))
    for row in REGISTRY_ROWS:
        add(row[0])
    return found


def _translation_maps() -> Dict[str, Dict[str, str]]:
    by_lang: Dict[str, Dict[str, str]] = {lang: {} for lang in LANGS if lang != "en"}
    for english, ar, he, fa, hr in ROWS + REGISTRY_ROWS:
        for lang, text in (("ar", ar), ("he", he), ("fa", fa), ("hr", hr)):
            by_lang[lang][english] = text
    return by_lang


def _catalog_path(lang: str) -> Path:
    return ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"


@lru_cache(maxsize=None)
def _catalog(lang: str):
    with _catalog_path(lang).open("rb") as handle:
        return read_po(handle, locale=lang)


def _translated_everywhere(msgid: str) -> bool:
    """True when every maintained catalog already carries this string.

    The page reuses a lot of vocabulary the rest of the interface translated
    long ago ("Cancel", "Save", "Delete"). Those existing translations are the
    right words to show here too, so a reused string needs no row of its own -
    only the ones the page introduces are listed in ``ROWS``.
    """
    for lang in LANGS:
        if lang == "en":
            continue
        message = _catalog(lang).get(msgid)
        if message is None or not message.string or message.fuzzy:
            return False
    return True


def check() -> int:
    """Report strings with no row (and rows with no string)."""
    msgids = lexis_msgids()
    rows = {row[0] for row in ROWS + REGISTRY_ROWS}
    missing = [
        msgid for msgid in msgids
        if msgid not in rows and not _translated_everywhere(msgid)
    ]
    unused = sorted(rows - set(msgids))
    if missing:
        print("missing translations for %d string(s):" % len(missing))
        for msgid in missing:
            print("   ", repr(msgid))
    if unused:
        print("rows with no matching string (%d): %s" % (len(unused), unused[:6]))
    return 1 if missing else 0


def _quoted(text: str, prefix: str) -> List[str]:
    """Render ``text`` as a (possibly multi-line) PO string value.

    PO continuation lines are concatenated verbatim, so the space that ended a
    wrapped line has to stay on that line - drop it and the catalog holds a
    different string than the page asks for, which is exactly how a page ends
    up half translated while looking complete in the diff.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    chunks: List[str] = []
    current = ""
    for token in re.findall(r"\S+\s*", escaped):
        if current and len(prefix) + len(current) + len(token) + 3 > 79:
            chunks.append(current)
            current = token
        else:
            current += token
    chunks.append(current)
    rendered = ['%s"%s"' % (prefix, chunks[0].replace('\\"', '\\"'))]
    for chunk in chunks[1:]:
        rendered.append('"%s"' % chunk)
    if len(chunks) > 1:
        # A multi-line value must open with an empty string, as gettext writes it.
        rendered[0] = '%s"%s"' % (prefix, chunks[0])
        return [rendered[0]] + rendered[1:]
    return rendered


def _entry(msgid: str, msgstr: str, references: str) -> str:
    lines = ["#: " + references]
    lines.extend(_quoted(msgid, "msgid "))
    lines.extend(_quoted(msgstr, "msgstr "))
    return "\n".join(lines) + "\n"


def apply() -> None:
    """Add the strings the catalogs are missing, leaving the rest untouched.

    The catalogs are edited as text rather than rewritten through Babel: a
    rewrite reflows and re-sorts thousands of unrelated entries, and a
    translation file should not carry a diff of the whole project for one new
    page. New entries are appended, and an entry the page needs that is only
    present as an empty or fuzzy placeholder is filled in place.
    """
    msgids = lexis_msgids()
    maps = _translation_maps()
    rows = {row[0] for row in ROWS + REGISTRY_ROWS}
    total_added = 0
    for lang in LANGS:
        catalog = _catalog(lang)
        text = _catalog_path(lang).read_text(encoding="utf-8")
        additions: List[str] = []
        added = 0
        for msgid in msgids:
            if lang == "en":
                text_wanted = msgid
            else:
                text_wanted = maps[lang].get(msgid)
            message = catalog.get(msgid)
            if message is None:
                additions.append(
                    _entry(msgid, text_wanted or msgid, REFERENCE)
                )
                added += 1
                continue
            if text_wanted is None:
                continue
            if message.string == text_wanted and not message.fuzzy:
                continue
            # Present but empty or fuzzy: the rows own the words, so fill the
            # existing entry rather than adding a duplicate msgid.
            if message.fuzzy:
                text = re.sub(
                    r"^#,.*\bfuzzy\b.*\n(?=(?:#[^\n]*\n)*msgid %s)"
                    % re.escape('"' + msgid + '"'),
                    "",
                    text,
                    flags=re.M,
                )
            text = re.sub(
                r"(?m)^(msgid %s\n)msgstr(?: \"[^\"]*\")*$"
                % re.escape('"' + msgid + '"'),
                lambda match: match.group(1)
                + "\n".join(_quoted(text_wanted, "msgstr ")),
                text,
                count=1,
            )
        if additions:
            if not text.endswith("\n"):
                text += "\n"
            text = text.rstrip("\n") + "\n\n" + "\n".join(additions)
        _catalog_path(lang).write_text(text, encoding="utf-8")
        total_added += added
        print("lexis i18n: %s (%d new, %d strings in scope)"
              % (lang, added, len(msgids)))
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "compile", "-d", "translations"],
        check=True,
        cwd=ROOT,
    )
    print("compiled .mo (%d entries added)" % total_added)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only")
    parser.add_argument(
        "--list-missing", action="store_true",
        help="print the page strings no catalog has yet (row authors' worklist)",
    )
    arguments = parser.parse_args()
    if arguments.list_missing:
        rows = {row[0] for row in ROWS + REGISTRY_ROWS}
        for msgid in lexis_msgids():
            if msgid not in rows and not _translated_everywhere(msgid):
                print(msgid)
        return
    status = check()
    if status:
        raise SystemExit(status)
    if arguments.check:
        return
    apply()


if __name__ == "__main__":
    main()
