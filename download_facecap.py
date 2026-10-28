import urllib.request
from pathlib import Path

# URL stable depuis la branche master de three.js
URL = "https://raw.githubusercontent.com/mrdoob/three.js/master/examples/models/gltf/facecap.glb"
DEST = Path("assets/avatar/sarra.glb")

DEST.parent.mkdir(parents=True, exist_ok=True)
print(f"📥 Téléchargement de facecap.glb...")

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=120) as r, open(DEST, "wb") as f:
        while chunk := r.read(64 * 1024):
            f.write(chunk)
    taille = DEST.stat().st_size
    print(f"✅ SUCCÈS : {DEST} ({taille // 1024} Ko)")
    if taille < 100000:
        print("⚠️  ATTENTION : fichier trop petit, téléchargement incomplet !")
except Exception as e:
    print(f"❌ ERREUR : {e}")