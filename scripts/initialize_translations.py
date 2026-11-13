"""
Translation Initialization Script
Extracts all translatable strings from templates and creates translation files
"""

import os
import sys
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from babel.messages import frontend
from babel.messages.pofile import read_po, write_po


def extract_translations():
    """Extract translatable strings from templates and Python files"""
    print("Extracting translatable strings...")
    
    try:
        # Use babel frontend API directly
        extract_cmd = frontend.ExtractMessages()
        extract_cmd.initialize_options()
        extract_cmd.mapping_file = str(project_root / 'babel.cfg')
        extract_cmd.keywords = ['_', 'gettext', 'ngettext', 'lazy_gettext']
        extract_cmd.output_file = str(project_root / 'translations' / 'messages.pot')
        extract_cmd.input_dirs = [str(project_root)]
        extract_cmd.finalize_options()
        extract_cmd.run()
        
        print("✅ Translation strings extracted successfully")
            
    except Exception as e:
        print(f"❌ Error extracting translations: {e}")
        import traceback
        traceback.print_exc()


def update_translation_files():
    """Update existing translation files with new strings"""
    print("\nUpdating translation files...")
    
    translations_dir = project_root / 'translations'
    pot_file = translations_dir / 'messages.pot'
    
    if not pot_file.exists():
        print("❌ messages.pot not found. Run extract_translations first.")
        return
    
    # Update each locale
    for locale_dir in translations_dir.iterdir():
        if locale_dir.is_dir() and locale_dir.name != 'messages.pot':
            locale = locale_dir.name
            po_file = locale_dir / 'LC_MESSAGES' / 'messages.po'
            
            if po_file.exists():
                print(f"Updating {locale} translations...")
                try:
                    # Use babel frontend API directly
                    update_cmd = frontend.UpdateCatalog()
                    update_cmd.initialize_options()
                    update_cmd.input_file = str(pot_file)
                    update_cmd.output_dir = str(translations_dir)
                    update_cmd.locale = locale
                    update_cmd.finalize_options()
                    update_cmd.run()
                    
                    print(f"✅ {locale} translations updated")
                except Exception as e:
                    print(f"❌ Error updating {locale}: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"Creating new translation file for {locale}...")
                try:
                    # Use babel frontend API directly
                    init_cmd = frontend.InitCatalog()
                    init_cmd.initialize_options()
                    init_cmd.input_file = str(pot_file)
                    init_cmd.output_dir = str(translations_dir)
                    init_cmd.locale = locale
                    init_cmd.finalize_options()
                    init_cmd.run()
                    
                    print(f"✅ {locale} translation file created")
                except Exception as e:
                    print(f"❌ Error creating {locale}: {e}")
                    import traceback
                    traceback.print_exc()


def compile_translations():
    """Compile .po files to .mo files"""
    print("\nCompiling translations...")
    
    translations_dir = project_root / 'translations'
    
    try:
        # Use babel frontend API directly
        compile_cmd = frontend.CompileCatalog()
        compile_cmd.initialize_options()
        compile_cmd.directory = str(translations_dir)
        compile_cmd.finalize_options()
        compile_cmd.run()
        
        print("✅ Translations compiled successfully")
    except Exception as e:
        print(f"❌ Error compiling translations: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Main function to initialize translations"""
    print("=" * 60)
    print("Translation Initialization")
    print("=" * 60)
    
    # Extract translations
    extract_translations()
    
    # Update translation files
    update_translation_files()
    
    # Compile translations
    compile_translations()
    
    print("\n" + "=" * 60)
    print("✅ Translation initialization completed")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Edit translation files in translations/<locale>/LC_MESSAGES/messages.po")
    print("2. Run this script again to compile updated translations")
    print("3. Restart the application to see changes")


if __name__ == '__main__':
    main()

