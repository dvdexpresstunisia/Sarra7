# -*- coding: utf-8 -*-
"""Pont Python -> avatar 3D Sarra (Three.js dans QtWebEngine)."""
from __future__ import annotations

import logging
import json
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

log = logging.getLogger("Sarra")

# Import optionnel : l'app doit survivre sans QtWebEngine (VM, GPU blacklistée).
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebChannel import QWebChannel
    WEBENGINE_DISPONIBLE = True
except Exception as e:
    QWebEngineView = None  # type: ignore
    QWebEngineSettings = None  # type: ignore
    QWebChannel = None  # type: ignore
    WEBENGINE_DISPONIBLE = False
    log.warning("QtWebEngine indisponible (%s). Avatar 3D désactivé, repli 2D.", e)

# Correspondance noms core/viseme.py -> IDs JS (TABLE_VISEMES dans app.js).
VISEME_IDS: dict[str, int] = {
    "REST": 0, "MBP": 1, "FV": 2, "TD": 4, "K": 5,
    "S": 7, "L": 4, "R": 9, "AA": 10, "E": 11,
    "I": 12, "O": 13, "U": 14,
}


class _AvatarPageBridge(QObject):
    """Objet exposé à la page (QWebChannel) pour les actions de l'interface HTML."""

    mic_toggle = pyqtSignal()

    @pyqtSlot()
    def toggleMic(self) -> None:
        """Appelé par le bouton micro HTML de index.html."""
        self.mic_toggle.emit()


