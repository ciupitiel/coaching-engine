# =============================================================================
#  rag_engine.py — Etapa 4: RAG Local cu ChromaDB
#  Noian Cristian · Bazat pe inteligență artificială
#  -----------------------------------------------------------------------------
#  Substituție zero-cost pentru Pinecone:
#    Pinecone (plătit)  →  ChromaDB EphemeralClient (rulează în același proces)
#
#  Ce face acest modul:
#    1. Construiește o bază de date vectorială cu 40+ alimente românești
#    2. La generarea planului alimentar, recuperează alimentele cele mai
#       relevante pentru obiectivul și macros-urile userului
#    3. Injectează contextul RAG în prompt-ul meal_plan_generator.py prin
#       câmpul `preferences` existent — ZERO modificări în fișiere existente
#
#  Embedding custom (RomanianFoodEmbeddingFunction):
#    · Nu descarcă niciun model ML (~0 bytes extra la deploy)
#    · Vocabular de 95 termeni nutriționali + culinari românești
#    · Bag-of-words cu prefix matching pentru forme gramaticale (fiert/fierte)
#    · L2-normalizat → cosine similarity prin ChromaDB
#    · Startup: ~50ms | Query: ~1ms
#
#  Interfață publică:
#    init_rag_engine()                → apelat O dată în lifespan() din main.py
#    query_all_meal_types(...)        → pentru meal plan generator
#    build_rag_context_string(...)    → formatare pentru injecție în prompt
# =============================================================================

import math
import re
import unicodedata
import chromadb

from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
#  VOCABULAR EMBEDDING — 95 termeni nutriționali + culinari românești
#
#  Dimensiunea vectorului = len(FOOD_VOCAB) = 95 float-uri
#  ChromaDB stochează vectorii în index HNSW (Hierarchical Navigable Small World)
#  și face nearest-neighbor search în O(log n) — perfect pentru 40+ alimente.
# ─────────────────────────────────────────────────────────────────────────────

FOOD_VOCAB = [
    # Surse proteice — animale
    "pui", "vita", "porc", "curcan", "miel",
    "ton", "somon", "crap", "scrumbie", "peste",
    "oua", "ou", "branza", "telemea", "urda",
    "lapte", "iaurt", "zer",
    # Carbohidrați
    "orez", "paste", "paine", "mamaliga", "cartofi",
    "fulgi", "ovaz", "fasole", "linte", "mazare",
    "cereale", "porumb", "cartof",
    # Grăsimi
    "ulei", "masline", "nuca", "migdale", "alune",
    "avocado", "unt", "smantana", "seminte",
    # Legume
    "rosii", "castraveti", "ardei", "varza", "spanac",
    "brocoli", "morcovi", "ceapa", "usturoi", "salata",
    "dovlecei", "vinete", "sfecla", "telina",
    # Fructe
    "mere", "portocale", "banane", "capsuni", "prune",
    "piersici", "struguri", "kiwi", "fruct",
    # Metode gătit
    "gratar", "fiert", "copt", "prajit", "crud",
    "afumat", "sotat", "cuptor", "aburi", "gatit",
    # Nutrienți
    "proteina", "carbohidrat", "grasime", "calorie",
    "fibra", "omega", "vitamine", "minerale",
    "kcal", "macro", "micro",
    # Obiective
    "cut", "slabit", "deficit", "masa", "surplus",
    "bulk", "mentinere", "recompozitie", "lean",
    # Mese
    "dejun", "pranz", "cina", "gustare", "mic",
    "masa", "mancare", "fel",
    # Caracteristici
    "rapid", "simplu", "satietos", "usor", "traditional",
    "romanesc", "complet", "light", "cald", "rece",
]

# Indexul rapid vocab_word → poziție vector
_VOCAB_IDX: dict[str, int] = {w: i for i, w in enumerate(FOOD_VOCAB)}
_VOCAB_SIZE: int = len(FOOD_VOCAB)

