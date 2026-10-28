// Scène Three.js de Sarra : PBR + visèmes lissés 50 FPS + respiration + LookAt.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
// three-vrm est chargé en différé : si son CDN est injoignable, le GLB et le
// lip-sync existant continuent de fonctionner au lieu de tout casser.
let VRMLoaderPlugin = null, VRMUtils = null, THREE_VRM_OK = false;
async function chargerLibVRM() {
  if (THREE_VRM_OK || chargerLibVRM._essai) return THREE_VRM_OK;
  chargerLibVRM._essai = true;
  try {
    const mod = await import('three-vrm');
    VRMLoaderPlugin = mod.VRMLoaderPlugin;
    VRMUtils = mod.VRMUtils;
    THREE_VRM_OK = !!(VRMLoaderPlugin && VRMUtils);
  } catch (e) {
    console.warn('[Sarra] three-vrm injoignable, mode GLB seul :', e);
  }
  return THREE_VRM_OK;
}

// Statut visible : console + bandeau de debug (ce que l'utilisateur lit).
function statutSarra(t) {
  console.log('[Sarra] ' + t);
  try {
    const el = document.getElementById('debug-sarra');
    if (el) el.textContent = t;
  } catch (_) { /* bandeau absent en page de test */ }
}

// Modèle chargé par défaut. setup_assets.py peut remplacer ce choix dans
// avatar_config.json ; window.loadModel('male'|'female') permet de basculer à chaud.
const GENDER = (globalThis.SARRA_GENDER || 'female').toLowerCase() === 'male' ? 'male' : 'female';

// --- Lissage à pas fixe 50 Hz (indépendant du GPU / de l'écran) ---
const FPS_CIBLE = 50;
const PAS = 1.0 / FPS_CIBLE;
const FACTEUR_LERP = 0.35; // Part du chemin vers la cible à chaque pas (~60 ms de convergence).

// --- 14 visèmes standards (+ silence) -> blendshapes natifs Ready Player Me ---
// RPM expose les visèmes Oculus + jawOpen + expressions (mouthSmile...).
// Chaque entrée : liste de [nomMorph, poidsRelatif].
const TABLE_VISEMES = {
  0:  [],                                       // sil : repos
  1:  [['viseme_PP', 1.0]],                     // p, b, m (lèvres fermées)
  2:  [['viseme_FF', 1.0]],                     // f, v
  3:  [['viseme_TH', 1.0]],                     // th
  4:  [['viseme_DD', 1.0]],                     // t, d, n, l
  5:  [['viseme_kk', 1.0]],                     // k, g
  6:  [['viseme_CH', 1.0]],                     // ch, sh
  7:  [['viseme_SS', 1.0]],                     // s, z
  8:  [['viseme_nn', 1.0]],                     // n, ng
  9:  [['viseme_RR', 1.0]],                     // r
  10: [['viseme_aa', 1.0], ['jawOpen', 0.55]],   // a ouvert
  11: [['viseme_E', 1.0]],                      // e
  12: [['viseme_I', 1.0]],                      // i
  13: [['viseme_O', 1.0], ['jawOpen', 0.35]],    // o arrondi
  14: [['viseme_U', 1.0]],                      // u
};
// --- Mêmes 14 visèmes -> noms nus Oculus + ARKit (ex. houda.vrm : Avaturn) ---
// houda expose sur AvatarHead : PP FF TH DD kk CH SS nn RR aa E ih oh ou sil,
// jawOpen, mouthClose, mouthFunnel... mais ses groupes VRM ont des binds vides,
// donc l'expressionManager three-vrm ne fait rien : on pilote les morphs en direct.
// Chaque entrée : liste de [nomMorph, poidsRelatif]. Gardée par `in cibles`.
const TABLE_HOUDA_14 = {
  0:  [],
  1:  [['PP', 1.0], ['mouthClose', 0.6]],
  2:  [['FF', 1.0]],
  3:  [['TH', 1.0]],
  4:  [['DD', 1.0]],
  5:  [['kk', 1.0]],
  6:  [['CH', 1.0]],
  7:  [['SS', 1.0]],
  8:  [['nn', 1.0]],
  9:  [['RR', 1.0]],
  10: [['aa', 1.0], ['jawOpen', 0.55]],
  11: [['E', 1.0], ['jawOpen', 0.15]],
  12: [['ih', 1.0], ['jawOpen', 0.12]],
  13: [['oh', 1.0], ['jawOpen', 0.35]],
  14: [['ou', 1.0], ['mouthFunnel', 0.4]],
};
// --- Mêmes 14 visèmes -> 5 presets bouche VRM (aa/ih/ou/ee/oh) ---
// three-vrm convertit seul les presets VRM 0.x (a/i/u/e/o) vers ces noms.
// Le VRM ne connait ni les consonnes ni jawOpen : chaque consonne est
// approximée par la voyelle la plus proche (aa=10, ih=12, ee=11/12,
// oh=13, ou=14). PP (lèvres fermées) et silence visent le neutre,
// c'est-à-dire la bouche fermée : visuellement juste.
// Chaque entrée : liste de [presetVRM, poidsRelatif].
const TABLE_VRM_14 = {
  0:  [],                 // sil : repos
  1:  [],                 // p, b, m : fermé = neutre
  2:  [['ih', 0.5]],      // f, v : dents visibles, étiré
  3:  [['ih', 0.4]],      // th : étiré
  4:  [['aa', 0.35]],     // t, d, n, l : entrouvert
  5:  [['aa', 0.45]],     // k, g : mi-ouvert
  6:  [['oh', 0.7]],      // ch, sh : arrondi ouvert
  7:  [['ih', 0.6]],      // s, z : étiré étroit
  8:  [['aa', 0.3]],      // n, ng : à peine ouvert
  9:  [['ou', 0.5]],      // r : arrondi
  10: [['aa', 1.0]],      // a
  11: [['ee', 1.0]],      // e
  12: [['ih', 1.0]],      // i
  13: [['oh', 1.0]],      // o
  14: [['ou', 1.0]],      // u
};

// --- État global ---
let teteMesh = null;       // Mesh du visage portant les morph targets.
let meshesMorphs = [];     // Certains GLB n'ont pas de noms de morphs sémantiques.
let morphsSansNoms = false;
let teteOs = null;         // Os "Head" pour le suivi de caméra (LookAt).
let couOs = null;          // Os "Neck" pour la respiration.
let modeleActuel = null;
let genreActuel = GENDER;
let dicoMorphs = {};       // nom -> index dans morphTargetInfluences.
const cibles = {};         // nom -> poids visé (écrit par updateViseme).
const courants = {};       // nom -> poids affiché (avance par lerp).
const ciblesIndex = {};    // indice -> poids visé (modèles sans noms de morphs).
const courantsIndex = {};  // indice -> poids affiché (même lissage).
let dicosParMesh = [];     // [{mesh, dico}] : visages/dents secondaires à animer aussi.
// --- État VRM : null tant qu'aucun modèle n'est chargé ---
let vrmActif = null;       // instance VRM three-vrm (scene + expressionManager + lookAt)
const CIBLES_VRM = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0, blink: 0 };
const COURANTS_VRM = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0, blink: 0 };
let accumulateur = 0;
let derniereHeure = performance.now();
let souris = { x: 0, y: 0 }; // Cible du regard (-1..1), pilotée par la souris.
let pluginVRMEnregistre = false;

// --- Rendu haute qualité ---
const conteneur = document.getElementById('scene');
const rendu = new THREE.WebGLRenderer({ antialias: true, alpha: true });
rendu.setClearColor(0x000000, 0);
rendu.setPixelRatio(Math.min(window.devicePixelRatio, 2));
rendu.setSize(window.innerWidth, window.innerHeight);
rendu.toneMapping = THREE.ACESFilmicToneMapping; // Courbe cinéma, teintes peau fidèles.
rendu.toneMappingExposure = 1.0;
rendu.outputColorSpace = THREE.SRGBColorSpace;
rendu.shadowMap.enabled = true;
rendu.shadowMap.type = THREE.PCFSoftShadowMap;
conteneur.appendChild(rendu.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 100);
camera.position.set(0, 0.5, 2.5); // Plus proche et centré sur le visage

const controles = new OrbitControls(camera, rendu.domElement);
controles.target.set(0, 1.45, 0);
controles.enableDamping = true;
controles.enablePan = false;
controles.enableZoom = true;
controles.minDistance = 0.3;
controles.maxDistance = 6.0;
controles.update();

// Masque d'affichage : découpe circulaire ou ovale de la scène 3D.
// Toutes les valeurs sont des pourcentages de la fenêtre : le masque reste
// donc correct quand la fenêtre avatar est redimensionnée.
// Défaut = tête seule en ovale (le corps hors du masque disparaît).
const MASQUE_DEFAUT = { shape: 'ellipse', size: 62, height: 80, x: 50, y: 42 };

const CONFIG_DEFAUT = {
  scale: 6, position: { x: 0, y: -0.8, z: 0 }, rotationY: 0,
  camera: { x: 0, y: 0.5, z: 2.5, targetX: 0, targetY: 1.55, targetZ: 0 },
  mask: { ...MASQUE_DEFAUT }
};
// Cadrage métrique pour les VRM (modèles en mètres, corps entier) : la config
// sauvegardée d'un GLB Sketchfab (échelle 6) ne s'applique jamais au VRM.
// Défaut = gros plan visage (tête ~1.45 m sur un corps de ~1.6 m).
const CONFIG_VRM_DEFAUT = {
  scale: 1, position: { x: 0, y: 0, z: 0 }, rotationY: 180,
  camera: { x: 0, y: 1.45, z: 0.85, targetX: 0, targetY: 1.45, targetZ: 0 },
  mask: { ...MASQUE_DEFAUT }
};
let configAvatar = structuredClone(CONFIG_DEFAUT);

function normaliserConfig(source) {
  const s = source && typeof source === 'object' ? source : {};
  const p = s.position && typeof s.position === 'object' ? s.position : {};
  const c = s.camera && typeof s.camera === 'object' ? s.camera : {};
  const m = s.mask && typeof s.mask === 'object' ? s.mask : {};
  const defaut = vrmActif ? CONFIG_VRM_DEFAUT : CONFIG_DEFAUT;
  const borne = (valeur, min, max, repli) => {
    const n = Number(valeur);
    return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : repli;
  };
  return { scale: Number.isFinite(Number(s.scale)) ? Number(s.scale) : defaut.scale,
    position: { x: Number.isFinite(Number(p.x)) ? Number(p.x) : defaut.position.x, y: Number.isFinite(Number(p.y)) ? Number(p.y) : defaut.position.y, z: Number.isFinite(Number(p.z)) ? Number(p.z) : defaut.position.z },
    rotationY: Number.isFinite(Number(s.rotationY)) ? Number(s.rotationY) : 0,
    camera: { x: Number.isFinite(Number(c.x)) ? Number(c.x) : defaut.camera.x, y: Number.isFinite(Number(c.y)) ? Number(c.y) : defaut.camera.y,
      z: Number.isFinite(Number(c.z)) ? Number(c.z) : defaut.camera.z,
      targetX: Number.isFinite(Number(c.targetX)) ? Number(c.targetX) : 0,
      targetY: Number.isFinite(Number(c.targetY)) ? Number(c.targetY) : defaut.camera.targetY,
      targetZ: Number.isFinite(Number(c.targetZ)) ? Number(c.targetZ) : 0 },
    mask: {
      shape: ['none', 'circle', 'ellipse'].includes(m.shape) ? m.shape : MASQUE_DEFAUT.shape,
      size: borne(m.size, 10, 100, MASQUE_DEFAUT.size),
      height: borne(m.height, 10, 100, MASQUE_DEFAUT.height),
      x: borne(m.x, 0, 100, MASQUE_DEFAUT.x),
      y: borne(m.y, 0, 100, MASQUE_DEFAUT.y)
    } };
}

