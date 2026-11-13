# Arabic Fonts Setup

Arabic fonts (Noto Sans Arabic) need to be downloaded and placed in this directory.

## Font Files Required:
- `noto-arabic-400.ttf` (Regular)
- `noto-arabic-500.ttf` (Medium)
- `noto-arabic-600.ttf` (SemiBold)
- `noto-arabic-700.ttf` (Bold)

## Download Instructions:

1. Visit: https://fonts.google.com/noto/specimen/Noto+Sans+Arabic
2. Click "Download family" to get all font files
3. Extract the TTF files
4. Rename and place them in this directory:
   - `NotoSansArabic-Regular.ttf` → `noto-arabic-400.ttf`
   - `NotoSansArabic-Medium.ttf` → `noto-arabic-500.ttf`
   - `NotoSansArabic-SemiBold.ttf` → `noto-arabic-600.ttf`
   - `NotoSansArabic-Bold.ttf` → `noto-arabic-700.ttf`

Alternatively, you can use the download script:
```bash
python static/fonts/download_arabic_fonts.py
```

The CSS file `noto-arabic.css` is already configured and will work once the font files are in place.

