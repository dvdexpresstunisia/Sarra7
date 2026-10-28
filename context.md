# Contexte de l'application — MARK LV

> **RÈGLE OBLIGATOIRE POUR TOUTE INTERVENTION : lire entièrement ce fichier avant de modifier le projet. Après chaque modification du code, mettre automatiquement ce fichier à jour afin qu'il reflète toujours l'architecture, les commandes, les dépendances et les décisions actuelles de l'application.**

## 1. Identification

- **Nom :** MARK LV
- **Type :** assistant personnel vocal et visuel multiplateforme.
- **Langage principal :** Python.
- **Point d'entrée :** `main.py`.
- **Interface :** PyQt6, avec avatar holographique rendu côté logiciel.
- **Avatar 3D Sarra :** Three.js chargé dans `PyQt6-WebEngine` via `core/avatar_bridge.py` et servi par `core/avatar_server.py` sur `http://127.0.0.1:8765`.
- **Types MIME avatar :** `AvatarHandler` sert explicitement les fichiers `.glb`, `.gltf`, `.hdr`, `.bin`, `.js`, `.html`, `.css` et `.json` avec leurs types MIME adaptés.
- **Modèles 3D :** `assets/avatar/avatar_female.glb` et `assets/avatar/avatar_male.glb`, sélectionnables à chaud.
- **Téléchargement des modèles :** `python setup_assets.py --gender female|male`; les URLs réelles RPM peuvent être fournies avec `--female-url` et `--male-url` ou `SARRA_FEMALE_URL` / `SARRA_MALE_URL`.
- **IA temps réel :** Gemini Live via `google-genai`.
- **Raisonnement texte :** `core/reasoning_router.py`, providers OpenAI-compatible configurés dans
  `config/providers.json`; paramètres et clés de raisonnement locaux dans
  `config/reasoning_state.json` et `config/api_keys.json`; Gemini Live n'est pas utilisé pour ces requêtes.
- **Systèmes ciblés :** Windows 10/11, macOS et Linux.
- **Version Python attendue :** Python 3.11 à 3.13 selon la documentation du projet. Une version plus récente peut fonctionner, mais n'est pas la version de référence testée. Le lanceur Windows `start.bat` privilégie désormais `.venv`, puis `py -3.13`, `py -3.12` et `py -3.11`, sans forcer `3.14`.

## 2. Démarrage

### Windows

Double-cliquer sur `start.bat` ou exécuter depuis un terminal :

```bat
start.bat
```

La commande applicative équivalente est :

```bat
python main.py
```

### Installation initiale

Depuis la racine du projet :

```bat
python setup.py
```

Le script installe les dépendances de `requirements.txt` adaptées au système d'exploitation et tente d'installer les navigateurs Playwright nécessaires à l'automatisation web.

### Configuration initiale

Au premier lancement, l'application demande la clé API Gemini. La configuration est conservée dans `config/api_keys.json`. Ne jamais publier cette clé ni l'ajouter à un rapport de bug ou à un commit.

## 3. Architecture

```text
main.py                 Boucle principale, audio, session Gemini Live,
                        détection des outils et dispatch des actions
ui.py                   Interface PyQt6, HUD, avatar, journal et paramètres
setup.py                Installation des dépendances et vérifications initiales
setup_assets.py         Téléchargement/préparation des assets de l'avatar
actions/                Compétences/outils pilotant l'ordinateur et les services
core/                   Moteur Gemini, audio, avatar, plugins, vision,
                        confirmation, undo, wake word et utilitaires centraux
                         ; `reasoning_router.py` route le raisonnement texte
memory/                 Mémoire longue durée et configuration locale
plugins/                Extensions auto-découvertes (voir `_template.py`)
dashboard/              Serveur et interface du dashboard local
assets/                 Assets statiques, notamment l'avatar
config/                 Configuration locale, dont la clé API
                        et `providers.json` (sans secrets)
```