class AvatarBridge(QObject):
    """Pilote la page Three.js. Appels asynchrones, file d'attente avant chargement."""

    _executer_js = pyqtSignal(str)
    # Le bouton micro HTML demande un basculement ; MainWindow écoute ce signal.
    mic_toggle = pyqtSignal()
    # Émission retardée : QTimer ne tourne que sur le thread GUI, or
    # push_frames arrive du thread audio — on y re-dispatche via ces signaux.
    _planifier_js = pyqtSignal(str, int)
    _repos_planifie = pyqtSignal(int, int)
    # Évaluation JS avec retour (mesure bouche) : le callback arrive GUI-thread.
    _evaluer_js = pyqtSignal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vue = None
        self._server = None
        self._pret = False
        self._file: list[str] = []
        self._actif = WEBENGINE_DISPONIBLE
        # Pic de niveau de CETTE voix (cf. _v_peak de core/avatar.py) : le TTS
        # peut parler bas, et un niveau absolu donnerait une bouche
        # à peine entrouverte en permanence. Normalisé ici, comme en 2D.
        self._v_peak = 0.18
        # Calibration temps réel (page LIPSYNC) : décalage A/V en ms
        # (négatif = bouche plus tôt), seuil de silence 0..0.10.
        self._sync_offset = 0.0
        self._seuil_silence = 0.02
        self._batch_id = 0
        self._jeton_mesure = 0
        self._rappels_mesure: dict = {}
        # Canal page -> Python (bouton micro HTML). Conservé pour la durée de vie
        # de la vue : QWebChannel ne doit pas être collecté.
        self._channel = None
        self._pont_page = None
        
        if not self._actif:
            return

        try:
            self._vue = QWebEngineView(parent)
            # Laisser apparaître le widget situé derrière l'avatar : le HTML
            # et le renderer Three.js utilisent déjà un canvas avec alpha.
            self._vue.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._vue.setAutoFillBackground(False)
            self._vue.setStyleSheet("background: transparent; border: none;")
            self._vue.page().setBackgroundColor(QColor(0, 0, 0, 0))
            settings = self._vue.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

            self._configurer_pont_page()
            
            self._vue.loadFinished.connect(self._au_chargement_fini)
            self._executer_js.connect(
                self._slot_executer_js, 
                type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.QueuedConnection
            )
            self._planifier_js.connect(
                self._slot_planifier,
                type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.QueuedConnection
            )
            self._repos_planifie.connect(
                self._slot_repos_planifie,
                type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.QueuedConnection
            )
            self._evaluer_js.connect(
                self._slot_evaluer,
                type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.QueuedConnection
            )

            # --- Servir l'avatar via HTTP local pour permettre les modules ES et les assets GLB. ---
            from core.avatar_server import AvatarServer
            self._server = AvatarServer(port=8765)
            url = self._server.get_url()
            self._vue.load(QUrl(url))
            print(f"[Sarra] Chargement via HTTP: {url}")
        except Exception as e:
            log.error("Échec init WebEngine (%s). Avatar 3D désactivé.", e)
            self._actif = False
            self._vue = None

    def _configurer_pont_page(self) -> None:
        """Expose un objet Python à la page (bouton micro HTML).

        qwebchannel.js est servi localement par AvatarServer et chargé par
        index.html ; ici on ne fait que créer le canal et enregistrer l'objet.
        """
        if self._vue is None or QWebChannel is None:
            return
        try:
            self._channel = QWebChannel(self._vue.page())
            self._pont_page = _AvatarPageBridge(self)
            self._pont_page.mic_toggle.connect(self.mic_toggle)
            self._channel.registerObject("avatarBridge", self._pont_page)
            self._vue.page().setWebChannel(self._channel)
        except Exception as e:
            log.warning("Pont QWebChannel indisponible (%s).", e)

    # -- API publique --

    @property
    def widget(self):
        return self._vue

    @property
    def disponible(self) -> bool:
        return self._actif and self._vue is not None

    def set_viseme_nom(self, nom: str, intensite: float) -> None:
        """Variante acceptant les noms de core/viseme.py ('AA', 'MBP', …)."""
        if not self.disponible:
            return
        vid = VISEME_IDS.get(str(nom).upper().strip(), 0)
        self.set_viseme(vid, float(intensite))

    def set_viseme(self, viseme_id: int, intensite: float) -> None:
        """Envoie un visème (0..14, intensité 0..1) au JS, sans attendre."""
        if not self.disponible:
            return
        try:
            vid = max(0, min(14, int(viseme_id)))
            k = max(0.0, min(1.0, float(intensite)))
        except (TypeError, ValueError):
            return
        self._envoyer_js(f"window.updateViseme({vid}, {k:.3f})")

    def reset_visemes(self) -> None:
        """Remet la bouche au repos."""
        if self.disponible:
            self._envoyer_js("window.resetVisemes()")

    def load_model(self, gender: str) -> None:
        """Recharge à chaud le modèle féminin ou masculin."""
        if not self.disponible:
            return
        genre = "male" if str(gender).lower().strip() == "male" else "female"
        self._envoyer_js(f"window.loadModel({json.dumps(genre)})")

    def set_avatar_only_mode(self, enabled: bool) -> None:
        """Masque ou affiche les contrôles intégrés à la vue avatar."""
        if self.disponible:
            self._envoyer_js(f"window.setAvatarOnlyMode({str(bool(enabled)).lower()})")

    def cadrer_visage(self) -> None:
        """Recadre la caméra sur le visage, face caméra (gros plan)."""
        if self.disponible:
            self._envoyer_js("window.cadrerVisage()")

    def apercu_viseme(self, viseme_id: int, intensite: float) -> None:
        """Calibration : tient un visème (0..14) à l'intensité donnée."""
        if self.disponible:
            try:
                vid = max(0, min(14, int(viseme_id)))
                k = max(0.0, min(1.0, float(intensite)))
            except (TypeError, ValueError):
                return
            self._envoyer_js(f"window.updateViseme({vid}, {k:.3f})")

    def relacher_visemes(self) -> None:
        """Calibration : relâche la bouche tenue en aperçu."""
        self.reset_visemes()

    def set_jaw_gain(self, gain: float) -> None:
        """Calibration (direct) : amplifie jawOpen, 0.2..2.0."""
        if self.disponible:
            try:
                g = max(0.2, min(2.0, float(gain)))
            except (TypeError, ValueError):
                return
            self._envoyer_js(f"window.setJawGain({g:.3f})")

    def set_articulation_gain(self, gain: float) -> None:
        """Calibration (direct) : amplifie l'intensité des formes, 0.2..2.0."""
        if self.disponible:
            try:
                g = max(0.2, min(2.0, float(gain)))
            except (TypeError, ValueError):
                return
            self._envoyer_js(f"window.setArticulationGain({g:.3f})")

    def set_fluidite(self, valeur: float) -> None:
        """Calibration (direct) : vitesse des transitions, 0.5..1.5."""
        if self.disponible:
            try:
                v = max(0.5, min(1.5, float(valeur)))
            except (TypeError, ValueError):
                return
            self._envoyer_js(f"window.setFluidite({v:.3f})")

    def set_force_fermetures(self, gain: float) -> None:
        """Calibration (direct) : appuie PP/FF/SS/mouthClose, 0.2..2.0."""
        if self.disponible:
            try:
                g = max(0.2, min(2.0, float(gain)))
            except (TypeError, ValueError):
                return
            self._envoyer_js(f"window.setForceFermetures({g:.3f})")

    def set_amplitude(self, gain: float) -> None:
        """Calibration (direct) : taille globale des mouvements bouche, 0.2..2.0."""
        if self.disponible:
            try:
                g = max(0.2, min(2.0, float(gain)))
            except (TypeError, ValueError):
                return
            self._envoyer_js(f"window.setAmplitude({g:.3f})")

    def set_trim_viseme(self, viseme_id: int, comp: str, valeur: float) -> None:
        """Calibration fine : trim open/close/stretch/round (0..2) d'un visème."""
        if self.disponible:
            try:
                vid = max(0, min(14, int(viseme_id)))
                v = max(-0.5, min(2.0, float(valeur)))
            except (TypeError, ValueError):
                return
            comp = str(comp or "").lower()
            if comp not in ("open", "close", "stretch", "round"):
                return
            self._envoyer_js(f"window.setTrimViseme({vid}, {json.dumps(comp)}, {v:.3f})")

    def reset_trims(self) -> None:
        """Calibration : efface tous les trims par visème (retour 1.0)."""
        if self.disponible:
            self._envoyer_js("window.resetTrims()")

    def set_sync_offset(self, ms: float) -> float:
        """Calibration temps réel : décalage bouche en ms (-500..+500,
        négatif = bouche plus tôt). Stocké ici, appliqué à l'émission."""
        try:
            self._sync_offset = max(-500.0, min(500.0, float(ms)))
        except (TypeError, ValueError):
            pass
        return self._sync_offset

    def set_seuil_silence(self, seuil: float) -> float:
        """Calibration : sous ce niveau (0..0.10) la bouche se ferme."""
        try:
            self._seuil_silence = max(0.0, min(0.10, float(seuil)))
        except (TypeError, ValueError):
            pass
        return self._seuil_silence

    def jouer_mot(self, mot: str) -> None:
        """Calibration : mime un mot tapé, au rythme naturel des phonèmes.

        La découpe vient du vrai pipeline (core.viseme.text_to_visemes) :
        le test montre ce que la bouche fera sur de vrais mots.
        """
        if not self.disponible:
            return
        try:
            from core.viseme import text_to_visemes, _DUR
        except Exception:
            return
        try:
            seq = [(n, _DUR.get(n, 1.0)) for n, _w in text_to_visemes(mot)]
        except Exception:
            return
        if not seq:
            return
        self._jouer_pas(seq, 0)

    def _jouer_pas(self, seq: list, i: int) -> None:
        if i >= len(seq):
            self.reset_visemes()
            return
        nom, poids = seq[i]
        self.set_viseme_nom(nom, 0.9)
        QTimer.singleShot(max(40, int(95 * float(poids or 1.0))),
                          lambda: self._jouer_pas(seq, i + 1))

    def jouer_sequence_test(self) -> None:
        """Calibration : enchaîne des formes contrastées puis relâche."""
        if self.disponible:
            self._envoyer_js("window.jouerSequenceTest()")

    def push_frames(self, frames: list, hop: float, at: float | None = None) -> None:
        """Envoie les frames audio (level, openness, width[, shape]) au JS.

        Depuis que VisemeStream joint le nom de forme du transcript (MBP, FV,
        S…), on le transmet tel quel avec l'ouverture continue : le JS mime la
        consonne exacte et pilote la mâchoire en continu, au lieu de requantifier
        l'ouverture en 5 voyelles. Couche visuelle seule, audio intact.

        Émission PLANIFIÉE sur l'horloge audio (at + i*hop + décalage), comme
        le HUD 2D : envoyées d'un coup, les formes devançaient le son de la
        latence de sortie. Sans `at` (tests), émission immédiate.
        """
        if not self.disponible or not frames:
            return
        try:
            hop = float(hop)
        except (TypeError, ValueError):
            hop = 0.02
        dt = hop * len(frames)
        try:
            batch_peak = max(float(f[0]) for f in frames)
        except (TypeError, ValueError, IndexError):
            batch_peak = 0.0
        self._v_peak = max(batch_peak, self._v_peak - dt * 0.55)
        ref = max(0.18, self._v_peak)
        try:
            base = float(at) if at is not None else time.time()
        except (TypeError, ValueError):
            base = time.time()
        maintenant = time.time()
        decalage = self._sync_offset / 1000.0
        seuil = self._seuil_silence
        self._batch_id += 1
        for i, frame in enumerate(frames):
            try:
                level = max(0.0, min(1.0, float(frame[0])))
                openness = max(0.0, min(1.0, float(frame[1]))) if len(frame) > 1 else 0.0
                nom = str(frame[3]).upper() if len(frame) > 3 and frame[3] else ""
            except (TypeError, ValueError, IndexError):
                continue
            if not nom or level <= seuil:
                js = "window.updateVisageNom(\"REST\", 0, 0)"
            else:
                k = min(1.0, (level / ref) * (0.6 + openness))
                js = (f"window.updateVisageNom({json.dumps(nom)}, "
                      f"{openness:.3f}, {k:.3f})")
            delai = max(0.0, (base + i * hop + decalage) - maintenant)
            self._planifier_js.emit(js, int(delai * 1000))
        # Chien de garde : si rien de neuf 400 ms après la fin du lot, la
        # bouche se ferme (fin de phrase sans trame de silence explicite).
        fin_ms = int(max(0.0, (base + dt + decalage) - maintenant) * 1000) + 400
        self._repos_planifie.emit(self._batch_id, fin_ms)

    def _repos_si_calme(self, bid: int) -> None:
        """Ferme la bouche si aucun lot plus récent n'a pris le relais."""
        if bid == self._batch_id and self.disponible:
            self._envoyer_js("window.updateVisageNom(\"REST\", 0, 0)")
            self._v_peak = min(self._v_peak, 0.25)

    def fond_calibration(self, actif: bool) -> None:
        """Affiche/masque le fond témoin magenta pour la mesure bouche."""
        if self.disponible:
            self._envoyer_js(
                f"window.fondCalibration({str(bool(actif)).lower()})")

    def demander_mesure(self, rappel) -> None:
        """Lit {ok, cavite, dents, ouverture, largeur, visage} via callback
        (thread GUI). Sans pont : rappel(None)."""
        if not self.disponible:
            try:
                rappel(None)
            except Exception:
                pass
            return
        self._jeton_mesure += 1
        tok = self._jeton_mesure
        self._rappels_mesure[tok] = rappel
        self._evaluer_js.emit("window.mesurerBouche()", tok)

    @pyqtSlot(str, int)
    def _slot_evaluer(self, script: str, jeton: int) -> None:
        try:
            if self._vue is not None:
                self._vue.page().runJavaScript(
                    script, lambda res: self._retour_mesure(jeton, res))
        except Exception as e:
            log.error("évaluation JS impossible : %s", e)
            self._retour_mesure(jeton, None)

    def _retour_mesure(self, jeton: int, resultat) -> None:
        rappel = self._rappels_mesure.pop(jeton, None)
        if rappel is None:
            return
        try:
            rappel(resultat)
        except Exception as e:
            log.error("rappel mesure : %s", e)

    @pyqtSlot(str, int)
    def _slot_planifier(self, script: str, delai_ms: int) -> None:
        """Thread GUI : émission JS retardée (timers interdits hors GUI)."""
        try:
            QTimer.singleShot(max(0, int(delai_ms)),
                              lambda s=script: self._envoyer_js(s))
        except Exception as e:
            log.error("planification JS impossible : %s", e)

    @pyqtSlot(int, int)
    def _slot_repos_planifie(self, bid: int, delai_ms: int) -> None:
        """Thread GUI : chien de garde silence, programmé côté GUI."""
        try:
            QTimer.singleShot(max(0, int(delai_ms)),
                              lambda: self._repos_si_calme(bid))
        except Exception as e:
            log.error("watchdog silence impossible : %s", e)

    def set_etat(self, state: str) -> None:
        """Émotion faciale selon l'état : écoute = sourire attentif, réflexion
        = sourcils froncés, parole/neutre = doux (bouche libre pour parler)."""
        if not self.disponible:
            return

        state_lower = str(state).lower()
        if 'listen' in state_lower:
            js_expr = "window.setEmotion('ecoute')"
        elif 'think' in state_lower or 'process' in state_lower:
            js_expr = "window.setEmotion('reflechi')"
        else:
            js_expr = "window.setEmotion('neutre')"

        if js_expr:
            self._envoyer_js(js_expr)

    def set_emotion(self, nom: str, intensite: float = 1.0) -> None:
        """Émotion directe : neutre, ecoute, sourire, grand_sourire, joie,
        triste, colere, surpris, reflechi (0..1)."""
        if not self.disponible:
            return
        try:
            k = max(0.0, min(1.0, float(intensite)))
        except (TypeError, ValueError):
            return
        self._envoyer_js(f"window.setEmotion({json.dumps(str(nom))}, {k:.3f})")

    def set_mic_muted(self, muted: bool) -> None:
        """Synchronise l'état du bouton micro HTML avec MainWindow."""
        if not self.disponible:
            return
        self._envoyer_js(
            f"window.setAvatarMicMuted({str(bool(muted)).lower()})")

    # -- Internes --

    def _envoyer_js(self, script: str) -> None:
        if not self._pret:
            if len(self._file) < 200:
                self._file.append(script)
            return
        self._executer_js.emit(script)

    @pyqtSlot(bool)
    def _au_chargement_fini(self, ok: bool) -> None:
        if not ok:
            log.error("index.html Sarra introuvable. Lancez setup_assets.py.")
            return
        self._pret = True
        file, self._file = self._file, []
        for script in file:
            self._executer_js.emit(script)

    @pyqtSlot(str)
    def _slot_executer_js(self, script: str) -> None:
        try:
            if self._vue is not None:
                self._vue.page().runJavaScript(script, self._retour_js)
        except Exception as e:
            log.error("runJavaScript a échoué : %s", e)

    @staticmethod
    def _retour_js(resultat) -> None:
        # setExpression répond False quand la forme n'existe pas sur le modèle
        # (ex. 'listening') : nominal, pas une erreur — seul un retour
        # inattendu autre que True/False/None mérite un warning.
        if resultat is not True and resultat is not None and resultat is not False:
            log.warning("Retour JS inattendu : %r", resultat)