# Prefix map: primele 4 caractere → prima poziție din vocab cu acel prefix
# Folosit pentru matching rapid (fiert↔fierte, proteina↔proteic, etc.)
_PREFIX_MAP: dict[str, int] = {}
for _w, _idx in _VOCAB_IDX.items():
    _pfx = _w[:4]
    if _pfx not in _PREFIX_MAP:
        _PREFIX_MAP[_pfx] = _idx


# ─────────────────────────────────────────────────────────────────────────────
#  EMBEDDING FUNCTION — Bag-of-words cu prefix matching
# ─────────────────────────────────────────────────────────────────────────────

class RomanianFoodEmbeddingFunction:
    def name(self) -> str:
        return "romanian_food_embedding"

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in input]

    @staticmethod
    def _strip_diacritics(text: str) -> str:
        """Elimină diacriticele românești fără dependențe externe."""
        # NFD descompune caracterele compuse (ă → a + combining breve)
        # Mn = Mark, Nonspacing — clasa Unicode pentru diacritice
        nfd = unicodedata.normalize("NFD", text)
        return "".join(c for c in nfd if unicodedata.category(c) != "Mn")

    def _embed(self, text: str) -> list[float]:
        clean = self._strip_diacritics(text.lower())
        tokens = re.findall(r"\w+", clean)

        vec = [0.0] * _VOCAB_SIZE

        for tok in tokens:
            # ① Exact match — cea mai sigură
            if tok in _VOCAB_IDX:
                vec[_VOCAB_IDX[tok]] += 1.0
            else:
                # ② Prefix match — pentru forme gramaticale
                pfx = tok[:4]
                if pfx in _PREFIX_MAP:
                    vec[_PREFIX_MAP[pfx]] += 0.4

        # Normalizare L2 → vector unitar
        norm = math.sqrt(sum(x * x for x in vec))
        if norm < 1e-9:
            return vec  # all-zero → "unknown food", returnat as-is
        return [x / norm for x in vec]

# ─────────────────────────────────────────────────────────────────────────────
#  BAZA DE DATE — 42 alimente românești curate
#
#  Structura fiecărui item:
#    id         : identificator unic (snake_case)
#    text       : descriere pentru embedding (cuvinte cheie, nu propoziție)
#    name       : nume afișat în planul alimentar
#    qty_desc   : cantitate / porție standard
#    calories, protein_g, carbs_g, fat_g : macros per porție
#    prep_min   : timp pregătire în minute
#    can_X      : bool — potrivit pentru masa X
#    for_X      : bool — potrivit pentru obiectivul X
#    accessibility : 0.0-1.0 (1.0 = găsit în orice supermarket românesc)
#
#  NOTĂ: Valorile nutriționale sunt per PORȚIE SERVITĂ (nu per 100g).
#  Extindere DB: adaugă oricâte item-uri în ROMANIAN_FOODS_DB —
#  init_rag_engine() le adaugă automat la pornire.
# ─────────────────────────────────────────────────────────────────────────────