// Applique la découpe circulaire ou ovale à la scène 3D. Le masque est calculé
// en pixels à partir des pourcentages de la fenêtre : un cercle reste un vrai
// cercle même si la fenêtre avatar n'a pas la même largeur que hauteur.
function appliquerMasque() {
  const conteneurScene = document.getElementById('scene');
  if (!conteneurScene) return;
  const m = configAvatar.mask || MASQUE_DEFAUT;
  if (!m || m.shape === 'none') {
    conteneurScene.style.clipPath = 'none';
    conteneurScene.style.webkitClipPath = 'none';
    return;
  }
  const largeur = Math.max(1, window.innerWidth);
  const hauteur = Math.max(1, window.innerHeight);
  const cx = largeur * (m.x / 100);
  const cy = hauteur * (m.y / 100);
  let decoupe;
  if (m.shape === 'circle') {
    const rayon = Math.min(largeur, hauteur) * (m.size / 100) / 2;
    decoupe = `circle(${rayon.toFixed(2)}px at ${cx.toFixed(2)}px ${cy.toFixed(2)}px)`;
  } else {
    const rx = largeur * (m.size / 100) / 2;
    const ry = hauteur * (m.height / 100) / 2;
    decoupe = `ellipse(${rx.toFixed(2)}px ${ry.toFixed(2)}px at ${cx.toFixed(2)}px ${cy.toFixed(2)}px)`;
  }
  conteneurScene.style.clipPath = decoupe;
  conteneurScene.style.webkitClipPath = decoupe;
}

function appliquerConfig() {
  // Le masque ne dépend pas du modèle : il peut être appliqué pendant le
  // chargement, ou avant qu'un GLB/VRM soit disponible.
  appliquerMasque();
  if (!modeleActuel) return;
  const p = configAvatar.position;
  modeleActuel.scale.setScalar(configAvatar.scale);
  modeleActuel.position.set(p.x, p.y, p.z);
  modeleActuel.rotation.y = THREE.MathUtils.degToRad(configAvatar.rotationY);
  camera.position.set(configAvatar.camera.x, configAvatar.camera.y, configAvatar.camera.z);
  const cibleX = Number(configAvatar.camera.targetX);
  const cibleZ = Number(configAvatar.camera.targetZ);
  controles.target.set(
    Number.isFinite(cibleX) ? cibleX : 0,
    configAvatar.camera.targetY,
    Number.isFinite(cibleZ) ? cibleZ : 0,
  );
  controles.update();
  remplirChamps();
  // Les offsets de micro-vie repartent sur la pose fraîche (le cadrage auto
  // change le yaw : re-capturer évite un saut d'amplitude 0.02, invisible).
  try {
    if (noeudTeteVRM) rotBaseTete = noeudTeteVRM.rotation.clone();
    if (noeudPoitrineVRM) rotBasePoitrine = noeudPoitrineVRM.rotation.clone();
  } catch (_) { /* os non résolus */ }
}

// --- Cadrage visage automatique : gros plan face caméra au démarrage ---
// Le visage est localisé dans l'ordre : os de tête VRM (humanoid, le plus
// fiable), mesh porteur des morphs (GLB = tête seule), sinon haut du modèle.
function centreVisageMonde() {
  try {
    if (vrmActif && vrmActif.humanoid) {
      const tete = vrmActif.humanoid.getNormalizedBoneNode('head');
      if (tete) {
        const p = new THREE.Vector3();
        tete.getWorldPosition(p);
        return { centre: new THREE.Vector3(p.x, p.y + 0.06, p.z), hauteur: 0.28 };
      }
    }
  } catch (_) { /* repli géométrique ci-dessous */ }
  try {
    if (teteMesh) {
      const boite = new THREE.Box3().setFromObject(teteMesh);
      const c = boite.getCenter(new THREE.Vector3());
      const h = Math.max(0.1, boite.max.y - boite.min.y);
      return { centre: c, hauteur: Math.min(h, 0.4) };
    }
  } catch (_) { /* repli corps entier ci-dessous */ }
  const boite = new THREE.Box3().setFromObject(modeleActuel);
  const hTot = Math.max(0.1, boite.max.y - boite.min.y);
  const hVis = Math.max(0.15, Math.min(0.35, hTot * 0.22));
  const c = new THREE.Vector3(
    (boite.min.x + boite.max.x) / 2,
    boite.max.y - hVis / 2 - hTot * 0.02,
    (boite.min.z + boite.max.z) / 2
  );
  return { centre: c, hauteur: hVis };
}

function cadrerVisageFaceCamera(marge = 1.6) {
  if (!modeleActuel) return false;
  try {
    modeleActuel.updateMatrixWorld(true);
    const premier = centreVisageMonde();
    // Garde : géométrie vide/bizarre = centre non fini → on ne touche à rien
    // (une caméra NaN rendrait la scène folle).
    if (!premier || !premier.centre
        || !Number.isFinite(premier.centre.x + premier.centre.y + premier.centre.z)
        || !Number.isFinite(premier.hauteur) || premier.hauteur <= 0) {
      console.warn('[Sarra] Cadrage impossible (centre invalide).');
      return false;
    }
    // Face caméra : sarra/houda (VRM 0.x + rotateVRM0) regardent vers 180°,
    // les VRM 1.0 (forward +Z natif) et les GLB RPM vers 0°.
    const estVRM0 = !!(vrmActif && vrmActif.meta && vrmActif.meta.metaVersion === '0');
    const angleFace = vrmActif ? (estVRM0 ? 180 : 0) : 0;
    configAvatar.rotationY = angleFace;
    modeleActuel.rotation.y = THREE.MathUtils.degToRad(angleFace);
    modeleActuel.updateMatrixWorld(true);
    // Recalcule après redressement : le yaw déplace le visage en x/z.
    const recentre = centreVisageMonde().centre;
    const demi = Math.max(0.08, premier.hauteur / 2);
    const dist = (demi / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) * marge;
    const z = Math.max(0.35, Math.min(5, dist));
    configAvatar.camera = {
      x: recentre.x, y: recentre.y, z: recentre.z + z,
      targetX: recentre.x, targetY: recentre.y, targetZ: recentre.z
    };
    camera.position.set(recentre.x, recentre.y, recentre.z + z);
    controles.target.set(recentre.x, recentre.y, recentre.z);
    controles.update();
    remplirChamps();
    return true;
  } catch (e) {
    console.warn('[Sarra] Cadrage visage impossible :', e);
    return false;
  }
}

// Une config sauvegardée hors de ces bornes (ex. caméra à 22 m, rotation de
// dos) est ignorée : le cadrage visage automatique prend le relais.
function configSauvegardeeValide(cfg, estVRM) {
  if (!cfg || typeof cfg !== 'object') return false;
  const num = (v) => Number.isFinite(Number(v));
  if (!num(cfg.scale) || !num(cfg.rotationY)) return false;
  const p = cfg.position || {}, c = cfg.camera || {};
  if (!num(p.x) || !num(p.y) || !num(p.z)) return false;
  if (!num(c.x) || !num(c.y) || !num(c.z) || !Number.isFinite(Number(c.targetY))) return false;
  // Les anciennes configurations n'ont pas targetX/targetZ : 0 par défaut.
  if (c.targetX !== undefined && !num(c.targetX)) return false;
  if (c.targetZ !== undefined && !num(c.targetZ)) return false;
  if (estVRM) {
    if (cfg.scale < 0.5 || cfg.scale > 2.5) return false;
    if (Math.abs(p.x) > 2 || Math.abs(p.z) > 2 || p.y < -2 || p.y > 2) return false;
    if (c.z < 0.3 || c.z > 5) return false;
    if (c.targetY < 0.8 || c.targetY > 2.0 || c.y < 0.5 || c.y > 2.2) return false;
    const r = ((Number(cfg.rotationY) % 360) + 360) % 360;
    // Visage : ≈180° en VRM 0.x, ≈0° en VRM 1.0 — sinon on recadre.
    if (!((r >= 135 && r <= 225) || r <= 45 || r >= 315)) return false;
  } else {
    if (cfg.scale < 0.5 || cfg.scale > 20) return false;
    // Le repli GLB est chargé à l'échelle 6 ; un cadrage VRM (échelle ~1)
    // ne doit pas lui être appliqué — sinon on recadre automatiquement.
    if (cfg.scale < 3) return false;
    if (c.z < 0.2 || c.z > 15) return false;
  }
  return true;
}

function definirMeshVisage() {
  if (teteMesh || !meshesMorphs.length) return;
  // Le mesh facial est celui qui porte le plus grand jeu de blendshapes.
  teteMesh = meshesMorphs.sort((a, b) => (b.morphTargetInfluences?.length || 0) - (a.morphTargetInfluences?.length || 0))[0];
  dicoMorphs = teteMesh.morphTargetDictionary || {};
  morphsSansNoms = Object.keys(dicoMorphs).length === 0 || Object.keys(dicoMorphs).every((nom) => /^\d+$/.test(nom));
  for (let i = 0; i < teteMesh.morphTargetInfluences.length; i += 1) {
    const nom = morphsSansNoms ? `morph_${i}` : (Object.keys(dicoMorphs).find((key) => dicoMorphs[key] === i) || `morph_${i}`);
    dicoMorphs[nom] = i;
    cibles[nom] = 0; courants[nom] = 0;
  }
  console.log('[Sarra] Mesh lipsync:', teteMesh.name, teteMesh.morphTargetInfluences.length, Object.keys(dicoMorphs));
}

// Les VRM chargés via three-vrm arrivent avec un morphTargetDictionary
// purement numérique ("0".."65") : les noms (extras.targetNames) sont perdus
// en route, et le pilotage par indices anime alors les mauvais morphs
// (ex. les yeux au lieu de la bouche). On relit donc le JSON embarqué du
// .vrm/.glb pour restaurer le vrai mapping nom -> index.
let _nomsMorphsFichier = {}; // nodeName -> [noms...] du modèle en cours
async function chargerNomsMorphsFichier(url) {
  _nomsMorphsFichier = {};
  try {
    const reponse = await fetch(url, { cache: 'no-store' });
    if (!reponse.ok) return;
    const buf = await reponse.arrayBuffer();
    const dv = new DataView(buf);
    if (dv.getUint32(0, true) !== 0x46546C67) return; // pas un glTF binaire
    const len = dv.getUint32(12, true);
    const js = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 20, len)));
    const meshes = js.meshes || [];
    for (const node of js.nodes || []) {
      if (node.mesh === undefined || !node.name) continue;
      for (const prim of meshes[node.mesh]?.primitives || []) {
        const noms = prim.extras?.targetNames;
        if (noms?.length) { _nomsMorphsFichier[node.name] = noms; break; }
      }
    }
  } catch (e) { console.warn('[Sarra] Noms morphs illisibles :', e); }
}

function restaurerNomsMorphs() {
  // Ne touche que les dictionnaires numériques ; les GLB déjà nommés restent intacts.
  for (const mesh of meshesMorphs) {
    const dico = mesh.morphTargetDictionary || {};
    const cles = Object.keys(dico);
    if (!cles.length || !cles.every((k) => /^\d+$/.test(k))) continue;
    const base = String(mesh.name || '').replace(/_\d+$/, '');
    const noms = _nomsMorphsFichier[mesh.name] || _nomsMorphsFichier[base];
    if (noms && noms.length === mesh.morphTargetInfluences.length) {
      const neuf = {};
      noms.forEach((n, i) => { neuf[n] = i; });
      mesh.morphTargetDictionary = neuf;
    }
  }
}

// Remet tous les morphs à zéro (anti-horreur : certains modèles arrivent avec
// des influences non nulles, ou un chargement partiel laisse des résidus).
// Retourne le nombre de meshes neutralisés.
function neutraliserMorphs() {
  let n = 0;
  for (const mesh of meshesMorphs) {
    const inf = mesh?.morphTargetInfluences;
    if (!inf) continue;
    for (let i = 0; i < inf.length; i += 1) inf[i] = 0;
    n += 1;
  }
  for (const cle of Object.keys(cibles)) cibles[cle] = 0;
  for (const cle of Object.keys(courants)) courants[cle] = 0;
  for (const cle of Object.keys(ciblesIndex)) ciblesIndex[cle] = 0;
  for (const cle of Object.keys(courantsIndex)) courantsIndex[cle] = 0;
  return n;
}

