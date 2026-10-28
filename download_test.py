# download_test.py
import urllib.request
from pathlib import Path

URL = "https://github.com/mrdoob/three.js/raw/refs/heads/dev/examples/models/gltf/facecap.glb"
DEST = Path("assets/avatar/sarra.glb")

# Créer le dossier s'il n'existe pas
DEST.parent.mkdir(parents=True, exist_ok=True)
print(f"📥 Téléchargement de facecap.glb (modèle avec 52 blendshapes ARKit)...")

req = urllib.request.Request(URL, headers={"User-Agent": "Mark-LV"})
try:
    with urllib.request.urlopen(req, timeout=120) as r, open(DEST, "wb") as f:
        while chunk := r.read(64 * 1024):
            f.write(chunk)
    print(f"✅ SUCCÈS : {DEST} ({DEST.stat().st_size // 1024} Ko)")
    print("🚀 Vous pouvez maintenant lancer : python main.py")
except Exception as e:
    print(f"❌ ERREUR de téléchargement : {e}")