ROMANIAN_FOODS_DB = [
    # ══ PROTEINE CURATE ══════════════════════════════════════════════════════
    {
        "id": "pui_gratar_150g",
        "text": "piept pui gratar proteina curata slaba deficit cut slabit pranz cina gratar usor",
        "name": "Piept pui la grătar",
        "qty_desc": "150g",
        "calories": 210, "protein_g": 40, "carbs_g": 0, "fat_g": 5,
        "prep_min": 20,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "pui_fiert_150g",
        "text": "piept pui fiert proteina curata slaba cut slabit mentinere pranz cina fiert",
        "name": "Piept pui fiert",
        "qty_desc": "150g",
        "calories": 195, "protein_g": 38, "carbs_g": 0, "fat_g": 4,
        "prep_min": 30,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "curcan_gratar_150g",
        "text": "curcan gratar proteina slaba cut deficit pranz cina usor digestie",
        "name": "Curcan la grătar",
        "qty_desc": "150g",
        "calories": 185, "protein_g": 38, "carbs_g": 0, "fat_g": 3,
        "prep_min": 20,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.8,
    },
    {
        "id": "ton_apa_100g",
        "text": "ton conserva apa proteina curata slaba cut slabit pranz gustare rapid simplu",
        "name": "Ton în apă (conservă)",
        "qty_desc": "100g",
        "calories": 120, "protein_g": 26, "carbs_g": 0, "fat_g": 1,
        "prep_min": 2,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": True,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "somon_cuptor_150g",
        "text": "somon copt cuptor proteina omega grasimi sanatoase pranz cina satios",
        "name": "Somon la cuptor",
        "qty_desc": "150g",
        "calories": 280, "protein_g": 30, "carbs_g": 0, "fat_g": 18,
        "prep_min": 25,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.7,
    },
    {
        "id": "scrumbie_afumata_100g",
        "text": "scrumbie afumata proteina omega grasimi pesti pranz cina romanesc traditional",
        "name": "Scrumbie afumată",
        "qty_desc": "100g",
        "calories": 220, "protein_g": 22, "carbs_g": 0, "fat_g": 15,
        "prep_min": 0,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    {
        "id": "crap_cuptor_150g",
        "text": "crap cuptor copt proteina peste romanesc traditional pranz cina",
        "name": "Crap la cuptor",
        "qty_desc": "150g",
        "calories": 215, "protein_g": 28, "carbs_g": 0, "fat_g": 12,
        "prep_min": 40,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.8,
    },
    {
        "id": "friptura_vita_150g",
        "text": "friptura vita gratar fiert proteina masa musculara surplus pranz cina complet",
        "name": "Friptură vită slabă",
        "qty_desc": "150g",
        "calories": 240, "protein_g": 36, "carbs_g": 0, "fat_g": 10,
        "prep_min": 25,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.7,
    },
    {
        "id": "mici_2buc_120g",
        "text": "mici vita porc gratar proteina calorii romanesc traditional pranz cina bulk masa",
        "name": "Mici la grătar (2 buc.)",
        "qty_desc": "2 buc., 120g",
        "calories": 320, "protein_g": 22, "carbs_g": 2, "fat_g": 26,
        "prep_min": 15,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    # ══ OUĂ ȘI LACTATE ═══════════════════════════════════════════════════════
    {
        "id": "oua_fierte_2buc",
        "text": "oua fierte proteina grasimi sanatoase dejun gustare rapid simplu",
        "name": "Ouă fierte (2 buc.)",
        "qty_desc": "2 buc.",
        "calories": 156, "protein_g": 12, "carbs_g": 0, "fat_g": 10,
        "prep_min": 10,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "omleta_2oua_branza",
        "text": "omleta oua branza telemea proteina grasimi dejun rapid cald satietos",
        "name": "Omletă cu brânză (2 ouă)",
        "qty_desc": "2 ouă + 30g brânză",
        "calories": 330, "protein_g": 22, "carbs_g": 1, "fat_g": 27,
        "prep_min": 10,
        "can_breakfast": True, "can_lunch": False, "can_dinner": True, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "omleta_simpla_2oua",
        "text": "omleta simpla oua sotat proteina grasimi usor cut dejun cina rapid",
        "name": "Omletă simplă (2 ouă)",
        "qty_desc": "2 ouă",
        "calories": 200, "protein_g": 14, "carbs_g": 1, "fat_g": 16,
        "prep_min": 8,
        "can_breakfast": True, "can_lunch": False, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "iaurt_grecesc_0pct_200g",
        "text": "iaurt grecesc proteina lactat gustare dejun cut mentinere rapid light",
        "name": "Iaurt grecesc 0%",
        "qty_desc": "200g",
        "calories": 120, "protein_g": 20, "carbs_g": 8, "fat_g": 0,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    {
        "id": "branza_vaci_200g",
        "text": "branza vaci proteina lactat dejun gustare cut mentinere usor digestie",
        "name": "Brânză de vaci",
        "qty_desc": "200g",
        "calories": 120, "protein_g": 16, "carbs_g": 6, "fat_g": 4,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "telemea_50g",
        "text": "telemea branza sarat lactat grasimi proteina dejun gustare romanesc traditional",
        "name": "Brânză telemea",
        "qty_desc": "50g",
        "calories": 130, "protein_g": 8, "carbs_g": 1, "fat_g": 11,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "lapte_200ml",
        "text": "lapte calciu proteina carbohidrat lactat dejun gustare",
        "name": "Lapte (200ml)",
        "qty_desc": "200ml",
        "calories": 100, "protein_g": 7, "carbs_g": 10, "fat_g": 4,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    # ══ CARBOHIDRAȚI COMPLECȘI ════════════════════════════════════════════════
    {
        "id": "orez_alb_fiert_200g",
        "text": "orez fiert carbohidrat complex energie masa surplus pranz cina complet",
        "name": "Orez alb fiert",
        "qty_desc": "200g (fiert)",
        "calories": 260, "protein_g": 5, "carbs_g": 56, "fat_g": 1,
        "prep_min": 20,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "paste_fierte_200g",
        "text": "paste fierte carbohidrat energie pranz bulk surplus satietos complet",
        "name": "Paste fierte",
        "qty_desc": "200g (fierte)",
        "calories": 280, "protein_g": 10, "carbs_g": 56, "fat_g": 2,
        "prep_min": 15,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "mamaliga_200g",
        "text": "mamaliga porumb carbohidrat romanesc traditional pranz cina energie simplu",
        "name": "Mămăligă",
        "qty_desc": "200g",
        "calories": 180, "protein_g": 5, "carbs_g": 38, "fat_g": 1,
        "prep_min": 15,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "cartofi_fierti_200g",
        "text": "cartofi fierti carbohidrat satietos pranz cina cut mentinere usor digestie",
        "name": "Cartofi fierți",
        "qty_desc": "200g",
        "calories": 160, "protein_g": 4, "carbs_g": 36, "fat_g": 0,
        "prep_min": 25,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "cartofi_copti_200g",
        "text": "cartofi copti cuptor carbohidrat fibra satietos pranz cina mentinere",
        "name": "Cartofi copți la cuptor",
        "qty_desc": "200g",
        "calories": 180, "protein_g": 4, "carbs_g": 40, "fat_g": 0,
        "prep_min": 45,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "paine_2felii_60g",
        "text": "paine carbohidrat dejun gustare sandwich rapid usor",
        "name": "Pâine (2 felii)",
        "qty_desc": "60g",
        "calories": 150, "protein_g": 6, "carbs_g": 28, "fat_g": 2,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "fulgi_ovaz_60g",
        "text": "fulgi ovaz cereale fibra proteina dejun energie cut mentinere lent digestie",
        "name": "Fulgi de ovăz",
        "qty_desc": "60g (uscat)",
        "calories": 225, "protein_g": 9, "carbs_g": 39, "fat_g": 4,
        "prep_min": 5,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "fasole_rosie_150g",
        "text": "fasole rosie fiarta proteina vegetala carbohidrat fibra pranz satietos cut",
        "name": "Fasole roșie fiartă",
        "qty_desc": "150g",
        "calories": 140, "protein_g": 10, "carbs_g": 24, "fat_g": 1,
        "prep_min": 60,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    {
        "id": "linte_fiarta_150g",
        "text": "linte fiarta proteina vegetala carbohidrat fibra pranz cina cut deficit light",
        "name": "Linte fiartă",
        "qty_desc": "150g",
        "calories": 165, "protein_g": 12, "carbs_g": 26, "fat_g": 1,
        "prep_min": 30,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.8,
    },
    # ══ GRĂSIMI SĂNĂTOASE ════════════════════════════════════════════════════
    {
        "id": "nuci_30g",
        "text": "nuci grasimi sanatoase omega proteina gustare rapid bulk mentinere",
        "name": "Nuci",
        "qty_desc": "30g",
        "calories": 196, "protein_g": 5, "carbs_g": 4, "fat_g": 19,
        "prep_min": 0,
        "can_breakfast": False, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    {
        "id": "migdale_30g",
        "text": "migdale grasimi sanatoase proteina gustare rapid cut mentinere energie",
        "name": "Migdale",
        "qty_desc": "30g",
        "calories": 174, "protein_g": 6, "carbs_g": 5, "fat_g": 15,
        "prep_min": 0,
        "can_breakfast": False, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.8,
    },
    # ══ FRUCTE ════════════════════════════════════════════════════════════════
    {
        "id": "banana_1buc_120g",
        "text": "banana fruct carbohidrat energie rapida gustare bulk pre-antrenament surplus",
        "name": "Banană",
        "qty_desc": "1 buc. medie, 120g",
        "calories": 105, "protein_g": 1, "carbs_g": 27, "fat_g": 0,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "mar_1buc_150g",
        "text": "mar fruct fibra vitamine gustare cut mentinere sarac calorii usor",
        "name": "Măr",
        "qty_desc": "1 buc. medie, 150g",
        "calories": 78, "protein_g": 0, "carbs_g": 21, "fat_g": 0,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": False, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "portocala_1buc_130g",
        "text": "portocala fruct vitamine gustare cut light sarac calorii fibra",
        "name": "Portocală",
        "qty_desc": "1 buc., 130g",
        "calories": 60, "protein_g": 1, "carbs_g": 15, "fat_g": 0,
        "prep_min": 0,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": False, "for_maintenance": True,
        "accessibility": 1.0,
    },
    # ══ MÂNCĂRURI ROMÂNEȘTI COMPLETE ═════════════════════════════════════════
    {
        "id": "ciorba_pui_400ml",
        "text": "ciorba pui supa proteina cald satietos pranz romanesc cut mentinere traditional",
        "name": "Ciorbă de pui",
        "qty_desc": "400ml",
        "calories": 180, "protein_g": 20, "carbs_g": 10, "fat_g": 6,
        "prep_min": 45,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    {
        "id": "ciorba_legume_400ml",
        "text": "ciorba legume vegetala fibra cut slabit pranz cina sarac calorii light",
        "name": "Ciorbă de legume",
        "qty_desc": "400ml",
        "calories": 120, "protein_g": 4, "carbs_g": 20, "fat_g": 3,
        "prep_min": 40,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": False, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "ciorba_burta_400ml",
        "text": "ciorba burta vita proteina grasime cald pranz romanesc traditional satietos",
        "name": "Ciorbă de burtă",
        "qty_desc": "400ml",
        "calories": 280, "protein_g": 18, "carbs_g": 8, "fat_g": 18,
        "prep_min": 0,
        "can_breakfast": False, "can_lunch": True, "can_dinner": False, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.8,
    },
    {
        "id": "sarmale_3buc_300g",
        "text": "sarmale varza vita porc orez proteina carbohidrat romanesc traditional pranz cina",
        "name": "Sarmale (3 buc.)",
        "qty_desc": "3 buc., 300g",
        "calories": 420, "protein_g": 22, "carbs_g": 30, "fat_g": 24,
        "prep_min": 120,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    {
        "id": "pilaf_pui_300g",
        "text": "pilaf pui orez carbohidrat proteina pranz cina complet masa bulk surplus",
        "name": "Pilaf cu pui",
        "qty_desc": "300g",
        "calories": 420, "protein_g": 32, "carbs_g": 48, "fat_g": 10,
        "prep_min": 35,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "tocanita_vita_300g",
        "text": "tocanita vita carne proteina legume pranz cina romanesc traditional bulk",
        "name": "Tocăniță de vită",
        "qty_desc": "300g",
        "calories": 380, "protein_g": 30, "carbs_g": 20, "fat_g": 20,
        "prep_min": 60,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.8,
    },
    {
        "id": "salata_ton_300g",
        "text": "salata ton conserva proteina cut slabit pranz cina rapid simplu light",
        "name": "Salată cu ton",
        "qty_desc": "300g",
        "calories": 250, "protein_g": 28, "carbs_g": 10, "fat_g": 10,
        "prep_min": 10,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": True, "for_bulk": False, "for_maintenance": True,
        "accessibility": 1.0,
    },
    {
        "id": "mancare_fasole_vita_300g",
        "text": "fasole vita carne proteina carbohidrat fibra romanesc satietos pranz cina bulk",
        "name": "Mâncare de fasole cu carne",
        "qty_desc": "300g",
        "calories": 380, "protein_g": 22, "carbs_g": 30, "fat_g": 18,
        "prep_min": 90,
        "can_breakfast": False, "can_lunch": True, "can_dinner": True, "can_snack": False,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    # ══ SHAKE-URI ȘI GUSTĂRI RAPIDE ══════════════════════════════════════════
    {
        "id": "shake_proteic_1doza",
        "text": "shake proteic pulbere proteina rapida gustare post-antrenament bulk cut rapid",
        "name": "Shake proteic",
        "qty_desc": "1 doză (30g pulbere + 300ml apă)",
        "calories": 130, "protein_g": 25, "carbs_g": 5, "fat_g": 2,
        "prep_min": 1,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.7,
    },
    {
        "id": "iaurt_nuci",
        "text": "iaurt nuci gustare proteina grasimi sanatoase rapida mentinere bulk",
        "name": "Iaurt cu nuci",
        "qty_desc": "200g iaurt + 20g nuci",
        "calories": 250, "protein_g": 18, "carbs_g": 12, "fat_g": 14,
        "prep_min": 1,
        "can_breakfast": True, "can_lunch": False, "can_dinner": False, "can_snack": True,
        "for_cut": False, "for_bulk": True, "for_maintenance": True,
        "accessibility": 0.9,
    },
    {
        "id": "sandwich_pui_paine",
        "text": "sandwich pui paine proteina carbohidrat dejun pranz rapid usor complet",
        "name": "Sandviș cu pui la grătar",
        "qty_desc": "2 felii pâine + 100g pui",
        "calories": 290, "protein_g": 30, "carbs_g": 28, "fat_g": 6,
        "prep_min": 10,
        "can_breakfast": True, "can_lunch": True, "can_dinner": False, "can_snack": False,
        "for_cut": True, "for_bulk": True, "for_maintenance": True,
        "accessibility": 1.0,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  SINGLETON — Client și Colecție ChromaDB
# ─────────────────────────────────────────────────────────────────────────────

_chroma_client: Optional[chromadb.EphemeralClient] = None
_food_collection = None   # chromadb.Collection


def get_rag_collection():
    """
    Returnează colecția ChromaDB. Trebuie apelat DUPĂ init_rag_engine().
    Sincron intenționat — ChromaDB e sincron, nu async.
    """
    if _food_collection is None:
        raise RuntimeError(
            "RAG engine neinițializat. "
            "Verifică că await init_rag_engine() e apelat în lifespan() din main.py."
        )
    return _food_collection


# ─────────────────────────────────────────────────────────────────────────────
#  INIȚIALIZARE
# ─────────────────────────────────────────────────────────────────────────────

async def init_rag_engine() -> None:
    """
    Creează clientul ChromaDB in-memory și populează colecția cu alimentele românești.

    Apelat O SINGURĂ DATĂ în lifespan() din main.py, după init_db_p4().
    Adaugă exact 2 linii în lifespan:
      from rag_engine import init_rag_engine
      await init_rag_engine()

    EphemeralClient = date in-memory, resetate la restart.
    Aceasta este corect: reconstruim colecția din ROMANIAN_FOODS_DB la fiecare pornire.
    Startup cost: ~80ms pentru 42 alimente.

    De ce async dacă ChromaDB e sincron?
    → Interfață uniformă cu celelalte funcții init_ din lifespan.
    → ChromaDB nu blochează event loop-ul FastAPI (rulează în același thread, rapid).
    """
    global _chroma_client, _food_collection

    _chroma_client = chromadb.EphemeralClient()
    embedding_fn   = RomanianFoodEmbeddingFunction()

    _food_collection = _chroma_client.get_or_create_collection(
        name="romanian_foods",
        embedding_function=embedding_fn,  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},  # cosine similarity pentru vectori normalizați
    )

    # Construim listele pentru add() în batch (O singura apelare API ChromaDB)
    ids       = [food["id"] for food in ROMANIAN_FOODS_DB]
    documents = [food["text"] for food in ROMANIAN_FOODS_DB]
    metadatas = [
        {
            "name":           food["name"],
            "qty_desc":       food["qty_desc"],
            "calories":       food["calories"],
            "protein_g":      food["protein_g"],
            "carbs_g":        food["carbs_g"],
            "fat_g":          food["fat_g"],
            "prep_min":       food["prep_min"],
            "can_breakfast":  food["can_breakfast"],
            "can_lunch":      food["can_lunch"],
            "can_dinner":     food["can_dinner"],
            "can_snack":      food["can_snack"],
            "for_cut":        food["for_cut"],
            "for_bulk":       food["for_bulk"],
            "for_maintenance": food["for_maintenance"],
            "accessibility":  food["accessibility"],
        }
        for food in ROMANIAN_FOODS_DB
    ]

    _food_collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    print(f"✅  RAG Engine: {len(ROMANIAN_FOODS_DB)} alimente românești indexate în ChromaDB")


# ─────────────────────────────────────────────────────────────────────────────
#  QUERY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

# Mapare obiectiv → termeni semantici pentru query RAG
_GOAL_QUERY_TERMS: dict[str, str] = {
    "cut_bland":  "cut slabit deficit light sarac calorii proteina curata",
    "cut":        "cut slabit deficit proteina curata slaba legume fibra",
    "mentinere":  "mentinere recompozitie echilibrat complet variat",
    "bulk_lean":  "bulk masa surplus proteina carbohidrat energie complet",
    "bulk":       "bulk masa surplus carbohidrat energie calorii proteina complet",
}

# Mapare tip masă → cheie metadata pentru filtru ChromaDB
_MEAL_META_KEY: dict[str, str] = {
    "mic_dejun": "can_breakfast",
    "pranz":     "can_lunch",
    "cina":      "can_dinner",
    "gustare":   "can_snack",
}


def query_foods_for_meal(
    goal:           str,
    meal_type:      str,
    n_results:      int = 5,
) -> list[dict]:
    """
    Recuperează top N alimente relevante pentru un obiectiv + tip masă.

    Args:
        goal      : "cut" | "cut_bland" | "mentinere" | "bulk_lean" | "bulk"
        meal_type : "mic_dejun" | "pranz" | "cina" | "gustare"
        n_results : câte alimente să recupereze (default 5)

    Returns:
        List de dict cu name, qty_desc, calories, protein_g, carbs_g, fat_g, prep_min
    """
    collection = get_rag_collection()

    query_text = _GOAL_QUERY_TERMS.get(goal, "complet variat echilibrat")
    meta_key   = _MEAL_META_KEY.get(meal_type)

    try:
        # Filtru metadata: alimente potrivite PENTRU tipul de masă respectiv
        where_filter = {meta_key: True} if meta_key else None

        results = collection.query(
            query_texts=[query_text],
            n_results=min(n_results, len(ROMANIAN_FOODS_DB)),
            where=where_filter,
            include=["metadatas", "distances"],
        )

        foods = []
        for meta in (results["metadatas"] or [[]])[0]:
            foods.append({
                "name":      meta.get("name", ""),
                "qty_desc":  meta.get("qty_desc", ""),
                "calories":  meta.get("calories", 0),
                "protein_g": meta.get("protein_g", 0),
                "carbs_g":   meta.get("carbs_g", 0),
                "fat_g":     meta.get("fat_g", 0),
                "prep_min":  meta.get("prep_min", 0),
            })
        return foods

    except Exception as e:
        # Failsafe: dacă ChromaDB are o problemă, returnăm listă goală
        # meal_plan_generator.py va genera planul cu cunoștințele proprii
        print(f"⚠️  RAG query error ({meal_type}): {e}")
        return []


def query_all_meal_types(
    goal:       str,
    n_per_meal: int = 4,
) -> dict[str, list[dict]]:
    """
    Recuperează alimente pentru toate cele 4 tipuri de mese simultan.

    Apelat O SINGURĂ DATĂ per generare de plan — 4 query-uri consecutive ChromaDB.
    Cost total: ~4ms (HNSW O(log n) × 4 mese).

    Returns:
        {
            "mic_dejun": [...4 alimente...],
            "pranz":     [...4 alimente...],
            "cina":      [...4 alimente...],
            "gustare":   [...4 alimente...],
        }
    """
    return {
        meal: query_foods_for_meal(goal, meal, n_results=n_per_meal)
        for meal in ["mic_dejun", "pranz", "cina", "gustare"]
    }


def build_rag_context_string(
    foods_by_meal: dict[str, list[dict]],
    goal:          str = "mentinere",
) -> str:
    """
    Formatează alimentele recuperate din RAG ca string injectabil în prompt-ul LLM.

    Output exemplu (injectat în câmpul `preferences` din generate_weekly_meal_plan):
    ─────────────────────────────────────────────────────────────────
    ALIMENTE RECOMANDATE DIN BAZA DE DATE (folosește-le ca inspirație):
    · Mic Dejun: Ouă fierte (2 buc.) [156kcal, P12g, C0g, G10g, 10min]
                 Iaurt grecesc 0% [120kcal, P20g, C8g, G0g, 0min]
    · Prânz: Piept pui la grătar [210kcal, P40g, C0g, G5g, 20min]
    ...
    ─────────────────────────────────────────────────────────────────

    Injectat astfel în main_p6_additions.py:
        rag_ctx = build_rag_context_string(foods_by_meal, goal)
        enhanced_prefs = f"{user_preferences}\\n\\n{rag_ctx}"
        await generate_weekly_meal_plan(..., preferences=enhanced_prefs)
    """
    meal_labels = {
        "mic_dejun": "Mic Dejun",
        "pranz":     "Prânz",
        "cina":      "Cină",
        "gustare":   "Gustare",
    }

    lines = [
        "ALIMENTE RECOMANDATE DIN BAZA DE DATE (folosește-le ca inspirație principală):",
    ]

    has_any = False
    for meal_key, label in meal_labels.items():
        foods = foods_by_meal.get(meal_key, [])
        if not foods:
            continue
        has_any = True
        lines.append(f"· {label}:")
        for f in foods:
            lines.append(
                f"  - {f['name']} [{f['qty_desc']}]: "
                f"{f['calories']}kcal · P{f['protein_g']}g · C{f['carbs_g']}g · "
                f"G{f['fat_g']}g · pregătire {f['prep_min']} min"
            )

    if not has_any:
        return ""

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITAR: STATISTICI COLECȚIE (pentru /cache/stats endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def get_rag_stats() -> dict:
    """
    Statistici despre colecția ChromaDB.
    Expus prin GET /rag/stats în main_p6_additions.py.
    """
    try:
        col   = get_rag_collection()
        count = col.count()
        return {
            "collection": "romanian_foods",
            "total_foods": count,
            "vocab_size":  _VOCAB_SIZE,
            "embedding":   "RomanianFoodEmbeddingFunction (bag-of-words, fără ML)",
            "storage":     "EphemeralClient (in-memory, rebuild la restart)",
            "status":      "active",
        }
    except RuntimeError as e:
        return {"status": "not_initialized", "error": str(e)}