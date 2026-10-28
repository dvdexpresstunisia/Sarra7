# -*- coding: utf-8 -*-
"""Serveur HTTP local pour servir les assets 3D à QtWebEngine."""
from __future__ import annotations

import threading
import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from core.avatar_paths import avatar_config_path, bundled_avatar_config_path


def _read_config(name: str) -> str:
    runtime_path = avatar_config_path(name)
    if runtime_path.is_file():
        return runtime_path.read_text(encoding="utf-8")
    bundled_path = bundled_avatar_config_path(name)
    if bundled_path.is_file():
        return bundled_path.read_text(encoding="utf-8")
    return "{}"


class AvatarHandler(SimpleHTTPRequestHandler):
    """Handler HTTP qui sert les fichiers du dossier assets/avatar/."""

    # Extensions MIME personnalisées pour les fichiers 3D
    extensions_map = {
        '.glb': 'model/gltf-binary',
        '.vrm': 'model/gltf-binary',
        '.gltf': 'model/gltf+json',
        '.hdr': 'application/octet-stream',
        '.bin': 'application/octet-stream',
        '.js': 'application/javascript',
        '.html': 'text/html',
        '.css': 'text/css',
        '.json': 'application/json',
        '': 'application/octet-stream',  # Default
    }

    def __init__(self, *args, directory=None, **kwargs):
        # Forcer le dossier assets/avatar/ comme racine
        avatar_dir = Path(__file__).resolve().parent.parent / "assets" / "avatar"
        super().__init__(*args, directory=str(avatar_dir), **kwargs)

    def _config_path(self) -> Path:
        return avatar_config_path("avatar_camera.json")

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/avatar_models.json":
            avatar_dir = Path(self.directory)
            models = sorted(
                path.name
                for path in avatar_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".glb", ".vrm"}
            )
            data = json.dumps(models, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.split("?", 1)[0] == "/avatar_camera.json":
            try:
                payload = _read_config("avatar_camera.json")
                json.loads(payload)
            except (OSError, ValueError):
                payload = "{}"
            data = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.split("?", 1)[0] == "/avatar_lipsync.json":
            try:
                payload = _read_config("avatar_lipsync.json")
                json.loads(payload)
            except (OSError, ValueError):
                payload = "{}"
            data = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/avatar_camera.json":
            self.send_error(404)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 16384)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("configuration invalide")
            path = self._config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            data = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, ValueError, json.JSONDecodeError):
            self.send_error(400, "configuration invalide")

    def log_message(self, format, *args):
        """Silence les logs HTTP pour ne pas polluer la console."""
        pass

    def guess_type(self, path):
        """Retourne le type MIME correct pour les fichiers 3D."""
        ext = Path(path).suffix.lower()
        return self.extensions_map.get(ext, super().guess_type(path))

class AvatarServer:
    """Serveur HTTP en arrière-plan pour les assets 3D."""
    
    def __init__(self, port: int = 8765):
        self.port = port
        self.server = None
        self.thread = None
        self._start()
    
    def _start(self):
        """Démarre le serveur dans un thread séparé."""
        try:
            self.server = HTTPServer(("127.0.0.1", self.port), AvatarHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            print(f"[Sarra] ✅ Serveur HTTP démarré sur http://127.0.0.1:{self.port}")
        except Exception as e:
            print(f"[Sarra] ⚠️ Échec démarrage serveur HTTP: {e}")
    
    def get_url(self) -> str:
        """Retourne l'URL de base pour charger index.html."""
        return f"http://127.0.0.1:{self.port}/index.html"
    
    def stop(self):
        """Arrête le serveur."""
        if self.server:
            self.server.shutdown()