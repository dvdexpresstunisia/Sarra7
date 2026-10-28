# extract_morphs.py (version améliorée)
import struct
import json
from pathlib import Path

glb_path = Path("assets/avatar/sarra.glb")

if not glb_path.exists():
    print(f"❌ Fichier introuvable : {glb_path}")
else:
    print(f"🔍 Analyse de {glb_path.name}...")
    with open(glb_path, "rb") as f:
        magic, version, length = struct.unpack("<III", f.read(12))
        if magic != 0x46546C67:
            print("❌ Fichier GLB invalide.")
        else:
            chunk_length, chunk_type = struct.unpack("<II", f.read(8))
            if chunk_type == 0x4E4F534A:
                json_data = f.read(chunk_length).decode('utf-8')
                gltf = json.loads(json_data)
                
                # Extraire les morph targets avec leurs indices
                morph_info = []
                for mesh_idx, mesh in enumerate(gltf.get("meshes", [])):
                    for prim_idx, primitive in enumerate(mesh.get("primitives", [])):
                        if "targets" in primitive:
                            print(f"\n✅ Mesh {mesh_idx}, Primitive {prim_idx} : {len(primitive['targets'])} morph targets")
                            for target_idx, target in enumerate(primitive["targets"]):
                                attributs = list(target.keys())
                                print(f"   Index {target_idx}: attributs = {attributs}")
                                morph_info.append({
                                    'mesh': mesh_idx,
                                    'primitive': prim_idx,
                                    'index': target_idx,
                                    'attributes': attributs
                                })
                
                if morph_info:
                    print(f"\n📊 Total: {len(morph_info)} morph targets trouvés")
                    print("\n👉 Utilisez ces INDICES dans app.js pour le mapping des visèmes")
                else:
                    print("⚠️ Aucun morph target trouvé.")
            else:
                print("❌ Chunk JSON non trouvé.")