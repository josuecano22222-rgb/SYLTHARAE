#!/usr/bin/env python3
"""Ensure Map Viewer strings exist and are translated in all locale catalogs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[1]
LANGS = ("en", "ar", "he", "fa", "hr")

MAP_VIEWER_MSGIDS: tuple[str, ...] = (
    "Map Viewer",
    "Layered basemaps, offline packs, and customizable coordinate markers",
    "Checking connection…",
    "points",
    "Offline map storage",
    "Map controls",
    "Map layers",
    "Choose standard, aerial, hybrid, terrain, or downloaded offline databases.",
    "Offline capture",
    "Automatically save map tiles while browsing online",
    "Viewed areas are stored as compressed images in map_data/registry.db and work offline on this device. Respect third-party tile provider terms.",
    "Display mode",
    "2D map",
    "Coordinate markers",
    "Icons shown at each geotagged file position.",
    "Marker icon",
    "Marker color",
    "Marker size",
    "Icon by file type (photos, documents, …)",
    "Open file when marker is clicked",
    "Show file names on map",
    "Offline databases",
    "Refresh",
    "Download compressed tile packs into map_data for use without network access. Copy map_data to move packs between devices.",
    "Export portable map bundle",
    "Import portable map bundle",
    "Creates or restores a .sylmap.tar.gz with catalog, SQLite registry, packages, and installed layers.",
    "Coordinates",
    "Filter",
    "File name or coordinates…",
    "Interactive map",
    "Fit all points",
    "Recenter",
    "Online",
    "Offline",
    "Installed",
    "Not installed",
    "Built-in",
    "Download",
    "Remove",
    "Use this layer",
    "Download failed",
    "Remove this offline map package from disk?",
    "No matching coordinates.",
    "Offline — local tiles only",
    "Download or install this pack to use it offline.",
    "This layer requires network access.",
    "Open file",
    "Could not initialize map viewer",
    "Standard map",
    "Aerial imagery",
    "Hybrid (imagery + labels)",
    "Topographic terrain",
    "Dark map",
    "Standard",
    "Satellite",
    "Hybrid",
    "Terrain",
    "Dark",
    "Pin",
    "Document",
    "Camera",
    "Video",
    "Flag",
    "Star",
    "Diamond",
    "Offline storage",
    "Map bundle imported successfully.",
    "Map bundle import failed.",
    "cached tiles",
    "Open coordinates in the Map Viewer",
    "Browse geotagged files on offline or online basemaps; download map packages into the project for use without network access.",
)

# pylint: disable=line-too-long
EXTRA: dict[str, dict[str, str]] = {
    "ar": {
        "Map Viewer": "عارض الخريطة",
        "Layered basemaps, offline packs, and customizable coordinate markers": "خرائط أساس متعددة الطبقات، حزم دون اتصال، وعلامات إحداثيات قابلة للتخصيص",
        "Checking connection…": "جاري التحقق من الاتصال…",
        "points": "نقاط",
        "Offline map storage": "تخزين الخريطة دون اتصال",
        "Map controls": "عناصر التحكم بالخريطة",
        "Map layers": "طبقات الخريطة",
        "Choose standard, aerial, hybrid, terrain, or downloaded offline databases.": "اختر خريطة قياسية أو جوية أو هجينة أو تضاريس أو قواعد بيانات محمّلة دون اتصال.",
        "Offline capture": "التقاط دون اتصال",
        "Automatically save map tiles while browsing online": "حفظ بلاط الخريطة تلقائيًا أثناء التصفح متصلًا",
        "Viewed areas are stored as compressed images in map_data/registry.db and work offline on this device. Respect third-party tile provider terms.": "المناطق المعروضة تُخزَّن كصور مضغوطة في map_data/registry.db وتعمل دون اتصال. راعِ شروط مزوّدي البلاط.",
        "Display mode": "وضع العرض",
        "2D map": "خريطة ثنائية الأبعاد",
        "Coordinate markers": "علامات الإحداثيات",
        "Icons shown at each geotagged file position.": "أيقونات تُعرض عند كل موقع ملف جغرافي.",
        "Marker icon": "أيقونة العلامة",
        "Marker color": "لون العلامة",
        "Marker size": "حجم العلامة",
        "Icon by file type (photos, documents, …)": "أيقونة حسب نوع الملف (صور، مستندات، …)",
        "Open file when marker is clicked": "فتح الملف عند النقر على العلامة",
        "Show file names on map": "إظهار أسماء الملفات على الخريطة",
        "Offline databases": "قواعد بيانات دون اتصال",
        "Refresh": "تحديث",
        "Download compressed tile packs into map_data for use without network access. Copy map_data to move packs between devices.": "حمّل حزم البلاط المضغوطة إلى map_data للاستخدام دون شبكة. انسخ map_data لنقل الحزم بين الأجهزة.",
        "Export portable map bundle": "تصدير حزمة خريطة محمولة",
        "Import portable map bundle": "استيراد حزمة خريطة محمولة",
        "Creates or restores a .sylmap.tar.gz with catalog, SQLite registry, packages, and installed layers.": "إنشاء أو استعادة .sylmap.tar.gz يتضمن catalog وسجل SQLite والحزم والطبقات المثبتة.",
        "Coordinates": "إحداثيات",
        "Filter": "تصفية",
        "File name or coordinates…": "اسم الملف أو الإحداثيات…",
        "Interactive map": "خريطة تفاعلية",
        "Fit all points": "ملاءمة كل النقاط",
        "Recenter": "إعادة التمركز",
        "Online": "متصل",
        "Offline": "دون اتصال",
        "Installed": "مثبت",
        "Not installed": "غير مثبت",
        "Built-in": "مدمج",
        "Download": "تنزيل",
        "Remove": "إزالة",
        "Use this layer": "استخدام هذه الطبقة",
        "Download failed": "فشل التنزيل",
        "Remove this offline map package from disk?": "إزالة حزمة الخريطة دون اتصال من القرص؟",
        "No matching coordinates.": "لا توجد إحداثيات مطابقة.",
        "Offline — local tiles only": "دون اتصال — بلاط محلي فقط",
        "Download or install this pack to use it offline.": "نزّل أو ثبّت هذه الحزمة لاستخدامها دون اتصال.",
        "This layer requires network access.": "تتطلب هذه الطبقة اتصالًا بالشبكة.",
        "Open file": "فتح الملف",
        "Could not initialize map viewer": "تعذّر تهيئة عارض الخريطة",
        "Standard map": "خريطة قياسية",
        "Aerial imagery": "صور جوية",
        "Hybrid (imagery + labels)": "هجين (صور + تسميات)",
        "Topographic terrain": "تضاريس طبوغرافية",
        "Dark map": "خريطة داكنة",
        "Standard": "قياسي",
        "Satellite": "قمر صناعي",
        "Hybrid": "هجين",
        "Terrain": "تضاريس",
        "Dark": "داكن",
        "Pin": "دبوس",
        "Document": "مستند",
        "Camera": "كاميرا",
        "Video": "فيديو",
        "Flag": "علم",
        "Star": "نجمة",
        "Diamond": "معيّن",
        "Offline storage": "التخزين دون اتصال",
        "Map bundle imported successfully.": "تم استيراد حزمة الخريطة بنجاح.",
        "Map bundle import failed.": "فشل استيراد حزمة الخريطة.",
        "cached tiles": "بلاط مخزّن",
        "Open coordinates in the Map Viewer": "فتح الإحداثيات في عارض الخريطة",
        "Browse geotagged files on offline or online basemaps; download map packages into the project for use without network access.": "تصفّح الملفات الجغرافية على خرائط أساس دون اتصال أو متصلة؛ نزّل حزم الخريطة إلى المشروع للاستخدام دون شبكة.",
    },
}

sys.path.insert(0, str(ROOT))
from scripts.i18n_map_viewer_langs import FA, HE, HR  # noqa: E402

EXTRA["he"] = HE
EXTRA["fa"] = FA
EXTRA["hr"] = HR


def _apply(lang: str) -> None:
    mapping = EXTRA.get(lang, {})
    path = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as handle:
        catalog = read_po(handle, locale=lang)
    for msgid in MAP_VIEWER_MSGIDS:
        message = catalog.get(msgid)
        if message is None:
            text = msgid if lang == "en" else mapping.get(msgid, msgid)
            catalog.add(msgid, string=text)
            continue
        if lang == "en":
            if not message.string:
                message.string = msgid
        else:
            text = mapping.get(msgid)
            if text:
                message.string = text
            elif not message.string:
                message.string = msgid
        message.flags.discard("fuzzy")
    with path.open("wb") as handle:
        write_po(handle, catalog, width=88)
    print(f"map viewer i18n: {lang}")


def main() -> None:
    for lang in LANGS:
        _apply(lang)
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "compile", "-d", "translations"],
        check=True,
        cwd=ROOT,
    )
    print("compiled .mo")


if __name__ == "__main__":
    main()
