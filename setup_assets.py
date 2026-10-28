# -*- coding: utf-8 -*-
"""Acquisition des assets 3D de Sarra (avatar photoréaliste Mark-LV).

Utilisation :
    python setup_assets.py
    python setup_assets.py --gender male
    python setup_assets.py --female-url "https://models.readyplayer.me/<id>.glb" \
        --male-url "https://models.readyplayer.me/<id>.glb"

Télécharge dans ``assets/avatar/`` :
  * ``avatar_female.glb`` — modèle féminin Ready Player Me.
  * ``avatar_male.glb``   — modèle masculin Ready Player Me.
  * ``royal_esplanade_1k.hdr`` — carte d'environnement Poly Haven (IBL, CC0).

100 % gratuit, aucune clé API. Ne touche pas au pipeline audio.
"""
from __future__ import annotations

import sys
import time
import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

# --- Chemins ---
RACINE = Path(__file__).resolve().parent
DOSSIER_AVATAR = RACINE / "assets" / "avatar"
FICHIER_FEMALE = DOSSIER_AVATAR / "avatar_female.glb"
FICHIER_MALE = DOSSIER_AVATAR / "avatar_male.glb"
FICHIER_CONFIG = DOSSIER_AVATAR / "avatar_config.json"
FICHIER_HDR = DOSSIER_AVATAR / "royal_esplanade_1k.hdr"

# --- Sources libres de droits ---
# Ces deux identifiants sont les valeurs demandées par le projet. Si un avatar
# RPM a été généré dans votre compte, remplacez-les par ses URLs .glb réelles
# via --female-url / --male-url ou les variables SARRA_FEMALE_URL / SARRA_MALE_URL.
URL_FEMALE = os.environ.get(
    "SARRA_FEMALE_URL",
    "https://models.readyplayer.me/64f1a4b0c2f3e2a1b3c4d5e6.glb",
)
URL_MALE = os.environ.get(
    "SARRA_MALE_URL",
    "https://models.readyplayer.me/64f1a4b0c2f3e2a1b3c4d5e7.glb",
)
# HDR Poly Haven, licence CC0, servi par leur CDN gratuit.
URL_HDR = "https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/royal_esplanade_1k.hdr"

TAILLE_MIN_GLB = 100_000   # Un GLB de tête complet pèse généralement plusieurs Mo.
TAILLE_MIN_HDR = 200_000   # Un HDR 1k pèse ~1-2 Mo.


def _barre_progression(telecharge: int, total: int) -> None:
    """Affiche une barre de progression sur une seule ligne (sans dépendance)."""
    if total > 0:
        pct = min(100.0, telecharge * 100.0 / total)
        barres = int(pct // 2)  # 50 caractères de large.
        ligne = f"\r  [{'#' * barres}{'-' * (50 - barres)}] {pct:5.1f}% ({telecharge // 1024} Ko)"
    else:
        ligne = f"\r  ... {telecharge // 1024} Ko téléchargés ..."
    sys.stdout.write(ligne)
    sys.stdout.flush()


def telecharger(url: str, destination: Path, taille_min: int, timeout: int = 120) -> bool:
    """Télécharge ``url`` vers ``destination`` avec progression. Retourne True si OK."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reprend si le fichier semble déjà complet (relances idempotentes).
    if destination.exists() and destination.stat().st_size >= taille_min:
        try:
            with destination.open("rb") as f:
                if f.read(4) == b"glTF":
                    print(f"  [OK] {destination.name} déjà présent ({destination.stat().st_size // 1024} Ko).")
                    return True
        except OSError:
            pass
        print(f"  [INFO] {destination.name} existant invalide, nouveau téléchargement.")
    print(f"  Téléchargement : {url}")
    debut = time.time()
    try:
        requete = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mark-LV/Sarra-setup",
                "Accept": "model/gltf-binary,application/octet-stream,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            total = int(reponse.headers.get("Content-Length", 0) or 0)
            recu = 0
            with open(destination, "wb") as f:
                while True:
                    bloc = reponse.read(1024 * 64)
                    if not bloc:
                        break
                    f.write(bloc)
                    recu += len(bloc)
                    _barre_progression(recu, total)
        print()  # Fin de la barre.
    except urllib.error.HTTPError as e:
        print(f"\n  [ERREUR] HTTP {e.code} pour {url} ({e.reason})", file=sys.stderr)
        destination.unlink(missing_ok=True)
        return False
    except urllib.error.URLError as e:
        print(f"\n  [ERREUR] Réseau pour {url} : {e.reason}. Vérifiez la connexion.", file=sys.stderr)
        destination.unlink(missing_ok=True)
        return False
    except TimeoutError:
        print(f"\n  [ERREUR] Timeout après {timeout}s pour {url}.", file=sys.stderr)
        destination.unlink(missing_ok=True)
        return False
    except OSError as e:
        print(f"\n  [ERREUR] Disque : {e}", file=sys.stderr)
        return False
    # Vérifie que le fichier n'est pas une page d'erreur tronquée.
    taille = destination.stat().st_size
    duree = time.time() - debut
    if taille < taille_min:
        print(f"  [ERREUR] {destination.name} incomplet ({taille} o). Réessayez.", file=sys.stderr)
        destination.unlink(missing_ok=True)
        return False
    try:
        with destination.open("rb") as f:
            if f.read(4) != b"glTF":
                raise ValueError("signature GLB absente")
    except (OSError, ValueError) as e:
        print(f"  [ERREUR] {destination.name} n'est pas un GLB valide : {e}.", file=sys.stderr)
        destination.unlink(missing_ok=True)
        return False
    print(f"  [OK] {destination.name} ({taille // 1024} Ko en {duree:.1f}s).")
    return True


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Télécharge les avatars 3D de Sarra.")
    parser.add_argument("--gender", choices=("female", "male"), default="female",
                        help="modèle sélectionné par défaut dans l'application (female par défaut)")
    parser.add_argument("--female-url", default=URL_FEMALE,
                        help="URL GLB du modèle féminin")
    parser.add_argument("--male-url", default=URL_MALE,
                        help="URL GLB du modèle masculin")
    return parser.parse_args()


def main() -> int:
    """Télécharge les deux GLB et le HDR. Retourne 0 si tout est OK."""
    args = _arguments()
    print("[Sarra] Acquisition des assets 3D -> assets/avatar/")
    print(f"[Sarra] Modèle par défaut : {args.gender}")
    ok_female = telecharger(args.female_url, FICHIER_FEMALE, TAILLE_MIN_GLB)
    ok_male = telecharger(args.male_url, FICHIER_MALE, TAILLE_MIN_GLB)
    ok_hdr = telecharger(URL_HDR, FICHIER_HDR, TAILLE_MIN_HDR)
    if ok_female and ok_male and ok_hdr:
        FICHIER_CONFIG.write_text(
            json.dumps({"gender": args.gender}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("[Sarra] Terminé. Lancez ensuite : python main.py")
        return 0
    print("[Sarra] ÉCHEC — les deux modèles GLB et le HDR sont nécessaires.", file=sys.stderr)
    print("  Fournissez des URLs GLB RPM réelles avec --female-url et --male-url.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
