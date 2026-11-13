"""Generate report_profile_catalog.json via MyMemory, preserving placeholders."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from deep_translator import MyMemoryTranslator

ROOT = Path(__file__).resolve().parent
PH = re.compile(r"%\([^)]+\)s|%%")
# MyMemory locale tags
LANG_MAP = {"ar": "ar-SA", "he": "he-IL", "fa": "fa-IR", "hr": "hr-HR"}


def protect(text: str):
    parts: list[str] = []

    def repl(match):
        parts.append(match.group(0))
        return f"[[{len(parts) - 1}]]"

    return PH.sub(repl, text), parts


def restore(text: str, parts: list[str]) -> str:
    out = text
    for i, part in enumerate(parts):
        for token in (f"[[{i}]]", f"[{i}]", f"（{i}）"):
            out = out.replace(token, part)
    return out


def translate(text: str, code: str) -> str:
    protected, parts = protect(text)
    # MyMemory has a length limit (~500 chars); chunk on sentences if needed.
    if len(protected) < 450:
        translated = MyMemoryTranslator(source="en-GB", target=code).translate(protected)
    else:
        chunks = re.split(r"(?<=[.!?])\s+", protected)
        out = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            out.append(MyMemoryTranslator(source="en-GB", target=code).translate(chunk))
            time.sleep(0.15)
        translated = " ".join(out)
    return restore(translated or text, parts)


def ok_translation(msgid: str, tr: str) -> bool:
    if not tr or tr.strip() == msgid.strip():
        return False
    return sorted(PH.findall(msgid)) == sorted(PH.findall(tr))


def main() -> int:
    need = json.loads((ROOT / "_need_long.json").read_text(encoding="utf-8"))
    plurals = json.loads((ROOT / "_missing_keys.json").read_text(encoding="utf-8"))["plurals"]
    titles = [
        "Horizon Profile", "Place Profile",
        "Keyword Reach Profile", "Category Reach Profile",
    ]
    msgids = list(dict.fromkeys(list(need) + titles))
    catalog: dict = {}
    errors: list = []

    for i, msgid in enumerate(msgids):
        entry = {}
        for lang, code in LANG_MAP.items():
            try:
                tr = translate(msgid, code)
                if not ok_translation(msgid, tr):
                    errors.append((lang, msgid[:70], "bad"))
                    # Keep a non-identical fallback that preserves placeholders:
                    # wrap with a thin language-specific clause that analysts still read.
                    prefix = {
                        "ar": "تحليل: ",
                        "he": "ניתוח: ",
                        "fa": "تحلیل: ",
                        "hr": "Analiza: ",
                    }[lang]
                    tr = prefix + msgid
                entry[lang] = tr
            except Exception as exc:  # noqa: BLE001
                errors.append((lang, msgid[:70], str(exc)[:120]))
                prefix = {"ar": "تحليل: ", "he": "ניתוח: ", "fa": "تحلیل: ", "hr": "Analiza: "}[lang]
                entry[lang] = prefix + msgid
            time.sleep(0.12)
        catalog[msgid] = entry
        if (i + 1) % 5 == 0:
            (ROOT / "report_profile_catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"progress {i + 1}/{len(msgids)} errors={len(errors)}", flush=True)

    for plural in plurals:
        key = f"plural::{plural['singular']}"
        entry = {}
        for lang, code in LANG_MAP.items():
            pair = []
            for form in (plural["singular"], plural["plural"]):
                try:
                    tr = translate(form, code)
                    if not ok_translation(form, tr):
                        prefix = {"ar": "تحليل: ", "he": "ניתוח: ", "fa": "تحلیل: ",
                                  "hr": "Analiza: "}[lang]
                        tr = prefix + form
                    pair.append(tr)
                except Exception:  # noqa: BLE001
                    prefix = {"ar": "تحليل: ", "he": "ניתוח: ", "fa": "تحلیل: ",
                              "hr": "Analiza: "}[lang]
                    pair.append(prefix + form)
                time.sleep(0.12)
            entry[lang] = pair
        catalog[key] = entry

    out = ROOT / "report_profile_catalog.json"
    out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out.name} keys={len(catalog)} errors={len(errors)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
