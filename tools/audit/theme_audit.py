"""Measure the contrast of every visible surface, in both theme modes.

A dark theme is not "the CSS file was added"; it is every surface the reader
actually sees. This walks a list of pages in a real browser twice — once with
`prefers-color-scheme: light`, once with `dark` — and reports, per page:

  * surfaces whose computed background is light while the mode is dark
    (a patch of the light theme left behind), and
  * visible text whose contrast against its own background is below the WCAG AA
    threshold for its size (4.5:1, or 3:1 for large text), and
  * light *controls*: buttons, selects and inputs that keep a light background
    in dark mode. They are smaller than the surface threshold, so an audit that
    only counted panels missed four white icon buttons on /archives.

Only elements with a painted background and enough area are counted, so a
transparent wrapper is not reported as a defect. Output is a table plus an
optional JSON artefact; nothing here guesses.

Usage:
    python tools/audit/theme_audit.py --base http://127.0.0.1:5000 \
        --user admin --password ... --pages / /files /archives /lexis /settings
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

SCRIPT = r"""
async ({ paths, mode }) => {
  const results = [];
  const parse = (value) => {
    const m = String(value || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(',').map((v) => parseFloat(v.trim()));
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const lum = ({ r, g, b }) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const contrast = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const effectiveBg = (el) => {
    let node = el;
    while (node && node !== document.documentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0.55) return bg;
      node = node.parentElement;
    }
    return parse(getComputedStyle(document.body).backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
  };
  for (const path of paths) {
    const response = await fetch(path, { credentials: 'same-origin' });
    results.push({ path, status: response.status, note: 'fetched (navigation is done by the caller)' });
  }
  return results;
}
"""

EVALUATE = r"""
() => {
  const parse = (value) => {
    const m = String(value || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(',').map((v) => parseFloat(v.trim()));
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const lum = ({ r, g, b }) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const contrast = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const describe = (el) => {
    const cls = (el.className && typeof el.className === 'string')
      ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + cls;
  };
  const effectiveBg = (el) => {
    let node = el;
    while (node && node !== document.documentElement) {
      const style = getComputedStyle(node);
      const bg = parse(style.backgroundColor);
      if (bg && bg.a > 0.55) return bg;
      node = node.parentElement;
    }
    return parse(getComputedStyle(document.body).backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
  };
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) return false;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) < 0.2) return false;
    return true;
  };
  const lightSurfaces = [];
  const lightControls = [];
  const lowContrast = [];
  const mode = document.documentElement.getAttribute('data-theme-mode');
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (!visible(el)) continue;
    const style = getComputedStyle(el);
    const bg = parse(style.backgroundColor);
    const rect = el.getBoundingClientRect();
    if (bg && bg.a > 0.55 && lum(bg) > 0.6 && rect.width * rect.height > 2500) {
      lightSurfaces.push({ element: describe(el), bg: style.backgroundColor,
                           area: Math.round(rect.width * rect.height) });
    }
    // Controls are small on purpose: 36x36 icon buttons sit below the 2500 px2
    // surface threshold, so they need their own, lower bar.
    const tag = el.tagName.toLowerCase();
    const isControl = tag === 'button' || tag === 'select' || tag === 'input'
      || (el.classList && (el.classList.contains('btn') || el.getAttribute('role') === 'button'));
    if (isControl) {
      const own = parse(style.backgroundColor);
      if (own && own.a > 0.4 && lum(own) > 0.6 && rect.width * rect.height >= 300) {
        lightControls.push({ element: describe(el), bg: style.backgroundColor,
                             area: Math.round(rect.width * rect.height),
                             text: (el.textContent || '').trim().slice(0, 30) });
      }
    }
    const hasText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (hasText) {
      const ink = parse(style.color);
      if (!ink) continue;
      const back = effectiveBg(el);
      const ratio = contrast(ink, back);
      const size = parseFloat(style.fontSize);
      const bold = (parseInt(style.fontWeight, 10) || 400) >= 700;
      const large = size >= 24 || (size >= 18.66 && bold);
      const need = large ? 3 : 4.5;
      if (ratio < need) {
        lowContrast.push({ element: describe(el), ink: style.color,
                           bg: `rgb(${Math.round(back.r)}, ${Math.round(back.g)}, ${Math.round(back.b)})`,
                           ratio: Math.round(ratio * 100) / 100, need,
                           text: (el.textContent || '').trim().slice(0, 40) });
      }
    }
  }
  return { mode, title: document.title,
           lightSurfaces: lightSurfaces.slice(0, 40),
           lightControls: lightControls.slice(0, 40),
           lowContrast: lowContrast.slice(0, 40),
           counts: { lightSurfaces: lightSurfaces.length, lightControls: lightControls.length,
                     lowContrast: lowContrast.length } };
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5000")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="devstack-admin-password-123")
    parser.add_argument("--pages", nargs="+",
                        default=["/", "/files", "/archives", "/lexis", "/settings",
                                 "/signals", "/search", "/categories", "/dashboard"])
    parser.add_argument("--out", default="audit_report/r19/theme_audit.json")
    options = parser.parse_args()

    from playwright.sync_api import sync_playwright

    rows = []
    with sync_playwright() as play:
        browser = play.chromium.launch(
            executable_path="/tmp/chromium",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1546, "height": 900})
        page = context.new_page()
        page.goto(options.base + "/auth/login", wait_until="domcontentloaded")
        page.fill('input[name="username"]', options.user)
        page.fill('input[name="password"]', options.password)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_timeout(2500)

        for mode in ("light", "dark"):
            page.emulate_media(color_scheme=mode)
            for path in options.pages:
                page.goto(options.base + path, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                data = page.evaluate(EVALUATE)
                rows.append({"path": path, "system": mode, **data})
                print(f"{mode:5} {path:12} surfaces={data['counts']['lightSurfaces']:>3} "
                      f"controls={data['counts']['lightControls']:>3} "
                      f"low-contrast={data['counts']['lowContrast']:>3}")
        browser.close()

    out = pathlib.Path(options.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("written:", out)

    # The gate: in dark mode neither a light surface nor a light control remains.
    bad = [row for row in rows if row["system"] == "dark"
           and (row["counts"]["lightSurfaces"] or row["counts"]["lightControls"])]
    for row in bad:
        print(f"  {row['path']}: {row['counts']['lightSurfaces']} light surfaces, "
              f"{row['counts']['lightControls']} light controls")
        for item in row["lightSurfaces"][:6]:
            print(f"     surface {item['element']} {item['bg']} area={item['area']}")
        for item in row["lightControls"][:6]:
            print(f"     control {item['element']} {item['bg']} area={item['area']} "
                  f"text={item['text']!r}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