Les actions de `actions/` se décrivent généralement avec un dictionnaire `TOOL` et un handler. Elles sont découvertes automatiquement par `core/action_loader.py`. Les plugins de `plugins/` suivent le même principe d'auto-découverte ; commencer par lire `plugins/_template.py` avant d'en ajouter un.

Les confirmations sont centralisées dans `core/confirm.py` : états IDLE, WAITING_CONFIRMATION, CONFIRMED, REJECTED, CANCELLED et EXPIRED ; chaque demande conserve un identifiant, l'action exacte, ses paramètres et l'identifiant de mission, avec expiration après 90 secondes. La voix utilise exclusivement `server_content.input_transcription` dans `main.py` ; les transcriptions de sortie de l'assistante ne résolvent jamais une demande. L'EchoGuard, les seuils audio et l'interruption Espace restent inchangés. Les permissions de mission sont éphémères (30 minutes maximum), liées aux demandes explicites et limitées à la recherche web du sujet, à Chrome en navigation/lecture, à la création d'un nouveau rapport texte dans l'espace utilisateur, ou au copier/coller demandé. Les thèmes (concours/prix et IA/essais) sont comparés entre langues pour les recherches générées. Quand Chrome est explicitement demandé, `main.py` ouvre la recherche via `browser_control` avant le backend web potentiellement lent ; `web_search` seul reste un backend en arrière-plan et alimente le panneau de résultats. La lecture YouTube ouvre son URL dans Chrome et demande une confirmation propre à la vidéo, où le seul « oui » vocal suffit ; les autres opérations YouTube restent protégées par leur confirmation.

## 4. Fonctionnement principal

1. `main.py` crée l'interface `JarvisUI`.
2. Une thread d'exécution attend la clé API puis démarre `JarvisLive`.
3. La session Gemini Live gère les échanges audio et les appels d'outils.
4. Les modules de `core/`, `actions/` et `memory/` exécutent les fonctionnalités demandées.
5. L'interface affiche l'état, les réponses, l'avatar, la vision et les réglages.

Fonctionnalités importantes : push-to-talk, wake word optionnel, synthèse/reconnaissance vocale, vision écran/caméra, mémoire longue durée, contrôle du système, navigateur et fichiers, lecture vidéo et dashboard local.

## 5. Fichiers et données sensibles

- `config/api_keys.json` : clé API et paramètres locaux ; ne pas exposer.
- `.gitignore` exclut également les copies de packaging/données locales `000/` et `exe/`, les exécutables et sauvegardes, ainsi que les copies imbriquées de clés, certificats et mémoire ; les publier expose des secrets et dépasse les limites GitHub.
- `memory/long_term.json` : mémoire persistante de l'utilisateur ; ne pas supprimer ou réinitialiser sans demande explicite. Le mode développement utilise cette copie source, tandis que l'exécutable utilise `exe/Sarra/memory/long_term.json` à côté de `Sarra.exe`. Au démarrage figé, les deux copies adjacentes sont réconciliées explicitement : les entrées les plus récentes gagnent, les données uniques sont conservées, et une sauvegarde exacte préalable est créée avant toute fusion atomique.
- `assets/avatar/avatar_config.json` : modèle Sarra sélectionné par défaut par `setup_assets.py`.
- `config/avatar_camera.json` : cadrage avatar/caméra (échelle, position, rotation, caméra) et masque d'affichage (forme, taille, position), sauvegardé automatiquement après chaque réglage ou orbite/zoom, et restauré au démarrage suivant.
- `config/avatar_window.json` : taille et position de la fenêtre avatar flottante, sauvegardées automatiquement et restaurées exactement au démarrage suivant. Seul le centre de la fenêtre est maintenu sur un écran (les bords transparents peuvent dépasser), afin de laisser l'avatar atteindre tous les bords tout en gardant la surface de glissement accessible ; le même glisser fonctionne en 3D et en repli 2D.
- Les fichiers générés localement et les secrets doivent rester couverts par `.gitignore`.

