# Sarra — Assistante IA vocale pour ordinateur

Sarra est une assistante IA locale conçue pour dialoguer avec l’utilisateur à la voix, présenter une avatar 3D et exécuter certaines actions sur l’ordinateur à travers des outils dédiés.

<p align="center">
  <img src="sarra-presentation.png" alt="Sarra dans son interface de bureau" width="900">
</p>

Ce README décrit **Sarra telle qu’elle est développée dans ce projet**. Il ne reprend pas le texte promotionnel du document de référence : les fonctionnalités ci-dessous sont formulées pour Sarra et doivent être confirmées dans le code et lors des tests avant toute publication.

> **État du projet :** en développement. Certaines fonctions peuvent être expérimentales ou nécessiter une validation sur la machine cible.

## Vidéo de présentation

Découvrez Sarra en vidéo :

[▶️ Regarder la vidéo de Sarra sur YouTube](https://www.youtube.com/watch?v=Rb-d53cW6qM)

## Présentation

Sarra combine une interface de bureau, une conversation vocale basée sur Gemini Live et un avatar 3D. Selon la configuration et les outils activés, elle peut aussi aider à effectuer des recherches, consulter des pages web, traiter des fichiers et exécuter des actions autorisées.

Le projet vise une interaction plus naturelle avec l’ordinateur, tout en gardant l’utilisateur informé et maître des actions réalisées.

## Fonctionnalités

Les éléments ci-dessous reflètent les capacités évoquées et travaillées dans le projet. Leur disponibilité exacte dépend de la version, de la configuration et des outils activés.

- **Conversation vocale** : échanges avec le modèle Gemini Live.
- **Avatar 3D** : affichage d’un avatar VRM dans l’interface.
- **Synchronisation labiale** : animation de la bouche pendant la parole, selon le modèle et le flux audio utilisés.
- **Recherche web** : lancement de recherches et présentation de résultats disponibles.
- **Contrôle du navigateur** : ouverture de Chrome et navigation lorsque l’outil correspondant est configuré.
- **Actions sur l’ordinateur** : exécution d’actions exposées par les outils de Sarra.
- **Mémoire locale** : conservation de certains éléments de contexte et de continuité entre les sessions.
- **Missions** : enchaînement de plusieurs étapes, avec suivi de l’état et restitution des résultats.
- **Permissions et confirmations** : certaines actions nécessitent une confirmation explicite. Les autorisations sont limitées selon l’action et le contexte.
- **Rapports** : création de rapports à partir des informations réellement récupérées.

Sarra ne doit pas présenter une recherche, une action ou un rapport comme terminé si son exécution n’a pas abouti ou si les sources ne sont pas disponibles.

## Principes de fonctionnement

### L’utilisateur garde la priorité

Sarra doit laisser à l’utilisateur la possibilité de prendre la parole, interrompre sa réponse selon le mécanisme prévu par l’application et éviter les monologues inutiles. Le comportement vocal peut varier selon le modèle, la connexion et la configuration.

### Des résultats vérifiables

Les rapports doivent s’appuyer sur les résultats réellement obtenus. Les liens et informations non vérifiés ne doivent pas être présentés comme des sources confirmées. En cas d’échec ou de résultats insuffisants, Sarra doit le signaler clairement.

### Des autorisations explicites

Les actions sont soumises au mécanisme d’autorisation de l’application. Une confirmation porte sur une action identifiée et ne vaut pas permission générale pour des actions différentes ou sensibles.

## Architecture

L’application est organisée autour de plusieurs composants :

| Composant | Rôle |
|---|---|
| `main.py` | Point d’entrée et orchestration de l’application |
| `core/` | Logique conversationnelle, gestion des missions, permissions et composants partagés |
| `actions/` | Outils/actions disponibles à Sarra |
| `memory/` | Gestion de la mémoire et de la continuité locale |
| `assets/` | Ressources de l’interface et de l’avatar |
| `tests/` | Tests automatisés |

> Cette structure est indicative. Vérifiez l’arborescence réelle de la version publiée et ajustez cette section avant diffusion.

## Prérequis

Les prérequis peuvent varier selon la version :

- Windows et un environnement Python compatible avec les dépendances du projet.
- Microphone et sortie audio pour la conversation vocale.
- Une clé API Gemini configurée pour les fonctionnalités qui l’utilisent.
- Chrome, si les actions de navigateur sont activées.
- Les dépendances Python et ressources 3D requises par la version.

Consultez les fichiers de configuration et de dépendances du dépôt pour connaître les versions exactes.

## Installation et lancement

Depuis le répertoire du projet :

```bash
# Créer et activer un environnement virtuel (recommandé)
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# Installer les dépendances, selon le fichier fourni par le projet
pip install -r requirements.txt

# Lancer Sarra
python main.py
```

Si le dépôt utilise un autre fichier d’installation ou un autre point d’entrée, suivez les instructions correspondantes. Ne publiez pas de clé API dans le dépôt.

Au premier lancement, Sarra affiche son écran d’initialisation. Saisissez votre clé Gemini ou laissez le champ vide si une clé est déjà enregistrée, vérifiez le système détecté, puis choisissez **INITIALISER SARRA**. La clé reste masquée et suit le stockage local existant dans `config/api_keys.json`. Les lancements suivants ouvrent directement l’interface lorsque la configuration est valide.

Pour remplacer la clé plus tard, ouvrez le menu **⚙ Setup** puis **GEMINI API KEY**. La nouvelle clé est utilisée à la prochaine connexion Gemini. Si Gemini rejette la clé, l’écran de configuration demandera explicitement une nouvelle clé.

## Configuration

Configurez les clés et paramètres requis par l’application en suivant les instructions présentes dans le dépôt.

- Gardez les clés API dans un fichier local ignoré par Git ou dans des variables d’environnement.
- Ne partagez pas les fichiers de configuration contenant des secrets.
- Vérifiez les permissions accordées aux actions et aux outils avant d’utiliser Sarra sur des données importantes.

## Confidentialité et sécurité

Sarra peut traiter des données provenant du microphone, du navigateur, de fichiers locaux ou d’autres outils, selon les fonctions activées.

- Le flux vocal destiné à Gemini Live est envoyé au service distant pendant la session active.
- Les données conservées dans la mémoire locale restent sur la machine, sous réserve des fonctions effectivement activées.
- Les actions sur l’ordinateur doivent rester soumises aux permissions applicables.
- Avant de publier le projet, vérifiez que les clés API, données personnelles, journaux, fichiers mémoire et certificats ne sont pas inclus dans le dépôt.

Ce README ne constitue pas une garantie de sécurité ou de confidentialité. Examinez la configuration et le code de la version que vous utilisez.

## Tests

Les tests automatisés peuvent être lancés depuis la racine du projet. Pour un projet utilisant `unittest` :

```bash
python -m unittest discover -s tests
```

Les tests automatisés ne remplacent pas les essais réels avec microphone, haut-parleurs, navigateur, connexion Gemini Live et actions sur le système.

## Limites connues

- La qualité et la latence de la conversation dépendent du service distant, du réseau et du matériel audio.
- Les résultats web peuvent être incomplets ou indisponibles.
- Certaines actions nécessitent une confirmation ou peuvent ne pas être prises en charge sur toutes les configurations.
- Les fonctions expérimentales peuvent changer au fil du développement.
- Les comportements doivent être validés sur la version et la machine réellement utilisées.

## Contributions

Avant de proposer une modification :

1. Consultez l’architecture et les consignes du dépôt.
2. Évitez d’ajouter des permissions plus larges que nécessaire.
3. Ajoutez ou adaptez les tests associés.
4. Décrivez les changements et les limitations dans la demande de contribution.

## Dépendances et attribution

Sarra peut s’appuyer sur des bibliothèques, modèles, services et ressources tiers. Chaque dépendance ou ressource distribuée doit être accompagnée de l’attribution et de la licence requises par son auteur.

Avant publication, vérifiez notamment :
- les licences des bibliothèques Python et JavaScript ;
- la licence et les conditions d’utilisation du modèle VRM et des ressources 3D ;
- les conditions des API et services utilisés ;
- les éventuelles obligations de redistribution.

N’incluez pas dans Sarra des fichiers, assets, textes, modèles ou extraits de code issus d’un autre projet sans vérifier leurs licences et obtenir les autorisations nécessaires.

## Licence du projet

**À compléter par le propriétaire du projet.**

N’ajoutez une licence qu’après avoir décidé des conditions de réutilisation, de modification et de redistribution du code et des ressources que vous possédez ou êtes autorisé à distribuer.

## État du développement

Sarra évolue encore. Les fonctionnalités, l’architecture et les instructions de ce README doivent être mises à jour au fur et à mesure des changements réellement intégrés et testés.
