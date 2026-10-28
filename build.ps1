$ErrorActionPreference = "Stop"

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --name "SpellcasterUniversity_RU_Installer" `
  --collect-all "UnityPy" `
  --add-data "data\i2_schema.json;data" `
  --add-data "data\translations_ru.json;data" `
  installer.py