// Les meshes secondaires (dents, cils...) portent souvent les mêmes morphs de
// bouche que le visage (ex. houda : AvatarTeethLower). On les anime avec les
// mêmes valeurs lissées, sans les compter comme visage.
function indexerMorphsSecondaires() {
  dicosParMesh = [];
  for (const mesh of meshesMorphs) {
    if (!mesh || mesh === teteMesh) continue;
    if (!mesh.morphTargetInfluences?.length) continue;
    const dico = mesh.morphTargetDictionary || {};
    const noms = Object.keys(dico);
    if (!noms.length || noms.every((n) => /^\d+$/.test(n))) continue;
    dicosParMesh.push({ mesh, dico });
  }
}

// Fallback pour les modèles qui exportent les blendshapes sans leurs noms.
// Les indices restent une meilleure solution que de désactiver complètement la bouche.
const INDICES_SECOURS = { 1: [0, 1, 2], 2: [3, 4], 4: [5, 6], 5: [7, 8], 7: [9, 10], 9: [11, 12], 10: [13, 14], 11: [15, 16], 12: [17, 18], 13: [19, 20], 14: [21, 22] };

function remplirChamps() {
  const valeurs = {
    'avatar-scale': configAvatar.scale, 'avatar-x': configAvatar.position.x,
    'avatar-y': configAvatar.position.y, 'avatar-z': configAvatar.position.z,
    'avatar-ry': configAvatar.rotationY, 'camera-x': configAvatar.camera.x,
    'camera-y': configAvatar.camera.y, 'camera-z': configAvatar.camera.z,
    'camera-target-y': configAvatar.camera.targetY,
    'mask-size': (configAvatar.mask || MASQUE_DEFAUT).size,
    'mask-height': (configAvatar.mask || MASQUE_DEFAUT).height,
    'mask-x': (configAvatar.mask || MASQUE_DEFAUT).x,
    'mask-y': (configAvatar.mask || MASQUE_DEFAUT).y
  };
  for (const [id, value] of Object.entries(valeurs)) {
    const el = document.getElementById(id); if (el) el.value = value;
  }
  const forme = document.getElementById('mask-shape');
  if (forme) forme.value = (configAvatar.mask || MASQUE_DEFAUT).shape || 'none';
  majChampsMasque();
}

// Grise les réglages sans effet pour la forme choisie : la hauteur ne sert
// qu'à l'ovale, et taille/position ne servent à rien sans masque.
function majChampsMasque() {
  const forme = document.getElementById('mask-shape')?.value || 'none';
  const actif = forme !== 'none';
  const hauteur = document.getElementById('mask-height');
  if (hauteur) {
    hauteur.disabled = forme !== 'ellipse';
    hauteur.style.opacity = forme === 'ellipse' ? '1' : '0.45';
  }
  for (const id of ['mask-size', 'mask-x', 'mask-y']) {
    const el = document.getElementById(id);
    if (el) el.disabled = !actif;
  }
  for (const id of ['mask-size', 'mask-x', 'mask-y']) {
    const el = document.getElementById(id);
    if (el) el.style.opacity = actif ? '1' : '0.45';
  }
}

function lireChamps() {
  const n = (id, fallback) => { const v = Number.parseFloat(document.getElementById(id)?.value); return Number.isFinite(v) ? v : fallback; };
  const forme = document.getElementById('mask-shape')?.value || 'none';
  const borne = (valeur, min, max) => Math.min(max, Math.max(min, valeur));
  configAvatar = { scale: Math.max(0.1, n('avatar-scale', 6)),
    position: { x: n('avatar-x', 0), y: n('avatar-y', -0.8), z: n('avatar-z', 0) },
    rotationY: n('avatar-ry', 0), camera: { x: n('camera-x', 0), y: n('camera-y', 0.5),
      z: Math.max(0.2, n('camera-z', 2.5)),
      targetX: Number.isFinite(Number(configAvatar.camera.targetX)) ? Number(configAvatar.camera.targetX) : 0,
      targetY: n('camera-target-y', 1.55),
      targetZ: Number.isFinite(Number(configAvatar.camera.targetZ)) ? Number(configAvatar.camera.targetZ) : 0 },
    mask: {
      shape: ['none', 'circle', 'ellipse'].includes(forme) ? forme : 'none',
      size: borne(n('mask-size', 100), 10, 100),
      height: borne(n('mask-height', 100), 10, 100),
      x: borne(n('mask-x', 50), 0, 100),
      y: borne(n('mask-y', 50), 0, 100)
    } };
  appliquerConfig();
  planifierSauvegardeConfig();
}

// Après une orbite ou un zoom (Ctrl+gauche / molette), la caméra est la
// source de vérité : on la recopie dans la configuration avant de sauvegarder.
function synchroniserCameraDepuisControles() {
  if (!modeleActuel) return;
  configAvatar.camera = {
    x: camera.position.x, y: camera.position.y, z: camera.position.z,
    targetX: controles.target.x, targetY: controles.target.y,
    targetZ: controles.target.z
  };
  remplirChamps();
}

let minuterieSauvegarde = null;