## 6. Règles de modification

- Lire `context.md`, `readme.md` et les fichiers concernés avant toute modification.
- Respecter les conventions Python et l'organisation existante ; ne pas ajouter de framework ou de dépendance sans vérifier qu'elle est nécessaire et compatible.
- Pour une nouvelle action, privilégier le modèle auto-descriptif existant dans `actions/`.
- Préserver la compatibilité Windows, macOS et Linux lorsqu'une modification concerne du système, des chemins, l'audio ou l'automatisation.
- Ne pas mettre en dur de clé API, de chemin utilisateur ou de secret.
- Les clés de raisonnement sont saisies dans le panneau natif PyQt6 et conservées
  dans `config/api_keys.json`, déjà couvert par `.gitignore`; `config/providers.json`
  ne contient que les métadonnées. Elles ne sont jamais placées dans le renderer,
  les journaux ou le journal d'audit `config/reasoning_audit.jsonl`.
- Le moteur utilise un seul adaptateur `POST /chat/completions`, découvre les
  modèles via `GET /models`, borne la température à 0.1, valide une sortie JSON,
  réalise une seconde passe critique en mode rigoureux et bascule explicitement
  sur les providers suivants en cas d'erreur réseau/API.
- `core/gemini.py` reste inchangé pour préserver Gemini Live. `main.py` route
  uniquement la transcription d'entrée vers `core.confirm` ; aucune modification
  des seuils audio, du moteur vocal ou de l'interruption Espace. Le panneau
  affiche « Voix : Gemini » en lecture seule.
- Après **chaque** modification du code, mettre à jour ce fichier si le comportement, l'architecture, les dépendances, la configuration ou les commandes changent. Cette mise à jour est obligatoire et fait partie de la modification.

## 7. Validation avant livraison

- Vérifier la syntaxe des fichiers Python modifiés :

```bat
python -m compileall main.py ui.py actions core memory dashboard
```

- Vérifier que le point d'entrée est toujours `main.py`.
- Tester `start.bat` sur Windows avec les dépendances installées.
- Pour une modification d'interface, vérifier que PyQt6 démarre sans erreur.
- Pour une modification audio ou Gemini, vérifier la configuration locale sans afficher de secret.
- Mettre à jour la section concernée de ce fichier avant de terminer.

## 8. Journal de maintenance

