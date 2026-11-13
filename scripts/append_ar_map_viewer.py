#!/usr/bin/env python3
"""Append Map Viewer i18n msgids to ar/messages.po (idempotent)."""
from pathlib import Path

MARKER = "# --- Map Viewer (layers + markers) ---"
BLOCK = """
# --- Map Viewer (layers + markers) ---
msgid "Map layers"
msgstr "طبقات الخريطة"

msgid "Standard map"
msgstr "خريطة قياسية"

msgid "Aerial imagery"
msgstr "صور جوية"

msgid "Hybrid (imagery + labels)"
msgstr "هجين (صور + تسميات)"

msgid "Topographic terrain"
msgstr "تضاريس طبوغرافية"

msgid "Dark map"
msgstr "خريطة داكنة"

msgid "Offline databases"
msgstr "قواعد بيانات دون اتصال"

msgid "Coordinate markers"
msgstr "علامات الإحداثيات"

msgid "Marker size"
msgstr "حجم العلامة"

msgid "Show file names on map"
msgstr "إظهار أسماء الملفات على الخريطة"

msgid "Display mode"
msgstr "وضع العرض"

msgid "2D map"
msgstr "خريطة ثنائية الأبعاد"

msgid "Use this layer"
msgstr "استخدام هذه الطبقة"

msgid "Online"
msgstr "متصل"

msgid "Offline"
msgstr "دون اتصال"

msgid "Installed"
msgstr "مثبت"

msgid "Not installed"
msgstr "غير مثبت"

msgid "Pin"
msgstr "دبوس"

msgid "Document"
msgstr "مستند"

msgid "Camera"
msgstr "كاميرا"

msgid "Flag"
msgstr "علم"

msgid "Star"
msgstr "نجمة"

msgid "Diamond"
msgstr "معيّن"

msgid "Open file"
msgstr "فتح الملف"

msgid "Offline storage"
msgstr "التخزين دون اتصال"

msgid "Layered basemaps, offline packs, and customizable coordinate markers"
msgstr "خرائط أساس متعددة الطبقات، حزم دون اتصال، وعلامات إحداثيات قابلة للتخصيص"

msgid "Choose standard, aerial, hybrid, terrain, or downloaded offline databases."
msgstr "اختر خريطة قياسية أو جوية أو هجينة أو تضاريس أو قواعد بيانات محمّلة دون اتصال."

msgid "Icons shown at each geotagged file position."
msgstr "أيقونات تُعرض عند كل موقع ملف جغرافي."

msgid "Download compressed tile packs into map_data for use without network access. Copy map_data to move packs between devices."
msgstr "حمّل حزم البلاط المضغوطة إلى map_data للاستخدام دون شبكة. انسخ map_data لنقل الحزم بين الأجهزة."
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