async function sauvegarderConfigMaintenant() {
  if (minuterieSauvegarde) {
    clearTimeout(minuterieSauvegarde);
    minuterieSauvegarde = null;
  }
  const r = await fetch('./avatar_camera.json', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(configAvatar)
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

// Sauvegarde automatique et débouncée : chaque réglage (taille, position,
// caméra, masque) est conservé sans devoir cliquer sur SAUVEGARDER.
function planifierSauvegardeConfig(delai = 600) {
  if (minuterieSauvegarde) clearTimeout(minuterieSauvegarde);
  minuterieSauvegarde = setTimeout(async () => {
    minuterieSauvegarde = null;
    synchroniserCameraDepuisControles();
    try {
      await sauvegarderConfigMaintenant();
    } catch (e) {
      console.warn('[Sarra] sauvegarde auto:', e);
    }
  }, delai);
}

function messageConfig(text, erreur = false) {
  const el = document.getElementById('avatar-config-status');
  if (el) { el.textContent = text; el.style.color = erreur ? '#ff7890' : '#72ffb0'; }
}

async function chargerConfig() {
  try { const r = await fetch('./avatar_camera.json', { cache: 'no-store' });
    if (r.ok) {
      const brute = await r.json();
      if (configSauvegardeeValide(brute, !!vrmActif)) {
        configAvatar = normaliserConfig(brute);
        remplirChamps(); appliquerConfig();
        return true;
      }
      console.warn('[Sarra] Config sauvegardée ignorée (hors bornes visage), cadrage auto.');
    }
  } catch (_) { /* première installation : valeurs par défaut */ }
  remplirChamps(); appliquerConfig();
  return false;
}

// Démarrage : on restaure le cadrage sauvegardé (taille, position, caméra).
// Le recadrage automatique ne sert que de secours si aucun réglage valide
// n'existe — sinon un zoom ou un placement choisi serait perdu à chaque lancement.
async function demarrageAvecCadrageVisage() {
  const configChargee = await chargerConfig();
  if (!configChargee) cadrerVisageFaceCamera();
}

document.querySelectorAll('#avatar-tools input').forEach((el) => el.addEventListener('input', lireChamps));
document.getElementById('mask-shape')?.addEventListener('change', () => {
  majChampsMasque();
  lireChamps();
});
document.getElementById('avatar-save')?.addEventListener('click', async () => {
  // Les champs sont déjà appliqués au fil de la saisie ; on relit seulement la
  // caméra pour ne pas annuler une orbite/zoom faite à la souris.
  synchroniserCameraDepuisControles();
  try {
    await sauvegarderConfigMaintenant();
    messageConfig('Configuration sauvegardée.');
  } catch (e) {
    messageConfig('Sauvegarde impossible.', true);
    console.error('[Sarra] sauvegarde:', e);
  }
});
document.getElementById('avatar-reset')?.addEventListener('click', async () => {
  // Le bouton réinitialise aussi le fichier persistant : au prochain
  // démarrage, ces valeurs resteront les nouvelles valeurs par défaut.
  configAvatar = structuredClone(vrmActif ? CONFIG_VRM_DEFAUT : CONFIG_DEFAUT);
  appliquerConfig();
  neutraliserMorphs();
  try {
    await sauvegarderConfigMaintenant();
    messageConfig('Valeurs par défaut restaurées et sauvegardées.');
  } catch (e) {
    messageConfig('Réinitialisation locale uniquement.', true);
    console.error('[Sarra] sauvegarde des valeurs par défaut:', e);
  }
});
document.getElementById('avatar-auto-frame')?.addEventListener('click', () => {
  if (!modeleActuel) return;
  if (cadrerVisageFaceCamera()) {
    messageConfig('Visage cadré face caméra.');
    planifierSauvegardeConfig(0);
  }
});
// Orbite/zoom à la souris (Ctrl+gauche, molette) : mémorise la taille et le
// placement exacts de l'avatar pour le prochain démarrage.
controles.addEventListener('end', () => planifierSauvegardeConfig(450));

// --- Bouton micro HTML, au-dessus du canvas Three.js --------------------
// Un QPushButton natif est peint sous le QWebEngineView ; ce bouton HTML,
// lui, reste visible au-dessus de l'avatar et du masque CSS.
let pontAvatar = null;
let pontAvatarEssais = 0;

function appliquerEtatMicro(coupe) {
  const btn = document.getElementById('avatar-mic-toggle');
  if (!btn) return;
  const muted = !!coupe;
  btn.textContent = muted ? '🔇' : '🎙';
  btn.title = muted
    ? 'Micro coupé — cliquer pour activer'
    : 'Micro actif — cliquer pour couper';
  btn.setAttribute('aria-pressed', muted ? 'true' : 'false');
  btn.classList.toggle('muted', muted);
}
window.setAvatarMicMuted = appliquerEtatMicro;

function initPontAvatar() {
  if (typeof QWebChannel !== 'undefined' && window.qt && qt.webChannelTransport) {
    new QWebChannel(qt.webChannelTransport, (canal) => {
      pontAvatar = canal.objects.avatarBridge || null;
      console.log('[Sarra] pont micro prêt');
    });
    return;
  }
  // app.js peut s'exécuter avant que Qt ait posé le transport.
  if (pontAvatarEssais++ < 100) setTimeout(initPontAvatar, 50);
  else console.warn('[Sarra] pont micro indisponible (QWebChannel).');
}
initPontAvatar();

document.getElementById('avatar-mic-toggle')?.addEventListener('click', () => {
  const btn = document.getElementById('avatar-mic-toggle');
  if (pontAvatar && typeof pontAvatar.toggleMic === 'function') {
    const etaitCoupe = btn?.classList.contains('muted');
    appliquerEtatMicro(!etaitCoupe); // retour visuel immédiat
    pontAvatar.toggleMic();
  } else {
    console.warn('[Sarra] clic micro ignoré : pont QWebChannel indisponible.');
  }
});

// --- Préréglage de masque « tête seule » --------------------------------
// Respecte la forme choisie (Cercle ou Ovale). Les champs restent ajustables
// et la sauvegarde n'a lieu qu'avec SAUVEGARDER.
const MASQUE_TETE = { size: 62, height: 80, x: 50, y: 42 };
// Un cercle doit être plus large que l'ovale pour ne pas couper le haut du
// crâne ; il inclut forcément un peu plus d'épaules. L'ovale reste le réglage
// le plus précis pour ne garder que la tête.
const MASQUE_TETE_CERCLE = { size: 78, height: 78, x: 50, y: 43 };
document.getElementById('mask-head')?.addEventListener('click', () => {
  const forme = document.getElementById('mask-shape')?.value === 'circle'
    ? 'circle' : 'ellipse';
  const preset = forme === 'circle' ? MASQUE_TETE_CERCLE : MASQUE_TETE;
  configAvatar.mask = { shape: forme, ...preset };
  appliquerConfig();
  planifierSauvegardeConfig();
  messageConfig(forme === 'circle'
    ? 'Masque cercle tête appliqué et sauvegardé.'
    : 'Masque ovale tête appliqué et sauvegardé.');
});

// --- Éclairage photoréaliste : IBL (HDR) + 3 points ---
scene.add(new THREE.HemisphereLight(0xbfd9ff, 0x30281e, 0.45));
const clef = new THREE.DirectionalLight(0xffffff, 2.0); // Clé : porte les ombres douces.
clef.position.set(2.0, 3.0, 2.5);
clef.castShadow = true;
clef.shadow.mapSize.set(1024, 1024);
scene.add(clef);
const remplissage = new THREE.DirectionalLight(0x88aaff, 0.55); // Débouche les ombres.
remplissage.position.set(-2.0, 1.5, 1.5);
scene.add(remplissage);
const contre = new THREE.DirectionalLight(0xfff0e0, 0.8); // Rim chaud : détache du fond.
contre.position.set(0, 2.2, -2.5);
scene.add(contre);

// Environnement IBL : le HDR téléchargé par setup_assets.py, repli procédural sinon.
const pmrem = new THREE.PMREMGenerator(rendu);
new RGBELoader().load('./royal_esplanade_1k.hdr',
  (texture) => {
    scene.environment = pmrem.fromEquirectangular(texture).texture;
    texture.dispose();
  },
  undefined,
  () => {
    // Pas de HDR (setup non lancé) : pièce virtuelle, jamais d'écran noir.
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  }
);

// Sol : attrape l'ombre portée, reste invisible.
const sol = new THREE.Mesh(
  new THREE.CircleGeometry(3, 32),
  new THREE.ShadowMaterial({ opacity: 0.25 })
);
sol.rotation.x = -Math.PI / 2;
sol.receiveShadow = true;
scene.add(sol);

// --- Chargement forcé du modèle GLB de test ---
const chargeurGLB = new GLTFLoader();
// Le plugin VRM ne s'enregistre qu'une fois la lib chargée (sinon throw + mort du module).
function retirerModeleActuel() {
  if (modeleActuel) scene.remove(modeleActuel);
  modeleActuel = null;
  vrmActif = null;
  noeudTeteVRM = null;
  noeudPoitrineVRM = null;
  rotBaseTete = null;
  rotBasePoitrine = null;
  teteMesh = null;
  meshesMorphs = [];
  morphsSansNoms = false;
  teteOs = null;
  couOs = null;
  dicoMorphs = {};
  for (const cle of Object.keys(cibles)) delete cibles[cle];
  for (const cle of Object.keys(courants)) delete courants[cle];
  for (const cle of Object.keys(ciblesIndex)) delete ciblesIndex[cle];
  for (const cle of Object.keys(courantsIndex)) delete courantsIndex[cle];
  dicosParMesh = [];
}

async function chargerVRM(url = './sarra7.vrm') {
  statutSarra('Chargement three-vrm…');
  if (!(await chargerLibVRM())) {
    statutSarra('three-vrm indisponible (CDN bloqué ?) : repli GLB.');
    chargerGLB();
    return;
  }
  try {
    if (!pluginVRMEnregistre) {
      chargeurGLB.register((parser) => new VRMLoaderPlugin(parser));
      pluginVRMEnregistre = true;
    }
  } catch (e) {
    statutSarra('Plugin VRM refusé : repli GLB.');
    chargerGLB();
    return;
  }
  statutSarra('Chargement ' + decodeURIComponent(url.replace('./', '')) + '…');
  chargeurGLB.load(url,
    (gltf) => { activerVRM(gltf, url); },
    undefined,
    (e) => {
      statutSarra('sarra.vrm illisible (' + (((e && e.message) || e) || 'voir console F12') + ') : repli GLB.');
      chargerGLB('./sarra.glb');
    }
  );
}

function chargerGLB(url = './sarra.glb') {
chargeurGLB.load(url,
  (gltf) => {
    retirerModeleActuel();
    const modele = gltf.scene;
    modele.traverse((obj) => {
      if (obj.isMesh) {
        obj.castShadow = true;
        // Matériaux PBR : s'assure que les textures RPM restent en espace couleur correct.
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        for (const m of mats) {
          if (m && m.isMeshStandardMaterial) {
            m.envMapIntensity = 0.9; // Les reflets IBL font le "photoréaliste".
            if (m.map) m.map.colorSpace = THREE.SRGBColorSpace;
          }
        }
        // Repère le visage = premier mesh avec des morphs de visèmes.
        if (obj.morphTargetInfluences?.length) meshesMorphs.push(obj);
        if (!teteMesh && obj.morphTargetDictionary) {
          const noms = Object.keys(obj.morphTargetDictionary);
          // DEBUG : Afficher les vrais noms des morphs
          console.log("=== MORPH TARGETS DISPONIBLES ===", noms);
          const debugDiv = document.getElementById('debug-sarra');
          if (debugDiv) {
            debugDiv.innerText = "Morphs: " + noms.join(', ');
            debugDiv.style.fontSize = "10px"; // Réduire la police pour tout voir
          }

          if (noms.some((n) => n.toLowerCase().includes('mouth') || n.toLowerCase().includes('jaw') || n.toLowerCase().includes('viseme'))) {
            teteMesh = obj;
            dicoMorphs = obj.morphTargetDictionary;
            for (const n of noms) { cibles[n] = 0; courants[n] = 0; }
          }
        }
      }
      // Repère les os pour respiration + regard.
      if (obj.isBone) {
        if (obj.name === 'Head') teteOs = obj;
        if (obj.name === 'Neck') couOs = obj;
      }
    });
    // --- MODIFICATION : Agrandir le modèle CuteGirl ---
    const echelle = 6.0; // Ajustez cette valeur (essayez 5.0, 6.0 ou 8.0)
    modele.scale.set(echelle, echelle, echelle);
    modele.position.set(0, -0.8, 0); // Descendre légèrement pour centrer le visage
    modeleActuel = modele;
    scene.add(modele);
    appliquerConfig();
    // Les noms des morphs GLB sont lus dans le fichier (les dictionnaires
    // numériques sont restaurés avant d'indexer le visage).
    chargerNomsMorphsFichier(url).then(() => {
      restaurerNomsMorphs();
      definirMeshVisage();
      indexerMorphsSecondaires();
      neutraliserMorphs();
      demarrageAvecCadrageVisage();
    });
    document.getElementById('chargement').style.display = 'none';
    window.sarraReady = true;
  },
  undefined,
  (err) => {
    document.getElementById('chargement').textContent = 'Fichier avatar illisible : vérifiez sa présence.';
    statutSarra('Ni VRM ni GLB affichables — console (F12) pour le détail.');
    console.error('[Sarra] Échec chargement de ./sarra.glb :', err);
  }
);
} // fin chargerGLB (repli GLB)

// --- Activation d'un modèle VRM : expressions + regard + spring bones ---
async function activerVRM(gltf, url) {
  const vrm = gltf.userData && gltf.userData.vrm;
  if (!vrm) { statutSarra('Fichier chargé mais pas un VRM valide.'); return; }
  retirerModeleActuel();
  try {
    // Les VRM 0.x regardent vers -Z : on les retourne face caméra.
    if (vrm.meta && vrm.meta.metaVersion === '0') VRMUtils.rotateVRM0(vrm);
  } catch (e) { console.warn('[Sarra] rotateVRM0 ignoré :', e); }
  vrm.scene.traverse((obj) => { if (obj.isMesh) obj.castShadow = true; });
  // Visèmes directs : certains VRM (ex. houda, binds vides) ne répondent pas à
  // l'expressionManager — on indexe leurs morphs comme en repli GLB.
  vrm.scene.traverse((obj) => {
    if (obj.isMesh && obj.morphTargetInfluences?.length) meshesMorphs.push(obj);
  });
  // Restaure les vrais noms des morphs (dictionnaire numérique three-vrm),
  // sans quoi le pilotage par indices anime les mauvais morphs (ex. les yeux).
  if (url) { try { await chargerNomsMorphsFichier(url); } catch (_) { /* noms seuls */ } }
  restaurerNomsMorphs();
  definirMeshVisage();
  indexerMorphsSecondaires();
  const nbNeutralises = neutraliserMorphs();
  console.log('[Sarra] VRM morphs :', meshesMorphs.length, 'meshes,',
    (teteMesh?.morphTargetInfluences?.length || 0), 'sur visage,',
    nbNeutralises, 'neutralisés, dico nommé :', !morphsSansNoms);
  // Cadrage métrique (VRM en mètres) : la config d'un GLB Sketchfab ne s'applique jamais ici.
  configAvatar = structuredClone(CONFIG_VRM_DEFAUT);
  modeleActuel = vrm.scene;
  vrmActif = vrm;
  // Micro-vie : résolution des os + la cible regard pilote le lookAt.
  try {
    const hum = vrm.humanoid || null;
    noeudTeteVRM = hum ? hum.getNormalizedBoneNode('head') : null;
    noeudPoitrineVRM = hum
      ? (hum.getNormalizedBoneNode('chest') || hum.getNormalizedBoneNode('spine'))
      : null;
    if (vrm.lookAt) vrm.lookAt.target = cibleRegard;
  } catch (_) { noeudTeteVRM = null; noeudPoitrineVRM = null; }
  scene.add(vrm.scene);
  appliquerConfig();
  // Le VRM relit la configuration persistante après son activation. Le
  // recadrage automatique ne s'applique que si elle est absente ou invalide.
  demarrageAvecCadrageVisage();
  document.getElementById('chargement').style.display = 'none';
  window.sarraReady = true;
  let pres = [];
  try {
    pres = Object.keys((vrm.expressionManager && vrm.expressionManager.presetExpressionMap) || {});
    console.log('[Sarra] VRM actif :', pres);
  } catch (_) { /* diagnostic seul */ }
  statutSarra('VRM affiché (' + pres.length + ' presets : ' + pres.slice(0, 8).join(',') + '…) | visage: '
    + (teteMesh ? teteMesh.name : '?') + ' (' + (teteMesh?.morphTargetInfluences?.length || 0) + ' morphs'
    + (morphsSansNoms ? ', SANS NOMS' : ', nommés') + ')');
}

function chargerAvatarDepuisListe(nom) {
  // Noms plats uniquement : aucun chemin (blocage traversal, même encodé).
  if (!/^[^\\/]+\.(vrm|glb)$/i.test(String(nom || ''))) {
    console.warn('[Sarra] Nom de modèle refusé :', nom);
    return;
  }
  const extension = String(nom).toLowerCase().split('.').pop();
  const url = './' + encodeURIComponent(nom);
  document.getElementById('chargement').style.display = 'flex';
  if (extension === 'vrm') chargerVRM(url);
  else if (extension === 'glb') chargerGLB(url);
}

async function initialiserSelectionAvatar() {
  const select = document.getElementById('avatar-model');
  const toggle = document.getElementById('avatar-select-toggle');
  if (!select || !toggle) return;
  toggle.addEventListener('click', () => {
    document.getElementById('avatar-tools')?.classList.toggle('open');
    select.focus();
  });
  select.addEventListener('change', () => {
    if (select.value) chargerAvatarDepuisListe(select.value);
  });
  try {
    const reponse = await fetch('./avatar_models.json');
    const modeles = await reponse.json();
    select.replaceChildren(...modeles.map((nom) => new Option(nom, nom)));
    select.value = modeles.includes('sarra7.vrm') ? 'sarra7.vrm'
      : (modeles.includes('houda2.vrm') ? 'houda2.vrm'
      : (modeles.includes('houda.vrm') ? 'houda.vrm'
      : (modeles.includes('sarra.vrm') ? 'sarra.vrm' : (modeles[0] || ''))));
  } catch (erreur) {
    select.innerHTML = '<option>Modèles indisponibles</option>';
    statutSarra('Impossible de lire le dossier avatar.');
    console.error('[Sarra] Liste des avatars indisponible :', erreur);
  }
}

// --- API appelée depuis Python (AvatarBridge.runJavaScript, asynchrone) ---
window.cadrerVisage = function () {
  return cadrerVisageFaceCamera();
};

window.neutraliserMorphs = function () {
  return neutraliserMorphs();
};

window.setAvatarOnlyMode = function (enabled) {
  document.body.classList.toggle('avatar-only', Boolean(enabled));
  return true;
};

window.updateViseme = function (visemeId, intensite) {
  const debugSarra = document.getElementById('debug-sarra');
  if (debugSarra) debugSarra.textContent = 'Reçu: ID=' + visemeId + ', Int=' + intensite;
  const id = Math.max(0, Math.min(14, parseInt(visemeId, 10) || 0));
  const k = Math.min(1, Math.max(0, Math.min(1, parseFloat(intensite) || 0)) * GAIN_ARTICULATION);
  suivreProsodie(k);
  // Coarticulation : les anciennes cibles décroissent au lieu d'être coupées —
  // la bouche fond d'une forme vers l'autre comme une vraie bouche, et le
  // silence relâche au lieu de claquer (les zéros de fin font le reste).
  for (const nom of Object.keys(cibles)) cibles[nom] *= 0.30;
  for (const [nom, poids] of (TABLE_VISEMES[id] || [])) {
    // Morph absent de ce modèle : ignoré (compat entre avatars RPM).
    if (nom in cibles) cibles[nom] = Math.min(1, k * poids);
  }
  // Noms nus Oculus / ARKit (ex. houda.vrm) : même garde, mêmes cibles.
  for (const [nom, poids] of (TABLE_HOUDA_14[id] || [])) {
    if (nom in cibles) cibles[nom] = Math.min(1, k * poids);
  }
  // Calibration : la mâchoire suit le gain réglé page LIPSYNC.
  if ('jawOpen' in cibles) {
    cibles['jawOpen'] = Math.min(1, (cibles['jawOpen'] || 0) * GAIN_MACHOIRE);
  }
  appliquerForceFermetures();
  appliquerAmplitude();
  appliquerTrims(id);
  // GLB sans noms de morphs (ex. Sketchfab multi-parties : 2 morphs anonymes
  // par mesh, pas de viseme_*/jawOpen). Pilotage par indices, pas par noms.
  if (teteMesh && morphsSansNoms) {
    const n = teteMesh.morphTargetInfluences.length;
    if (n <= 3) {
      // Pas de visèmes dans ce modèle : la bouche suit le niveau sonore
      // (mouvement simple au lieu d'une bouche figée). Si les sliders de
      // test_morphs.html ne montrent aucune déformation de la bouche, ce
      // modèle ne peut pas faire de lipsync : il faudra le remplacer.
      if (n > 0) ciblesIndex[0] = Math.min(1, k);
      if (n > 1) ciblesIndex[1] = Math.min(1, k * 0.5);
    } else {
      for (const idx of (INDICES_SECOURS[id] || [])) {
        if (idx < n) ciblesIndex[idx] = Math.min(1, k);
      }
    }
  }
  // VRM : mêmes ID 0..14, table réduite aux 5 presets bouche (+ blink procédural).
  if (vrmActif) {
    for (const cle of Object.keys(CIBLES_VRM)) { if (cle !== 'blink') CIBLES_VRM[cle] = 0; }
    for (const [preset, poids] of (TABLE_VRM_14[id] || [])) {
      // setValue ignore les presets absents : compatible tous modèles VRM.
      CIBLES_VRM[preset] = Math.min(1, k * poids);
    }
  }
  return true;
};

// --- Formes nommées du transcript (MBP, FV, S…) + mâchoire continue ---
// Reçoit le nom exact au lieu d'un ID requantifié : les consonnes mimées sont
// les bonnes (/s/ = dents serrées, pas un E). Par-dessus, jawOpen suit
// l'ouverture audio en continu : la mâchoire vit même entre deux formes.
const TABLE_NOMS_RPM = {
  REST: [], AA: [['viseme_aa', 1.0], ['jawOpen', 0.55]],
  E: [['viseme_E', 1.0], ['jawOpen', 0.15]], I: [['viseme_I', 1.0], ['jawOpen', 0.12]],
  O: [['viseme_O', 1.0], ['jawOpen', 0.35]], U: [['viseme_U', 1.0]],
  MBP: [['viseme_PP', 1.0]], FV: [['viseme_FF', 1.0], ['jawOpen', 0.10]],
  S: [['viseme_SS', 1.0]], L: [['viseme_DD', 0.8]], TD: [['viseme_DD', 1.0]],
  K: [['viseme_kk', 1.0]], R: [['viseme_RR', 1.0]],
};
const TABLE_NOMS_HOUDA = {
  REST: [], AA: [['aa', 1.0], ['jawOpen', 0.55]],
  E: [['E', 1.0], ['jawOpen', 0.15]], I: [['ih', 1.0], ['jawOpen', 0.12]],
  O: [['oh', 1.0], ['jawOpen', 0.35]], U: [['ou', 1.0], ['mouthFunnel', 0.4]],
  MBP: [['PP', 1.0], ['mouthClose', 0.8]], FV: [['FF', 1.0], ['jawOpen', 0.10]],
  S: [['SS', 1.0]], L: [['DD', 0.8]], TD: [['DD', 1.0]],
  K: [['kk', 1.0]], R: [['RR', 1.0]],
};
const TABLE_NOMS_VRM = {
  REST: [], AA: [['aa', 1.0]], E: [['ee', 1.0]], I: [['ih', 1.0]],
  O: [['oh', 1.0]], U: [['ou', 1.0]], MBP: [], FV: [['ih', 0.5]],
  S: [['ih', 0.6]], L: [['aa', 0.35]], TD: [['aa', 0.35]],
  K: [['aa', 0.45]], R: [['ou', 0.5]],
};

// --- Calibration labiale : gains réglés depuis la page LIPSYNC ---
// GAIN_MACHOIRE amplifie jawOpen, GAIN_ARTICULATION l'intensité des formes,
// FLUIDITE règle la vitesse des transitions, FORCE_FERMETURES appuie les
// closures (PP, FF, SS, mouthClose). 1.0 = neutre partout, chargés depuis
// ./avatar_lipsync.json au démarrage.
let GAIN_MACHOIRE = 1.0;
let GAIN_ARTICULATION = 1.0;
let FLUIDITE = 1.0;
let FORCE_FERMETURES = 1.0;
// AMPLITUDE : taille globale des mouvements de bouche (0.2..2.0).
let AMPLITUDE = 1.0;
// Morphs de parole (pas les émotions/sourires) : mis à l'échelle amplitude.
const MORPHS_BOUCHE = ['jawOpen', 'PP', 'FF', 'TH', 'DD', 'kk', 'CH', 'SS',
  'nn', 'RR', 'aa', 'E', 'ih', 'oh', 'ou', 'sil', 'mouthClose', 'mouthFunnel',
  'mouthPucker', 'mouthStretchLeft', 'mouthStretchRight', 'mouthRollLower',
  'mouthRollUpper', 'mouthShrugLower', 'mouthShrugUpper', 'mouthLowerDownLeft',
  'mouthLowerDownRight', 'mouthUpperUpLeft', 'mouthUpperUpRight',
  'viseme_PP', 'viseme_FF', 'viseme_TH', 'viseme_DD', 'viseme_kk',
  'viseme_CH', 'viseme_SS', 'viseme_nn', 'viseme_RR', 'viseme_aa',
  'viseme_E', 'viseme_I', 'viseme_O', 'viseme_U'];

function appliquerAmplitude() {
  for (const m of MORPHS_BOUCHE) {
    if (m in cibles && cibles[m] > 0) {
      cibles[m] = Math.min(1, cibles[m] * AMPLITUDE);
    }
  }
}

// --- Trims par visème (page LIPSYNC) : 4 axes de contrôle fin ---
// open = ouverture (jawOpen), close = fermeture (PP/mouthClose),
// stretch = étirement (spread), round = arrondi (funnel/purse + ou/oh).
// 1.0 = neutre ; 0 = axe coupé. Stockage creux : seuls les ≠1 persistent.
const TRIM_VISEMES = {};
const TRIM_COMPS = {
  open: ['jawOpen'],
  close: ['mouthClose', 'PP', 'viseme_PP'],
  stretch: ['mouthStretchLeft', 'mouthStretchRight', 'ih', 'E',
    'viseme_I', 'viseme_E'],
  round: ['mouthFunnel', 'mouthPucker', 'ou', 'oh', 'viseme_O', 'viseme_U'],
};
const VISEME_ID_PAR_NOM = {
  REST: 0, MBP: 1, FV: 2, TD: 4, K: 5, S: 7, L: 4, R: 9,
  AA: 10, E: 11, I: 12, O: 13, U: 14,
};

function bornerTrim(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? Math.max(-0.5, Math.min(2.0, n)) : 1.0;
}

window.setTrimViseme = function (vid, comp, val) {
  const id = Math.max(0, Math.min(14, parseInt(vid, 10) || 0));
  const c = String(comp || '').toLowerCase();
  if (!(c in TRIM_COMPS)) return false;
  const v = bornerTrim(val);
  if (!TRIM_VISEMES[id]) TRIM_VISEMES[id] = {};
  if (v === 1.0) delete TRIM_VISEMES[id][c];
  else TRIM_VISEMES[id][c] = v;
  if (TRIM_VISEMES[id] && Object.keys(TRIM_VISEMES[id]).length === 0) {
    delete TRIM_VISEMES[id];
  }
  return true;
};

window.resetTrims = function () {
  for (const k of Object.keys(TRIM_VISEMES)) delete TRIM_VISEMES[k];
  return true;
};

function appliquerTrims(id) {
  const t = TRIM_VISEMES[id];
  if (!t) return;
  for (const [comp, morphs] of Object.entries(TRIM_COMPS)) {
    const f = t[comp];
    if (f === undefined || f === 1.0) continue;
    for (const m of morphs) {
      if (m in cibles && cibles[m] > 0) {
        cibles[m] = Math.min(1, cibles[m] * f);
      }
    }
  }
}
// Morphs de fermeture : multipliés par FORCE_FERMETURES quand ils sont actifs
// (×0 reste 0 : jamais de fermeture fantôme).
const MORPHS_FERMETURE = ['PP', 'mouthClose', 'FF', 'SS',
  'viseme_PP', 'viseme_FF', 'viseme_SS'];

function bornerGain(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? Math.max(0.2, Math.min(2.0, n)) : 1.0;
}

function bornerFluidite(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? Math.max(0.5, Math.min(1.5, n)) : 1.0;
}

function appliquerFluidite() {
  LERP_OUVRE = Math.max(0.05, Math.min(0.9, 0.50 * FLUIDITE));
  LERP_FERME = Math.max(0.05, Math.min(0.9, 0.22 * FLUIDITE));
}

function appliquerForceFermetures() {
  for (const m of MORPHS_FERMETURE) {
    if (m in cibles && cibles[m] > 0) {
      cibles[m] = Math.min(1, cibles[m] * FORCE_FERMETURES);
    }
  }
}

window.setJawGain = function (v) { GAIN_MACHOIRE = bornerGain(v); return GAIN_MACHOIRE; };
window.setArticulationGain = function (v) { GAIN_ARTICULATION = bornerGain(v); return GAIN_ARTICULATION; };
window.setFluidite = function (v) { FLUIDITE = bornerFluidite(v); appliquerFluidite(); return FLUIDITE; };
window.setForceFermetures = function (v) { FORCE_FERMETURES = bornerGain(v); return FORCE_FERMETURES; };
window.setAmplitude = function (v) { AMPLITUDE = bornerGain(v); return AMPLITUDE; };

// --- Calibration automatique : fond témoin + mesure bouche ---
// fondCalibration(true) peint un fond magenta uni (restaure l'alpha après) ;
// mesurerBouche() rend UNE image de façon synchrone puis lit les pixels
// (valide sans preserveDrawingBuffer) autour de la bouche projetée.
// Retourne {ok, cavite, largeur, visage} — fractions 0..1 dans la boîte.
window.fondCalibration = function (actif) {
  try {
    if (actif) {
      document.body.classList.add('calibration');
      scene.background = new THREE.Color(1, 0, 1);
      rendu.setClearColor(0xff00ff, 1);
    } else {
      document.body.classList.remove('calibration');
      scene.background = null;
      rendu.setClearColor(0x000000, 0);
    }
    return true;
  } catch (_) { return false; }
};

window.mesurerBouche = function () {
  try {
    if (!modeleActuel) return { ok: false };
    modeleActuel.updateMatrixWorld(true);
    const { centre, hauteur } = centreVisageMonde();
    // Bouche ≈ sous le centre du visage, côté caméra (+Z monde = caméra).
    const bouche = new THREE.Vector3(centre.x, centre.y - hauteur * 0.32, centre.z + 0.10);
    const proj = bouche.clone().project(camera);
    const px = Math.round((proj.x * 0.5 + 0.5) * window.innerWidth);
    const py = Math.round((-proj.y * 0.5 + 0.5) * window.innerHeight);
    const BW = 150, BH = 110;
    const x0 = Math.max(0, px - BW / 2), y0 = Math.max(0, py - BH / 2);
    let w = Math.min(BW, window.innerWidth - x0);
    let h = Math.min(BH, window.innerHeight - y0);
    // Écrans HiDPI : readPixels parle en pixels physiques, pas CSS.
    const ech = (rendu.getPixelRatio ? rendu.getPixelRatio() : 1) || 1;
    const dx0 = Math.round(x0 * ech), dy0 = Math.round(y0 * ech);
    w = Math.round(w * ech); h = Math.round(h * ech);
    const dw = rendu.domElement.width, dh = rendu.domElement.height;
    rendu.render(scene, camera);
    const gl = rendu.getContext();
    const pixels = new Uint8Array(w * h * 4);
    // readPixels compte depuis le BAS (WebGL) : y inversé (pixels physiques).
    gl.readPixels(dx0, dh - dy0 - h, w, h,
                  gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    let visage = 0, sombre = 0, dents = 0, minX = w, maxX = -1;
    for (let y = 0; y < h; y += 2) {
      for (let x = 0; x < w; x += 2) {
        const i = (y * w + x) * 4;
        const r = pixels[i], g = pixels[i + 1], b = pixels[i + 2];
        // Fond témoin magenta : tout le reste = visage potentiel.
        if (r > 200 && g < 80 && b > 200) continue;
        visage++;
        const lum = 0.299 * r + 0.587 * g + 0.114 * b;
        const sat = Math.max(r, g, b) - Math.min(r, g, b);
        // Cavité sombre OU dents (blanc peu saturé) : les deux signent
        // une bouche ouverte (les lèvres rouges existent aussi fermée).
        if (lum < 70) { sombre++; if (x < minX) minX = x; if (x > maxX) maxX = x; }
        else if (lum > 185 && sat < 50) { dents++; if (x < minX) minX = x; if (x > maxX) maxX = x; }
      }
    }
    const total = Math.max(1, Math.ceil(w / 2) * Math.ceil(h / 2));
    const res = {
      ok: visage / total > 0.15,
      cavite: visage ? sombre / Math.max(1, visage) : 0,
      dents: visage ? dents / Math.max(1, visage) : 0,
      ouverture: visage ? (sombre + dents) / Math.max(1, visage) : 0,
      largeur: maxX >= 0 ? (maxX - minX) / w : 0,
      visage: visage / total,
    };
    console.info('[Sarra] mesure bouche:', JSON.stringify(res));
    return res;
  } catch (e) {
    console.warn('[Sarra] mesure impossible :', e);
    return { ok: false };
  }
};

async function chargerGainsLipsync() {
  try {
    const r = await fetch('./avatar_lipsync.json', { cache: 'no-store' });
    if (r.ok) {
      const cfg = await r.json();
      if (cfg && typeof cfg === 'object') {
        if (cfg.jaw_gain !== undefined) GAIN_MACHOIRE = bornerGain(cfg.jaw_gain);
        if (cfg.articulation_gain !== undefined) GAIN_ARTICULATION = bornerGain(cfg.articulation_gain);
        if (cfg.fluidite !== undefined) FLUIDITE = bornerFluidite(cfg.fluidite);
        if (cfg.force_fermetures !== undefined) FORCE_FERMETURES = bornerGain(cfg.force_fermetures);
        if (cfg.amplitude !== undefined) AMPLITUDE = bornerGain(cfg.amplitude);
        if (cfg.visemes && typeof cfg.visemes === 'object') {
          for (const [idStr, trims] of Object.entries(cfg.visemes)) {
            const id = Math.max(0, Math.min(14, parseInt(idStr, 10) || 0));
            if (trims && typeof trims === 'object') {
              for (const [comp, val] of Object.entries(trims)) {
                window.setTrimViseme(id, comp, val);
              }
            }
          }
        }
        appliquerFluidite();
      }
    }
  } catch (_) { /* pas de calibration : gains neutres */ }
}

// Séquence d'aperçu : enchaîne des formes contrastées puis relâche.
window.jouerSequenceTest = function () {
  const seq = [[10, 1.0], [1, 0.9], [12, 0.9], [13, 0.9], [7, 0.9], [1, 1.0]];
  seq.forEach(([id, k], i) => {
    setTimeout(() => window.updateViseme(id, k), i * 380);
  });
  setTimeout(() => window.resetVisemes(), seq.length * 380 + 200);
  return true;
};

// --- Émotions : couche séparée des visèmes (additionnée à l'écriture) ---
// Les visèmes possèdent la bouche 50 fois/s ; une émotion qui écrirait dans
// `cibles` serait écrasée à la trame suivante. `emoCibles` s'ajoute au moment
// du rendu : sourire + parole cohabitent (morphs différents), et l'émotion
// survit aux raz visèmes. Noms ARKit (gardés : absent = ignoré).
const TABLE_EMOTIONS = {
  neutre:   [['mouthSmileLeft', 0.12], ['mouthSmileRight', 0.12]],
  ecoute:   [['mouthSmileLeft', 0.45], ['mouthSmileRight', 0.45],
             ['cheekSquintLeft', 0.15], ['cheekSquintRight', 0.15]],
  sourire:  [['mouthSmileLeft', 0.55], ['mouthSmileRight', 0.55],
             ['mouthUpperUpLeft', 0.6], ['mouthUpperUpRight', 0.6],
             ['mouthStretchLeft', 0.15], ['mouthStretchRight', 0.15],
             ['jawOpen', 0.12]],
  grand_sourire: [['mouthSmileLeft', 1.0], ['mouthSmileRight', 1.0],
             ['mouthStretchLeft', 0.5], ['mouthStretchRight', 0.5],
             ['mouthUpperUpLeft', 0.7], ['mouthUpperUpRight', 0.7],
             ['mouthDimpleLeft', 0.25], ['mouthDimpleRight', 0.25],
             ['cheekSquintLeft', 0.5], ['cheekSquintRight', 0.5],
             ['eyeSquintLeft', 0.25], ['eyeSquintRight', 0.25],
             ['jawOpen', 0.28]],
  joie:     [['mouthSmileLeft', 1.0], ['mouthSmileRight', 1.0],
             ['mouthStretchLeft', 0.35], ['mouthStretchRight', 0.35],
             ['mouthDimpleLeft', 0.2], ['mouthDimpleRight', 0.2],
             ['cheekSquintLeft', 0.45], ['cheekSquintRight', 0.45],
             ['eyeSquintLeft', 0.2], ['eyeSquintRight', 0.2]],
  triste:   [['mouthFrownLeft', 0.7], ['mouthFrownRight', 0.7],
             ['browInnerUp', 0.4]],
  colere:   [['browDownLeft', 0.8], ['browDownRight', 0.8],
             ['mouthPressLeft', 0.4], ['mouthPressRight', 0.4]],
  surpris:  [['eyeWideLeft', 0.7], ['eyeWideRight', 0.7], ['jawOpen', 0.3]],
  reflechi: [['browDownLeft', 0.35], ['browDownRight', 0.35]],
};
// Équivalents presets VRM quand les binds existent (sarra, VRM 1.0…).
const TABLE_EMOTIONS_VRM = {
  joie: 'happy', sourire: 'happy', grand_sourire: 'happy',
  triste: 'sad', colere: 'angry', surpris: 'surprised',
};
const emoCibles = {};
const emoLisse = {};

window.setEmotion = function (nom, intensite = 1.0) {
  const cle = String(nom || 'neutre').toLowerCase();
  const k = Math.max(0, Math.min(1, parseFloat(intensite) || 0));
  for (const m of Object.keys(emoCibles)) delete emoCibles[m];
  for (const [morph, poids] of (TABLE_EMOTIONS[cle] || TABLE_EMOTIONS.neutre)) {
    if (morph in cibles) emoCibles[morph] = Math.min(1, k * poids);
  }
  // Presets VRM : émotions directes quand les binds existent.
  if (vrmActif && vrmActif.expressionManager) {
    try {
      const em = vrmActif.expressionManager;
      for (const p of ['happy', 'sad', 'angry', 'surprised']) {
        try { em.setValue(p, 0); } catch (_) { /* absent */ }
      }
      const preset = TABLE_EMOTIONS_VRM[cle];
      if (preset) em.setValue(preset, k);
    } catch (_) { /* sans danger */ }
  }
  const debugSarra = document.getElementById('debug-sarra');
  if (debugSarra) debugSarra.textContent = 'Émotion: ' + cle;
  return true;
};

window.updateVisageNom = function (nomForme, ouverture, intensite) {
  const debugSarra = document.getElementById('debug-sarra');
  if (debugSarra) debugSarra.textContent = 'Forme: ' + nomForme + ', Ouv=' + ouverture + ', Int=' + intensite;
  const nom = String(nomForme || 'REST').toUpperCase();
  const o = Math.max(0, Math.min(1, parseFloat(ouverture) || 0));
  const k = Math.min(1, Math.max(0, Math.min(1, parseFloat(intensite) || 0)) * GAIN_ARTICULATION);
  suivreProsodie(k);
  // Raz ciblée : seules les formes du précédent appel retombent (remise à
  // zéro franche, le lerp asymétrique fond la transition). Le ×0.30 global
  // d'avant tuait tout à 50 Hz — la bouche ne s'ouvrait jamais vraiment.
  if (window._derniersMorphs) {
    for (const m of window._derniersMorphs) { if (m in cibles) cibles[m] = 0; }
  }
  if (window._derniersPresetsVRM) {
    for (const p of window._derniersPresetsVRM) { if (p in CIBLES_VRM) CIBLES_VRM[p] = 0; }
  }
  const actifs = [];
  const appliquer = (table) => {
    for (const [morph, poids] of (table[nom] || [])) {
      if (morph in cibles) { cibles[morph] = Math.min(1, k * poids); actifs.push(morph); }
    }
  };
  appliquer(TABLE_NOMS_RPM);
  appliquer(TABLE_NOMS_HOUDA);
  appliquerForceFermetures();
  // Mâchoire continue : le max avec la table, jamais en-dessous du flux audio,
  // le tout à l'échelle du gain calibré, puis amplitude globale.
  if ('jawOpen' in cibles) {
    cibles['jawOpen'] = Math.min(1, Math.max(cibles['jawOpen'] || 0, o * (0.25 + 0.75 * k)) * GAIN_MACHOIRE);
    if (!actifs.includes('jawOpen')) actifs.push('jawOpen');
  }
  appliquerAmplitude();
  appliquerTrims(VISEME_ID_PAR_NOM[nom] ?? 0);
  window._derniersMorphs = actifs;
  const presetsActifs = [];
  if (vrmActif) {
    for (const [preset, poids] of (TABLE_NOMS_VRM[nom] || [])) {
      CIBLES_VRM[preset] = Math.min(1, k * poids);
      presetsActifs.push(preset);
    }
  }
  window._derniersPresetsVRM = presetsActifs;
  return true;
};

// Remet la bouche au repos entre deux phrases.
window.resetVisemes = function () {
  for (const nom of Object.keys(cibles)) cibles[nom] = 0;
  for (const idx of Object.keys(ciblesIndex)) ciblesIndex[idx] = 0;
  for (const cle of Object.keys(CIBLES_VRM)) { if (cle !== 'blink') CIBLES_VRM[cle] = 0; }
  return true;
};

// Sourire / expression d'écoute (ex. appelée sur état LISTENING).
window.setExpression = function (nom, valeur) {
  const k = Math.max(0, Math.min(1, parseFloat(valeur) || 0));
  if (nom in cibles) { cibles[nom] = k; return true; }
  // VRM : bouche lissée via CIBLES_VRM, émotions en direct. mouthSmile -> happy.
  if (vrmActif) {
    const cle = (String(nom) === 'mouthSmile') ? 'happy' : String(nom);
    if (cle in CIBLES_VRM) { CIBLES_VRM[cle] = k; return true; }
    try {
      if (vrmActif.expressionManager.getExpression(cle)) {
        vrmActif.expressionManager.setValue(cle, k);
        return true;
      }
    } catch (_) { /* preset inconnu */ }
  }
  return false;
};

// --- Lissage : avance les poids affichés vers les cibles ---
// Asymétrique : la bouche s'ouvre franchement (attaque rapide) et se referme
// souplement (relâche plus lent) — l'inverse fait "marionnette qui claque".
const LERP_OUVRE_DEFAUT = 0.50;
const LERP_FERME_DEFAUT = 0.22;
let LERP_OUVRE = LERP_OUVRE_DEFAUT;
let LERP_FERME = LERP_FERME_DEFAUT;
function lerpBouche(visez, affichez) {
  return visez > affichez ? LERP_OUVRE : LERP_FERME;
}
function appliquerLerp() {
  // Émotions lissées comme la bouche (transitions douces, jamais de snap).
  for (const [nom, vise] of Object.entries(emoCibles)) {
    emoLisse[nom] = (emoLisse[nom] ?? 0) + (vise - (emoLisse[nom] ?? 0)) * LERP_FERME;
    if (Math.abs(emoLisse[nom]) < 0.002) emoLisse[nom] = 0;
  }
  for (const nom of Object.keys(emoLisse)) {
    if (!(nom in emoCibles)) delete emoLisse[nom];
  }
  if (teteMesh) {
    const influences = teteMesh.morphTargetInfluences;
    const tMicro = performance.now() / 1000;
    for (const [nom, idx] of Object.entries(dicoMorphs)) {
      // Les entrées morph_* des modèles sans noms sont pilotées par indices
      // ci-dessous : on les ignore ici pour ne pas les appliquer deux fois.
      if (morphsSansNoms && /^morph_\d+$/.test(nom)) continue;
      const vise = cibles[nom] ?? 0;
      if (!Number.isFinite(vise)) continue;
      // Auto-réparation : un résidu NaN se propagerait sinon à jamais.
      if (!Number.isFinite(courants[nom])) courants[nom] = vise;
      courants[nom] = courants[nom] + (vise - courants[nom]) * lerpBouche(vise, courants[nom]);
      // Coupe les résidus pour éviter un tremblement permanent autour de 0.
      let aff = Math.abs(courants[nom]) < 0.001 ? 0 : courants[nom];
      aff += emoLisse[nom] || 0;
      aff += microLevres(nom, tMicro);
      influences[idx] = Math.max(0, Math.min(1, aff));
    }
  }
  // Meshes secondaires (dents...) : mêmes valeurs déjà lissées, sans re-lissage.
  for (const { mesh, dico } of dicosParMesh) {
    const influences = mesh.morphTargetInfluences;
    if (!influences) continue;
    for (const [nom, idx] of Object.entries(dico)) {
      if (idx < influences.length && nom in courants) {
        influences[idx] = courants[nom];
      }
    }
  }
  // Cibles par indice (modèles sans noms) : appliquées à TOUS les meshes à
  // morphs, car sur les GLB Sketchfab multi-parties la bouche peut se trouver
  // sur un autre mesh que celui choisi comme visage.
  const clesIndex = Object.keys(ciblesIndex);
  if (clesIndex.length) {
    for (const mesh of meshesMorphs) {
      const influences = mesh.morphTargetInfluences;
      for (const cle of clesIndex) {
        const idx = Number(cle);
        if (idx < influences.length) {
          const vise = ciblesIndex[cle] ?? 0;
          const aff = courantsIndex[cle] ?? 0;
          courantsIndex[cle] = aff + (vise - aff) * lerpBouche(vise, aff);
          influences[idx] = Math.abs(courantsIndex[cle]) < 0.001 ? 0 : courantsIndex[cle];
        }
      }
    }
  }
}

// --- Lissage VRM : mêmes cibles 50 Hz vers le gestionnaire d'expressions ---
function appliquerLerpVRM() {
  if (!vrmActif || !vrmActif.expressionManager) return;
  for (const cle of Object.keys(CIBLES_VRM)) {
    COURANTS_VRM[cle] = COURANTS_VRM[cle] + (CIBLES_VRM[cle] - COURANTS_VRM[cle]) * lerpBouche(CIBLES_VRM[cle], COURANTS_VRM[cle]);
    const v = Math.abs(COURANTS_VRM[cle]) < 0.001 ? 0 : COURANTS_VRM[cle];
    try { vrmActif.expressionManager.setValue(cle, v); } catch (_) { /* preset absent */ }
  }
}

// --- Réalisme : respiration + suivi de caméra ---
function animerCorps(tempsS, dt) {  // Respiration : soulève doucement la cage (cou + buste via le cou).
  const souffle = Math.sin(tempsS * 1.4) * 0.5 + 0.5; // 0..1, ~12 cycles/min.
  // Profondeur variable : le souffle n'est jamais métronomique.
  const profSouffle = 0.8 + 0.2 * Math.sin(tempsS * 0.13 + 1.0);
  // Prosodie : retombe sans trame vocale ; tête et sourcils suivent l'emphase.
  decroitreProsodie();
  if (couOs) {
    couOs.position.y = couOs.userData.y0 ?? (couOs.userData.y0 = couOs.position.y);
    couOs.position.y = couOs.userData.y0 + souffle * 0.008 * profSouffle;
    couOs.rotation.x = Math.sin(tempsS * 1.4) * 0.015;
  }
  // Micro-vie VRM (couOs nul sur VRM) : souffle poitrine + dérive de tête en
  // bruit doux (pas de sinus pur = pas d'effet métronome), roulis inclus.
  // La tête suit le regard LENTEMENT via teteSuitRegard, jamais en claquant.
  if (noeudPoitrineVRM && rotBasePoitrine) {
    const rx = rotBasePoitrine.x + souffle * 0.012 * profSouffle;
    if (Number.isFinite(rx)) noeudPoitrineVRM.rotation.x = rx;
  }
  if (noeudTeteVRM && rotBaseTete) {
    const ry = rotBaseTete.y
      + bruitDoux(tempsS, 0.31, 0.53, 0.83, 0.4, 1.9) * 0.022
      + teteSuitRegard.x * 0.10;
    const rx = rotBaseTete.x
      + bruitDoux(tempsS, 0.23, 0.47, 0.71, 2.6, 0.7) * 0.015
      - teteSuitRegard.y * 0.07
      - emphaseParole * 0.035;
    const rz = (rotBaseTete.z || 0)
      + bruitDoux(tempsS, 0.19, 0.37, 0.59, 4.4, 1.2) * 0.010;
    if (Number.isFinite(ry)) noeudTeteVRM.rotation.y = ry;
    if (Number.isFinite(rx)) noeudTeteVRM.rotation.x = rx;
    if (Number.isFinite(rz)) noeudTeteVRM.rotation.z = rz;
  }
  // Regard : la tête suit légèrement la souris (= caméra utilisateur), angles bridés.
  // En repli GLB (pas de lookAt three-vrm), les saccades y participent un peu.
  const yaw = THREE.MathUtils.clamp(souris.x * 0.35 + regard.x * 0.12, -0.4, 0.4);
  const pitch = THREE.MathUtils.clamp(-souris.y * 0.22 - regard.y * 0.08 - emphaseParole * 0.05, -0.25, 0.25);
  if (teteOs) {
    // Lerp critique ici aussi : évite les à-coups quand la souris saute.
    teteOs.rotation.y += (yaw - teteOs.rotation.y) * 0.08;
    teteOs.rotation.x += (pitch - teteOs.rotation.x) * 0.08;
    teteOs.rotation.z += (bruitDoux(tempsS, 0.19, 0.37, 0.59, 4.4, 1.2) * 0.010 - teteOs.rotation.z) * 0.05;
  }
  // Micro-clignement procédural (au cas où le modèle n'en a pas) + VRM :
  // intervalles variables + doubles occasionnels ; le miroir paupières VRM
  // (blink + regard bas) est posé dans animerRegard, après vrm.update().
  if (tempsS >= prochainClignement) {
    // Anti-rattrapage : après un gros saut temporel (freeze, rendu lent),
    // on reprogramme SANS déclencher, sinon chaque frame lente referme
    // les yeux en permanence.
    if (tempsS - prochainClignement < 1.0) {
      clignementJusqua = tempsS + 0.12;
      if (Math.random() < 0.12) secondClignementAt = tempsS + 0.28;
      else secondClignementAt = -1;
    }
    prochainClignement = tempsS + 2.5 + Math.random() * 3.5;
  } else if (secondClignementAt > 0 && tempsS >= secondClignementAt) {
    clignementJusqua = tempsS + 0.12;
    secondClignementAt = -1;
  }
  if (teteMesh && 'eyesClosed' in cibles) {
    cibles['eyesClosed'] = tempsS < clignementJusqua ? 1.0 : 0.0;
  }
  if (vrmActif) {
    CIBLES_VRM.blink = tempsS < clignementJusqua ? 1.0 : 0.0;
  }
  // Sourcils sur la parole (gardé : absent = ignoré). Évite la raz visèmes :
  // browInnerUp ne figure dans aucune table, il persiste entre les trames.
  if ('browInnerUp' in cibles) {
    cibles['browInnerUp'] = Math.max(cibles['browInnerUp'] || 0, emphaseParole * 0.35);
  }
}

// --- Prosodie : la parole bouge la tête et les sourcils ---
// L'enveloppe k de chaque trame alimente une emphase lissée (attaque vive,
// relâche lente). Sans ça, tête et sourcils restent neutres pendant la
// parole — la bouche seule fait marionnette. Retombe à 0 sans trame (>200 ms).
let energieVoix = 0;
let emphaseParole = 0;
let dernierEventVoix = -1e9;

function suivreProsodie(k) {
  const maintenant = performance.now();
  dernierEventVoix = maintenant;
  energieVoix += (k - energieVoix) * (k > energieVoix ? 0.5 : 0.15);
  emphaseParole += (k - emphaseParole) * (k > emphaseParole ? 0.4 : 0.08);
}

function decroitreProsodie() {
  if (performance.now() - dernierEventVoix > 200) {
    energieVoix *= 0.9;
    emphaseParole *= 0.9;
    if (energieVoix < 0.002) energieVoix = 0;
    if (emphaseParole < 0.002) emphaseParole = 0;
  }
}

// --- Regard vivant : saccades ---
// Sans ça les yeux restent plantés caméra (effet "fixe"). Le regard saute
// entre des points toutes les 1-2 s et revient souvent vers l'interlocuteur.
// Écrit APRÈS vrm.update() (le lookAt three-vrm pose les mêmes presets
// pendant l'update : nos valeurs, posées après, gagnent), AVANT
// expressionManager.update() qui les applique aux morphs.
const regard = { x: 0, y: 0 };
const regardCible = { x: 0, y: 0 };
let prochaineSaccade = 1.5;
// Micro-saccades : tremblement de fixation autour du point regardé.
// L'œil vivant ne tient jamais parfaitement en place.
const microRegard = { x: 0, y: 0 };
let prochaineMicroSaccade = 0.5;
// Tête suit le regard LENTEMENT (les yeux claquent, la tête glisse ou pas).
const teteSuitRegard = { x: 0, y: 0 };
// Clignement à intervalles variables + doubles occasionnels.
let prochainClignement = 3.0;
let secondClignementAt = -1;
let clignementJusqua = -1;

// Bruit doux : 3 sinus incommensurables ≈ mouvement organique, jamais
// périodique visible (un sinus pur = robot). Normalisé ~[-1, 1].
function bruitDoux(t, a, b, c, p1, p2) {
  return (Math.sin(t * a + p1) + Math.sin(t * b + p2) + Math.sin(t * c + p1 * 1.7)) / 3;
}

// Micro-bouche au repos, ajoutée à l'écriture (jamais en conflit avec les
// visèmes qui dominent quand ils sont actifs) : mâchoire sur le souffle,
// coins asymétriques qui dérivent — une bouche parfaitement symétrique et
// immobile fait masque.
function microLevres(nom, t) {
  if (nom === 'jawOpen') return 0.008 * (0.5 + 0.5 * Math.sin(t * 1.4));
  if (nom === 'mouthSmileLeft') return 0.030 * bruitDoux(t, 0.23, 0.41, 0.67, 0.5, 2.1);
  if (nom === 'mouthSmileRight') return 0.030 * bruitDoux(t, 0.27, 0.43, 0.61, 3.3, 0.9);
  if (nom === 'mouthClose') return 0.012 * (0.5 + 0.5 * Math.sin(t * 1.4 + 1.2));
  return 0;
}
// --- Micro-vie VRM : os résolus au chargement ---
// Tête = ancrage de la cible regard + dérive ; poitrine = souffle.
// Rotations de base re-capturées à chaque appliquerConfig (le cadrage auto
// change le yaw : les offsets repartent sur la bonne base, sans saut visible
// vu leurs amplitudes).
let noeudTeteVRM = null;
let noeudPoitrineVRM = null;
let rotBaseTete = null;
let rotBasePoitrine = null;
const cibleRegard = new THREE.Object3D();
cibleRegard.position.set(0, 1.4, 0.8);
scene.add(cibleRegard);
const _tmpMonde = new THREE.Vector3();

function animerRegard(tempsS, dt) {
  if (tempsS >= prochaineSaccade) {
    regardCible.x = (Math.random() * 2 - 1) * 0.85;
    regardCible.y = (Math.random() * 2 - 1) * 0.45;
    if (Math.random() < 0.45) { regardCible.x *= 0.2; regardCible.y *= 0.2; }
    prochaineSaccade = tempsS + 1.1 + Math.random() * 2.2;
  }
  const f = Math.min(1, dt * 9);
  regard.x += (regardCible.x - regard.x) * f;
  regard.y += (regardCible.y - regard.y) * f;
  // Micro-saccades : la fixation tremble en permanence, minuscule.
  if (tempsS >= prochaineMicroSaccade) {
    microRegard.x = (Math.random() * 2 - 1) * 0.10;
    microRegard.y = (Math.random() * 2 - 1) * 0.08;
    prochaineMicroSaccade = tempsS + 0.25 + Math.random() * 0.45;
  }
  // Tête : suit le regard avec un temps de retard (jamais de claquement).
  const fl = Math.min(1, dt * 1.4);
  teteSuitRegard.x += (regard.x * 0.5 - teteSuitRegard.x) * fl;
  teteSuitRegard.y += (regard.y * 0.5 - teteSuitRegard.y) * fl;
  const rx = regard.x + microRegard.x;
  const ry = regard.y + microRegard.y;
  // three-vrm (VRM 0.x avec binds comme VRM 1.0) : presets regard directs.
  // Paupière couplée : en regard bas la paupière haute descend un peu.
  const paupiereBas = Math.max(0, -ry) * 0.18;
  if (vrmActif && vrmActif.expressionManager) {
    try {
      const em = vrmActif.expressionManager;
      em.setValue('lookLeft', Math.max(0, -rx));
      em.setValue('lookRight', Math.max(0, rx));
      em.setValue('lookUp', Math.max(0, ry));
      em.setValue('lookDown', Math.max(0, -ry));
    } catch (_) { /* presets absents */ }
  }
  // Cible mobile pour le lookAt osseux (globes oculaires VRM 0.x : les
  // meshes yeux n'ont pas de morphs, seuls les os les bougent).
  if (noeudTeteVRM && vrmActif && vrmActif.lookAt) {
    noeudTeteVRM.getWorldPosition(_tmpMonde);
    cibleRegard.position.set(
      _tmpMonde.x + rx * 0.35,
      _tmpMonde.y + 0.05 + ry * 0.22,
      _tmpMonde.z + 0.6
    );
  }
  // Repli morphs directs (binds VRM vides, ex. houda) : mêmes directions.
  if ('eyeLookOutLeft' in cibles) {
    cibles['eyeLookOutLeft'] = Math.max(0, -rx);
    cibles['eyeLookInRight'] = Math.max(0, -rx);
    cibles['eyeLookInLeft'] = Math.max(0, rx);
    cibles['eyeLookOutRight'] = Math.max(0, rx);
    cibles['eyeLookUpLeft'] = Math.max(0, ry);
    cibles['eyeLookUpRight'] = Math.max(0, ry);
    cibles['eyeLookDownLeft'] = Math.max(0, -ry);
    cibles['eyeLookDownRight'] = Math.max(0, -ry);
  }
  // Paupières : clignement + suivent le regard bas (gardé : absent = ignoré).
  if ('eyeBlinkLeft' in cibles) {
    cibles['eyeBlinkLeft'] = Math.max(CIBLES_VRM.blink, paupiereBas);
    cibles['eyeBlinkRight'] = Math.max(CIBLES_VRM.blink, paupiereBas);
  }
}

// Le regard piloté par la souris est volontairement limité à la fenêtre des
// réglages. En dehors de cette zone, l'avatar reste immobile : aucun suivi
// global de la souris ne doit modifier sa pose.
const avatarTools = document.getElementById('avatar-tools');
avatarTools?.addEventListener('pointermove', (e) => {
  const bounds = avatarTools.getBoundingClientRect();
  souris.x = ((e.clientX - bounds.left) / bounds.width) * 2 - 1;
  souris.y = ((e.clientY - bounds.top) / bounds.height) * 2 - 1;
});
avatarTools?.addEventListener('pointerleave', () => {
  souris.x = 0;
  souris.y = 0;
});

// --- Boucle principale : pas fixe pour le lerp, rendu à chaque frame ---
function boucle() {
  requestAnimationFrame(boucle);
  window.__frames = (window.__frames || 0) + 1;
  const maintenant = performance.now();
  const dtTrame = Math.min(0.1, (maintenant - derniereHeure) / 1000); // Anti-spirale si onglet gelé.
  accumulateur += dtTrame;
  derniereHeure = maintenant;
  while (accumulateur >= PAS) { // Même vitesse sur écran 60 Hz ou 120 Hz.
    appliquerLerp();
    appliquerLerpVRM();
    accumulateur -= PAS;
  }
  animerCorps(maintenant / 1000, dtTrame);
  // VRM : spring bones + regard + application des expressions lissées.
  // (expressionManager.update() est idempotent : sans danger si déjà fait.)
  if (vrmActif) {
    try { vrmActif.update(dtTrame); } catch (_) { /* spring bones optionnels */ }
    animerRegard(maintenant / 1000, dtTrame);
    try { vrmActif.expressionManager.update(); } catch (_) { /* sans danger */ }
  } else {
    animerRegard(maintenant / 1000, dtTrame);
  }
  controles.update();
  rendu.render(scene, camera);
}
boucle();

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  rendu.setSize(window.innerWidth, window.innerHeight);
  appliquerMasque();
});

// Démarrage de l'avatar principal : le VRM local est prioritaire, le GLB est
// utilisé uniquement comme solution de secours si le CDN ou le fichier VRM
// rencontre un problème. ?model=xxx.vrm permet d'imposer un modèle (test, diagnostic).
initialiserSelectionAvatar();
chargerGainsLipsync();
try {
  const params = new URLSearchParams(location.search);
  const modeleForce = params.get('model');
  if (modeleForce && /^[^\\/]+\.(vrm|glb)$/i.test(modeleForce)) chargerAvatarDepuisListe(modeleForce);
  else chargerVRM();
  // Test : ?viseme=10&intensite=1 fige un visème après chargement (diagnostic).
  if (params.get('viseme') !== null) {
    const tv = params.get('viseme'), tk = parseFloat(params.get('intensite') || '1');
    const attente = setInterval(() => {
      if (window.sarraReady && teteMesh) {
        clearInterval(attente);
        window.updateViseme(tv, tk);
      }
    }, 200);
  }
  // Test : ?visnom=S&ouv=0.2&int=0.8 fige une forme nommée (diagnostic).
  if (params.get('visnom') !== null) {
    const tn = params.get('visnom'), to = parseFloat(params.get('ouv') || '0.3'),
      tk2 = parseFloat(params.get('int') || '0.8');
    const attente2 = setInterval(() => {
      if (window.sarraReady && teteMesh) {
        clearInterval(attente2);
        window.updateVisageNom(tn, to, tk2);
      }
    }, 200);
  }
  // Test : ?emotion=joie fige une émotion après chargement (diagnostic).
  if (params.get('emotion') !== null) {
    const te = params.get('emotion');
    const attente3 = setInterval(() => {
      if (window.sarraReady && teteMesh) {
        clearInterval(attente3);
        window.setEmotion(te, 1.0);
      }
    }, 200);
  }
  // Test : ?mesure=1 appelle mesurerBouche après chargement (diagnostic).
  if (params.get('mesure') !== null) {
    const attente4 = setInterval(() => {
      if (window.sarraReady && teteMesh) {
        clearInterval(attente4);
        window.fondCalibration(true);
        setTimeout(() => {
          const res = window.mesurerBouche();
          console.info('[Sarra] mesure test:', JSON.stringify(res));
          window.fondCalibration(false);
        }, 600);
      }
    }, 200);
  }
  // Test : ?dumpmorphs=1 loggue les influences morphs (diagnostic explosion).
  if (params.get('dumpmorphs') !== null) {
    const attente5 = setInterval(() => {
      if (window.sarraReady && teteMesh) {
        clearInterval(attente5);
        setTimeout(() => {
          const stats = [];
          for (const mesh of meshesMorphs) {
            const inf = mesh.morphTargetInfluences || [];
            let mx = 0, nan = 0;
            for (const v of inf) {
              if (!Number.isFinite(v)) nan++;
              else mx = Math.max(mx, Math.abs(v));
            }
            stats.push(mesh.name + ':n=' + inf.length + ' max=' + mx.toFixed(3) + ' nan=' + nan);
          }
          console.info('[Sarra] dump morphs: ' + stats.join(' | ')
            + ' || cibles=' + Object.keys(cibles).length
            + ' emo=' + JSON.stringify(emoCibles).slice(0, 200));
          try {
            const hw = new THREE.Vector3();
            if (noeudTeteVRM) noeudTeteVRM.getWorldPosition(hw);
            const rot = (n) => n ? [n.rotation.x, n.rotation.y, n.rotation.z]
              .map((v) => (Number.isFinite(v) ? v.toFixed(3) : 'NAN')).join(',') : '?';
            let hum = null;
            try { hum = vrmActif && vrmActif.humanoid; } catch (_) {}
            const oe = (s) => { try { return hum.getNormalizedBoneNode(s); } catch (_) { return null; } };
            console.info('[Sarra] dump scene: frames=' + (window.__frames || 0)
              + ' cam=' + camera.position.toArray().map((v) => v.toFixed(2)).join(',')
              + ' head=' + hw.toArray().map((v) => v.toFixed(2)).join(',')
              + ' rotTete=' + rot(noeudTeteVRM)
              + ' rotOeilG=' + rot(oe('leftEye')) + ' rotOeilD=' + rot(oe('rightEye'))
              + ' cibleRegard=' + cibleRegard.position.toArray().map((v) => v.toFixed(2)).join(','));
          } catch (e) { console.info('[Sarra] dump scene impossible: ' + e); }
        }, 1500);
      }
    }, 200);
  }
} catch (_) { chargerVRM(); }