| Date | Modification | Fichiers concernés |
|---|---|---|
| 2026-10-28 | Audit des autorisations et confirmation vocale : états explicites, ID d'action unique, réponses oui/non/annule avec clarification des ambiguïtés, expiration, garde anti-rejeu, portée de mission éphémère, recherche visible dans Chrome avant le backend avec correspondance thématique multilingue, et confirmation YouTube playback par « oui » seul pour une action précise. Cause racine : instructions de prompt contradictoires, recherches web/plugin par défaut confirmées, aucun lien entre la transcription utilisateur et la confirmation HUD, et exemptions globales incohérentes avec les consignes. Aucune reconstruction de l'exécutable. | `core/confirm.py`, `core/action_loader.py`, `actions/web_search.py`, `actions/browser_control.py`, `actions/youtube_video.py`, `actions/computer_control.py`, `actions/file_controller.py`, `main.py`, `ui.py`, `core/prompt.txt`, `core directives.txt`, `tests/test_action_authorization.py`, `context.md`, `readme.md` |
| 2026-10-28 | Continuité persistante étendue dans le stockage mémoire existant : conversations horodatées conservées, résumés non consommables, état structuré du projet actif, restauration limitée au démarrage, quatre états d'action vérifiés, archivage explicite, écriture atomique, quarantaine des données corrompues et persistance synchrone avant fermeture. Les données initiales ont été sauvegardées avant migration. | `memory/memory_manager.py`, `memory/long_term.json`, `main.py`, `ui.py`, `core/prompt.txt`, `actions/background_monitor.py`, `tests/test_memory_continuity.py`, `context.md` |
| 2026-10-27 | Les **core directives** sont désormais éditables directement dans le panneau de configuration et enregistrées par `APPLY CHANGES` : `core/prompt.txt` devient le prompt officiel chargé par `main.py`, `core directives.txt` reste synchronisé et `prompt.txt.bak` conserve la version précédente. La sauvegarde depuis le grand éditeur alimente aussi le panneau. Les jetons `{assistant_name}` et `{platform}` sont obligatoires ; une sauvegarde valide reconstruit la session Live sans reprise de contexte afin de garantir que les nouvelles instructions remplacent réellement les anciennes. | `ui.py`, `main.py`, `context.md` |
| 2026-10-26 | Persistance du cadrage 3D (échelle, position, caméra, masque) dans `config/avatar_camera.json` : sauvegarde automatique débouncée après réglage ou orbite/zoom, et restauration au démarrage sans recadrage forcé si la configuration est valide. | `assets/avatar/app.js`, `context.md` |
| 2026-10-27 | Persistance de la taille et de la position de la fenêtre avatar flottante dans `config/avatar_window.json`, restaurée exactement au démarrage. Sauvegarde débouncée sur déplacement/redimensionnement, immédiate au relâchement de la souris, à la fermeture et au passage en interface complète. Surface de glisser ajoutée aussi au repli 2D, avec filtre au niveau application (la surface WebEngine peut recouvrir l'overlay), déplacement natif Windows (`startSystemMove`) et repli manuel ; seul le centre de la fenêtre est contraint à un écran pour que l'avatar puisse atteindre tous les bords sans perdre la poignée. | `ui.py`, `.gitignore`, `context.md` |
| 2026-10-26 | Micro HTML remonté de 3 cm depuis sa position en bas au centre (`bottom: calc(10px + 3cm)`), pour le rapprocher du bas du visage. | `assets/avatar/index.html`, `context.md` |
| 2026-10-26 | Suppression du bouton `⚙ AVATAR / CAMÉRA` de la page avatar (il n'est plus créé) et déplacement du bouton micro HTML en bas au centre. Le panneau de réglage reste accessible via le bouton AVATAR en interface complète, mais plus en mode avatar flottant. | `assets/avatar/index.html`, `assets/avatar/app.js`, `context.md` |
| 2026-10-26 | Bouton micro passé en HTML dans `index.html` (au-dessus du canvas Three.js et du masque), relié à Python par `QWebChannel` (`qwebchannel.js` servi localement). Le bouton natif ne sert plus qu'au repli 2D. Masque par défaut = tête seule en ovale, avec préréglage `TÊTE SEULE` (cercle ou ovale). | `ui.py`, `core/avatar_bridge.py`, `assets/avatar/index.html`, `assets/avatar/app.js`, `assets/avatar/qwebchannel.js`, `config/avatar_camera.json`, `context.md` |
| 2026-10-26 | Remplacement des directives système de l'avatar par le protocole Kenza orienté hackathons, concours et essais gratuits d'IA. `core/prompt.txt` (fichier réellement chargé) et `core directives.txt` sont synchronisés ; les jetons `{assistant_name}` et `{platform}` sont conservés. | `core/prompt.txt`, `core directives.txt`, `context.md` |
| 2026-10-26 | Bouton micro on/off posé sur l'avatar (fenêtre flottante et HUD 2D/3D) et masque circulaire/ovale réglable (forme, taille, position) dans le panneau `⚙ AVATAR / CAMÉRA`, persisté dans `config/avatar_camera.json`. Le panneau reste accessible en mode avatar flottant. | `ui.py`, `assets/avatar/index.html`, `assets/avatar/app.js`, `context.md` |
| 2026-10-15 | Séparation du raisonnement et de Gemini Live : catalogue de providers gratuits, adaptateur OpenAI-compatible strict, persistance locale des clés, panneau PyQt6 et audit sans secrets. OpenCode/FreeLLMAPI restent TODO à vérifier. | `config/providers.json`, `core/reasoning_router.py`, `ui.py`, `context.md` |
| 2026-10-15 | Reprise sûre du provider par défaut après une ancienne préférence invalide et conservation d'une clé existante lorsque le champ masqué reste vide. | `core/reasoning_router.py`, `ui.py`, `context.md` |
| 2026-10-12 | Création du contexte de maintenance et du lanceur Windows. | `context.md`, `start.bat` |
| 2026-10-12 | Activation forcée de Sarra 3D en index 0 du `QStackedWidget` et correction des index caméra/vidéo. Ajout de la dépendance WebEngine. | `ui.py`, `requirements.txt`, `context.md` |
| 2026-10-12 | Correction du cadrage de Sarra : caméra éloignée, FOV élargi, cible recentrée sur le visage et zoom molette borné. | `assets/avatar/app.js`, `context.md` |
| 2026-10-12 | Ajout du diagnostic lip-sync : overlay visuel, logs des morphs et visèmes côté JavaScript, log des frames côté Python. | `assets/avatar/index.html`, `assets/avatar/app.js`, `core/avatar_bridge.py`, `context.md` |
| 2026-10-12 | Support de deux modèles GLB féminin/masculin, sélection CLI, validation GLB, chargement dynamique et rechargement à chaud via le bridge. | `setup_assets.py`, `assets/avatar/app.js`, `core/avatar_bridge.py`, `context.md` |
| 2026-10-12 | Renforcement du téléchargement : User-Agent/Accept standards, validation des fichiers déjà présents et écriture de la configuration uniquement après succès complet. | `setup_assets.py`, `context.md` |
| 2026-10-12 | Mode diagnostic forcé sur `assets/avatar/sarra.glb`, avec affichage des morphs et détection de `mouthSmile`. | `assets/avatar/app.js`, `context.md` |
| 2026-10-12 | Chargement de l'avatar via le serveur HTTP local `AvatarServer` afin de servir correctement les modules Three.js et les assets GLB à QtWebEngine. | `core/avatar_bridge.py`, `context.md` |
| 2026-10-12 | Affichage détaillé de tous les morph targets dans la console et dans l'overlay `debug-sarra` après détection du mesh facial. | `assets/avatar/app.js`, `context.md` |
| 2026-10-12 | Interprétation des IDs de visèmes flottants côté bridge, agrandissement de l'avatar CuteGirl, caméra rapprochée et détection élargie des morphs `mouth`/`jaw`/`viseme`. | `core/avatar_bridge.py`, `assets/avatar/app.js`, `context.md` |
| 2026-10-12 | Ajout d'un panneau de réglage avatar/caméra avec auto-cadrage du visage, chargement automatique et bouton de sauvegarde JSON local. | `assets/avatar/index.html`, `assets/avatar/app.js`, `core/avatar_server.py`, `.gitignore`, `context.md` |
| 2026-10-12 | Correction lipsync pour le GLB Sarra : sélection automatique du mesh avec morph targets et fallback par indices lorsque le GLB n'exporte pas les noms des blendshapes. | `assets/avatar/app.js`, `context.md` |
| 2026-10-13 | Ajout du routage de raisonnement multi-provider, fallbacks, prompts anti-hallucination et panneau de sélection à chaud ; Gemini Live reste réservé à la voix. | `core/reasoning_router.py`, `core/gemini.py`, `config/providers.json`, `dashboard/server.py`, `dashboard/static/app.html`, `readme.md`, `context.md` |
| 2026-10-13 | Le panneau est rendu explicitement visible sous le titre « Configuration des IA », avec actualisation manuelle et désactivation du cache HTML. | `dashboard/static/app.html`, `dashboard/server.py`, `context.md` |
