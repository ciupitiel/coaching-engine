# =============================================================================
#  photo_food_analyzer.py — Motor AI Vision · Photo Food Log
#  Noian Lab · v7 — Gemini cu bază de date alimentară extinsă
# =============================================================================

import json
import re
import os
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

_MODEL_CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.0-flash",
]

_active_model: str | None = None

# ─────────────────────────────────────────────────────────────────────────────
#  PROMPT — bază de date extinsă
# ─────────────────────────────────────────────────────────────────────────────
_PROMPT = """Ești nutriționist expert cu 20 de ani experiență. Analizează CU ATENȚIE toate alimentele vizibile.

Returnează EXCLUSIV JSON valid, fără text înainte/după, fără ``` markdown:

{
  "detected": true,
  "meal_name": "Denumire concisă în română",
  "analysis_quality": "high",
  "foods": [
    {
      "name": "Nume exact aliment în română",
      "portion_estimate": "~200g",
      "calories": 250,
      "protein_g": 20,
      "carbs_g": 30,
      "fat_g": 8,
      "confidence": "high"
    }
  ],
  "total_calories": 250,
  "total_protein_g": 20,
  "total_carbs_g": 30,
  "total_fat_g": 8,
  "notes": "observații despre preparare sau estimare"
}

Dacă NU există aliment: {"detected": false, "meal_name": null, "foods": [], "total_calories": 0, "total_protein_g": 0, "total_carbs_g": 0, "total_fat_g": 0, "analysis_quality": "none", "notes": ""}

═══ REGULI CRITICE ═══
1. detected: true pentru ORICE aliment vizibil
2. Distinge carnea cu atenție:
   - Pui: culoare aurie-deschis, textură moale, os subțire
   - Porc: culoare mai închisă/roșiatică, grăsime vizibilă, os mai gros
   - Vită: roșu-maro închis, textură densă
   - Pește: alb/roz, textură sfărâmicioasă, piele vizibilă
3. Detectează TOATE toppingurile și condimentele vizibile
4. Analizează TOATE farfuriile/bolurile din imagine
5. total_calories = SUMA exactă din foods[]. Verifică aritmetic.
6. confidence: "high"=sigur 90%+ | "medium"=estimare | "low"=ghici

═══ REFERINȚĂ CALORII (per 100g, dacă nu e specificat altfel) ═══

▶ CARNE PASĂRE
piept pui fiert=165 P31 C0 G4 | piept pui la cuptor=195 P29 C0 G8
pulpă pui la cuptor=209 P26 C0 G12 | pui întreg fript=215 P25 C0 G13
aripioare pui prăjite=290 P27 C8 G17 | pui la grătar=165 P31 C0 G4
curcan piept=157 P30 C0 G3 | curcan pulpă=188 P27 C0 G9
rață piept=201 P19 C0 G13 | pui pané=230 P20 C10 G12

▶ CARNE PORC
friptură porc=242 P27 C0 G15 | cotlet porc la grătar=215 P25 C0 G12
ceafă porc la grătar=290 P22 C0 G22 | pulpă porc coaptă=265 P28 C0 G17
mușchi porc=182 P22 C0 G10 | coastă porc=292 P18 C0 G24
șnițel porc pané=250 P20 C12 G14 | tobă=280 P16 C2 G23
caltaboș=310 P14 C4 G27 | cârnați proaspeți=290 P15 C2 G25
cârnați afumați=320 P17 C1 G28 | salam de vară=340 P20 C2 G28
kaizer=290 P19 C1 G23 | șuncă presată=145 P22 C2 G6
bacon=541 P37 C1 G42 | jambon=110 P17 C2 G4

▶ CARNE VITĂ
mușchi vită la grătar=217 P26 C0 G12 | antricot vită=291 P26 C0 G20
carne tocată vită 20%fat=254 P17 C0 G20 | carne tocată vită 10%fat=175 P20 C0 G10
friptură vită=250 P28 C0 G15 | oasis vită=200 P23 C0 G12
hamburger vită pătrat=290 P25 C0 G21 | biftec=210 P28 C0 G11

▶ CARNE MIEL/OAIE
cotlet miel=290 P25 C0 G21 | pulpă miel=231 P28 C0 G13
drob miel=220 P18 C8 G14 | miel la grătar=260 P26 C0 G17

▶ PEȘTE & FRUCTE DE MARE
somon proaspăt=208 P20 C0 G13 | somon afumat=117 P18 C0 G5
ton conservă în apă=116 P26 C0 G1 | ton conservă în ulei=198 P25 C0 G10
macrou=205 P19 C0 G14 | crap fiert=127 P18 C0 G6
șalău la grătar=100 P21 C0 G2 | păstrăv la cuptor=148 P21 C0 G7
cod fiert=82 P18 C0 G1 | hering afumat=217 P21 C0 G15
creveți fierți=99 P21 C0 G1 | calmar=92 P16 C3 G1
midii=86 P12 C4 G2 | file pește pané=230 P15 C14 G12

▶ OUĂ & DERIVATE
ou întreg 60g=78 P6.3 C0.4 G5.3 | albuș ou=52 P11 C0.7 G0.2
gălbenuș ou=322 P16 C3.6 G27 | omletă 2 ouă=180 P13 C1 G14
ochiuri 2 ouă=185 P13 C0.5 G15 | scrambled eggs 2 ouă=200 P14 C2 G16
ouă fierte tari=155 P13 C1 G11

▶ LACTATE — IAURT & BRÂNZETURI
iaurt 0% simplu=59 P10 C4 G0 | iaurt 2% simplu=63 P5 C3.6 G3
iaurt grecesc 0%=57 P10 C3.6 G0.2 | iaurt grecesc 10%=133 P9 C3.6 G9
skyr=62 P11 C4 G0.2 | kefir 1.5%=41 P3.3 C4.7 G1.5
brânză telemea vaci=258 P16 C1 G21 | brânză telemea oi=310 P18 C2 G26
cașcaval=370 P25 C1.3 G29 | cașcaval afumat=385 P26 C1 G32
brânză brie=334 P21 C0.5 G28 | brânză camembert=300 P20 C0.5 G25
brânză mozzarella=280 P18 C2 G22 | mozzarella light=175 P18 C2 G11
brânză cheddar=403 P25 C1.3 G33 | brânză parmezan=431 P38 C4 G29
brânză gouda=356 P25 C2 G28 | brânză emmentaler=380 P29 C0.5 G30
brânză cottage 4%=98 P11 C3.4 G4 | brânză cottage 0%=72 P12 C3.4 G0.5
urdă=170 P14 C4 G11 | smântână 20%=191 P2.7 C3.4 G19
smântână 30%=292 P2 C3 G30 | frișcă lichidă=300 P2 C3 G31
lapte integral 3.5%=61 P3.2 C4.8 G3.3 | lapte 1.5%=47 P3.4 C5 G1.5
lapte degresat 0%=35 P3.4 C5 G0.2 | lapte vegetal soia=39 P3.3 C2.9 G2
lapte migdale=13 P0.5 C0.3 G1.1 | lapte ovăz=47 P1 C9 G1.5
unt=717 P0.9 C0.1 G81 | margarina=718 P0.2 C0.4 G80

▶ CEREALE & PÂINE
orez alb fiert=130 P2.4 C28 G0.3 | orez brun fiert=112 P2.6 C23 G0.9
orez basmati fiert=121 P2.5 C26 G0.2 | orez sălbatic fiert=101 P4 C21 G0.3
paste fierte=131 P4.8 C25 G1.1 | paste integrale fierte=124 P5.3 C23 G1
paste cu ou fierte=138 P5 C26 G1.8 | tăiței fieri=125 P4 C24 G1
pâine albă felie 30g=80 P2.7 C15 G1 | pâine integrală felie 30g=74 P3.9 C12 G1
pâine graham felie=68 P3 C13 G0.9 | franzelă simplă=265 P9 C49 G3
covrigi simpli=350 P12 C73 G2 | lipie=267 P8.5 C53 G2.5
tortilla de grâu 30g=90 P2.5 C15 G2.5 | pita 60g=165 P5.5 C33 G1
fulgi ovăz=389 P17 C66 G7 | ovăz fiert cu apă=71 P2.5 C12 G1.5
granola 30g=134 P3 C20 G5 | müsli=370 P10 C66 G7
cornflakes=357 P7.5 C84 G0.9 | cereale integrale=345 P10 C71 G4
griș fiert=55 P2 C11 G0.3 | mămăligă=84 P2 C18 G0.5
quinoa fiartă=120 P4.4 C22 G2 | bulgur fiert=83 P3 C18 G0.2
linte fiartă=116 P9 C20 G0.4 | năut fiert=164 P9 C27 G2.6
fasole roșie fiartă=127 P8.7 C23 G0.5 | fasole albă fiartă=139 P10 C25 G0.6
mazăre fiartă=84 P5.4 C14 G0.4 | soia fiartă=173 P17 C10 G9

▶ CARTOFI & TUBERCULI
cartofi fierți=87 P1.9 C20 G0.1 | cartofi copți=93 P2.5 C21 G0.1
cartofi prăjiți=312 P3.4 C41 G15 | cartofi pai fast food=320 P3.5 C43 G15
piure cartofi simplu=77 P1.8 C17 G0.1 | piure cartofi cu unt=110 P2 C16 G4.5
piure cartofi cu lapte și unt=90 P2 C14 G3.5 | cartofi la cuptor cu piele=93 P2.5 C21 G0.1
cartofi dulci fierți=76 P1.4 C18 G0.1 | cartofi dulci copți=90 P2 C21 G0.1
cartofi natur cu unt=140 P2 C20 G5

▶ LEGUME CRUDE & FIERTE
roșii=18 P0.9 C3.9 G0.2 | castraveți=16 P0.7 C3.6 G0.1
salată verde=15 P1.4 C2.2 G0.2 | salată iceberg=14 P0.9 C2.1 G0.1
morcovi cruzi=41 P0.9 C10 G0.2 | morcovi fierți=35 P0.8 C8 G0.2
ardei roșu=31 P1 C6 G0.3 | ardei verde=20 P0.9 C4.6 G0.2
ardei galben=27 P1 C6.3 G0.2 | ceapă=40 P1.1 C9.3 G0.1
ceapă roșie=42 P1 C10 G0.1 | usturoi=149 P6.4 C33 G0.5
broccoli=34 P2.8 C6.6 G0.4 | conopidă=25 P1.9 C5 G0.3
varză albă=25 P1.3 C5.8 G0.1 | varză roșie=31 P1.5 C7 G0.1
varză de Bruxelles=43 P3.4 C9 G0.3 | spanac proaspăt=23 P2.9 C3.6 G0.4
spanac gătit=23 P2.5 C3.8 G0.4 | sfeclă roșie fiartă=44 P1.7 C10 G0.2
dovlecel=17 P1.2 C3.1 G0.3 | dovleac copt=26 P1 C6.5 G0.1
vinete=25 P1 C5.9 G0.2 | vinete la grătar=35 P1.5 C8 G0.4
fasole verde fiartă=35 P2 C7 G0.4 | sparanghel=20 P2.2 C3.9 G0.1
ciuperci crude=22 P3.1 C3.3 G0.3 | ciuperci la grătar=35 P3.5 C4.5 G0.8
roșii cherry=18 P0.9 C3.9 G0.2 | anghinare=53 P2.9 C10 G0.2
porumb fiert=96 P3.4 C21 G1.5 | mazăre verde fiartă=84 P5 C14 G0.4

▶ FRUCTE
mere=52 P0.3 C14 G0.2 | pere=57 P0.4 C15 G0.1
banane=89 P1.1 C23 G0.3 | banane verzi=90 P1 C23 G0.3
portocale=47 P0.9 C12 G0.1 | mandarine=53 P0.8 C13 G0.3
grapefruit=42 P0.8 C11 G0.1 | lămâi=29 P1.1 C9 G0.3
kiwi=61 P1.1 C15 G0.5 | ananas=50 P0.5 C13 G0.1
mango=60 P0.8 C15 G0.4 | papaya=43 P0.5 C11 G0.3
avocado=160 P2 C9 G15 | pepene verde=30 P0.6 C8 G0.2
pepene galben=34 P0.8 C8 G0.2 | struguri albi=69 P0.6 C18 G0.2
struguri negri=69 P0.7 C18 G0.2 | căpșuni=32 P0.7 C7.7 G0.3
afine=57 P0.7 C14 G0.3 | zmeură=32 P0.7 C7.3 G0.4
mure=43 P1.4 C10 G0.5 | cireșe=63 P1.1 C16 G0.2
vișine=50 P1 C12 G0.3 | caise=48 P1.4 C11 G0.4
piersici=39 P0.9 C10 G0.3 | nectarine=44 P1.1 C11 G0.3
prune=46 P0.7 C11 G0.3 | smochine proaspete=74 P0.8 C19 G0.3
smochine uscate=249 P3.3 C63 G0.9 | curmale=282 P2.5 C75 G0.4
stafide=299 P3.1 C79 G0.5 | merisoare uscate=308 P0.1 C82 G1.4
cocos ras=354 P3.3 C15 G33

▶ NUCI & SEMINȚE
migdale crude=579 P21 C22 G50 | migdale prăjite=598 P22 C22 G52
nuci crude=654 P15 C14 G65 | nuci prăjite=642 P15 C14 G64
nuci caju crude=553 P18 C30 G44 | nuci caju prăjite=580 P17 C32 G46
alune de pădure=628 P15 C17 G61 | arahide crude=567 P26 C16 G49
arahide prăjite=599 P29 C21 G50 | pistachios=562 P20 C28 G45
macadamia=718 P8 C14 G76 | pecan=691 P9 C14 G72
semințe floarea soarelui=584 P21 C20 G51 | semințe dovleac=559 P30 C11 G49
semințe chia=486 P17 C42 G31 | semințe in=534 P18 C29 G42
semințe susan=573 P18 C23 G50 | semințe cânepă=553 P32 C9 G49
unt arahide natural=588 P25 C20 G50 | unt migdale=614 P21 C19 G56

▶ ULEIURI & GRĂSIMI
ulei măsline=884 P0 C0 G100 | ulei floarea soarelui=884 P0 C0 G100
ulei cocos=892 P0 C0 G100 | ulei avocado=884 P0 C0 G100
ulei rapiță=884 P0 C0 G100 | ghee=900 P0.3 C0 G99

▶ CONDIMENTE & TOPPINGURI (per porție uzuală)
miere 15g=46 P0 C12 G0 | miere 1 lingurită 7g=21 P0 C5.7 G0
scorțișoară 3g=8 P0.2 C2.5 G0.1 | zahăr 10g=39 P0 C10 G0
zahăr brun 10g=38 P0 C9.8 G0 | sirop arțar 15ml=52 P0 C13 G0
cacao pudră 10g=23 P2.2 C5.8 G1.4 | ciocolată neagră 70% 10g=60 P1 C4.5 G4.2
ciocolată lapte 10g=54 P0.8 C5.8 G3.2 | chipsuri ciocolată 10g=55 P0.7 C5.5 G3.5
gem 20g=52 P0.1 C13 G0 | nutella 15g=82 P1.1 C8.5 G4.8
ketchup 15g=16 P0.2 C3.8 G0 | muștar 10g=10 P0.8 C1 G0.5
maioneză 15g=100 P0.3 C0.4 G11 | maioneză light 15g=48 P0.5 C1.8 G4.5
sos de soia 15ml=13 P1.3 C1.2 G0.1 | sos tabasco 5ml=3 P0.1 C0.5 G0
smântână acidă 15g=29 P0.4 C0.5 G2.9 | tahini 15g=88 P2.6 C3 G8

▶ DESERTURI & DULCIURI
ciocolată neagră 85%=599 P8 C19 G51 | ciocolată cu lapte=535 P8 C57 G30
ciocolată albă=539 P6 C59 G32 | bomboane gumă 30g=100 P2 C23 G0
prăjitură cu ciocolată=380 P5 C52 G18 | tiramisu=288 P5.5 C28 G17
ecler ciocolată=280 P5 C33 G14 | cremă caramel=130 P4 C17 G5
clătite cu gem 2 buc=220 P6 C38 G6 | pancakes 2 buc=150 P5 C20 G6
wafe=456 P6 C62 G21 | biscuiți digestivi 30g=140 P2 C21 G5
biscuiți Oreo 3 buc=160 P1.5 C25 G7 | cozonac 50g=185 P4 C28 G7
chec 50g=190 P3 C26 G9 | tort frișcă felie=350 P4 C45 G17
înghețată vanilie 100ml=200 P3 C23 G11 | înghețată ciocolată=216 P3.8 C26 G12
sorbet mango=97 P0.5 C24 G0.3 | baclava 50g=320 P4 C40 G16
papanași 1 buc=280 P8 C35 G12 | gogoși 1 buc simplu=270 P4 C35 G13

▶ SNACKS & FAST FOOD
chipsuri cartofi 30g=160 P1.8 C15 G10 | chipsuri tortilla 30g=140 P2 C19 G6
popcorn simplu 30g=110 P3.5 C22 G1.5 | popcorn unt 30g=150 P2.5 C18 G7
covrigei 30g=115 P3 C23 G1.5 | baton proteic 60g=220 P20 C18 G8
pizza margherita felie 120g=250 P11 C30 G10 | pizza pepperoni felie=300 P13 C29 G15
hamburger simplu=295 P17 C30 G12 | hamburger dublu=450 P26 C37 G22
hotdog=290 P11 C24 G16 | șaormă medie=420 P25 C38 G18
kebab pita=420 P28 C38 G15 | sandwich pui=310 P24 C28 G10
shaorma pui mic=380 P22 C34 G16 | wrap pui=350 P23 C32 G12

▶ PREPARATE ROMÂNEȘTI
ciorbă de burtă 300ml=190 P12 C8 G12 | supă de pui cu tăiței 300ml=130 P12 C14 G4
ciorbă de perișoare 300ml=150 P11 C12 G6 | supă cremă de roșii 300ml=110 P2 C14 G5
borș de pui 300ml=90 P8 C8 G3 | mâncare fasole bătută 200g=280 P14 C38 G8
mâncare varză călită 200g=120 P4 C12 G7 | mâncare de cartofi 200g=180 P5 C28 G6
mici 1 buc 60g=180 P10 C5 G13 | cârnăciori 1 buc 50g=160 P8 C2 G14
tochituri 200g=380 P25 C5 G29 | sarmale 1 buc 120g=200 P12 C18 G9
ardei umplut 1 buc 180g=220 P14 C20 G9 | moussaka 200g=260 P16 C18 G14
salată de boeuf 100g=180 P8 C12 G12 | salată orientală 100g=95 P2 C9 G6
drob miel 100g=220 P18 C8 G14 | piftie 100g=120 P20 C2 G4
jumări 50g=420 P12 C0 G42 | ouă cu roșii 2 ouă=220 P14 C6 G16

▶ BĂUTURI (per 100ml)
apă=0 | apă cu lămâie=4 P0 C1 G0
suc portocale proaspăt=45 P0.7 C10 G0.2 | suc mere proaspăt=46 P0.1 C11 G0.1
Cola 250ml=105 P0 C27 G0 | Cola Zero 250ml=3 P0 C0.3 G0
suc lămâie limonadă 250ml=100 P0.3 C26 G0 | limonadă fără zahăr=5 P0 C1.2 G0
lapte ciocolată 200ml=130 P6 C18 G4 | smoothie fructe 200ml=120 P1.5 C29 G0.5
cafea espresso=5 P0.1 C0.7 G0.2 | cafea cu lapte 200ml=60 P3.2 C5 G2.8
cafea cu zahăr și lapte 200ml=100 P2 C15 G3 | ceai simplu=2 P0 C0.4 G0
vin roșu 150ml=125 P0.1 C3.8 G0 | vin alb 150ml=121 P0.1 C3.8 G0
bere 330ml=140 P1.7 C11 G0 | bere fără alcool 330ml=66 P0.6 C15 G0

▶ ALTELE
hummus 100g=166 P8 C14 G10 | guacamole 100g=160 P2 C8.5 G15
supe instant 1 plic=310 P7 C60 G5 | ramen instant=440 P10 C65 G16
tofu ferm 100g=76 P8 C2 G4 | tempeh 100g=193 P19 C9 G11
seitan 100g=370 P75 C14 G2 | edamame fiert 100g=122 P11 C10 G5

IMPORTANT: Dacă un aliment nu e în tabel, folosește cunoștințele tale nutriționale pentru a estima corect."""

# ─────────────────────────────────────────────────────────────────────────────
#  UTILITARE
# ─────────────────────────────────────────────────────────────────────────────

def _strip_data_url(image_data: str) -> str:
    if "," in image_data:
        return image_data.split(",", 1)[1]
    return image_data

def _detect_mime(image_data: str) -> str:
    prefix = image_data[:40]
    if "image/png"  in prefix: return "image/png"
    if "image/webp" in prefix: return "image/webp"
    if "image/gif"  in prefix: return "image/gif"
    return "image/jpeg"

def _parse_json(raw: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None

def _not_detected(reason: str = "") -> dict:
    return {
        "detected": False, "meal_name": None, "foods": [],
        "total_calories": 0, "total_protein_g": 0,
        "total_carbs_g": 0, "total_fat_g": 0,
        "analysis_quality": "none",
        "notes": reason or "Niciun aliment detectat.",
    }

def _validate(data: dict) -> dict:
    defaults = {
        "detected": False, "meal_name": None, "foods": [],
        "total_calories": 0, "total_protein_g": 0,
        "total_carbs_g": 0, "total_fat_g": 0,
        "analysis_quality": "medium", "notes": "",
    }
    for k, v in defaults.items():
        if k not in data:
            data[k] = v

    fixed = []
    for item in data.get("foods", []):
        try:
            cal = max(0, round(float(item.get("calories", 0) or 0)))
            if cal == 0:
                continue
            fixed.append({
                "name":             str(item.get("name", "Aliment")).strip(),
                "portion_estimate": str(item.get("portion_estimate", "—")).strip(),
                "calories":         cal,
                "protein_g":        max(0, round(float(item.get("protein_g", 0) or 0))),
                "carbs_g":          max(0, round(float(item.get("carbs_g",   0) or 0))),
                "fat_g":            max(0, round(float(item.get("fat_g",     0) or 0))),
                "confidence":       item.get("confidence", "medium"),
            })
        except (ValueError, TypeError):
            continue

    data["foods"] = fixed
    if fixed:
        data["total_calories"]  = sum(f["calories"]  for f in fixed)
        data["total_protein_g"] = sum(f["protein_g"] for f in fixed)
        data["total_carbs_g"]   = sum(f["carbs_g"]   for f in fixed)
        data["total_fat_g"]     = sum(f["fat_g"]     for f in fixed)
        data["detected"] = True
    else:
        data["detected"] = False
    return data

# ─────────────────────────────────────────────────────────────────────────────
#  AUTO-DETECT MODEL
# ─────────────────────────────────────────────────────────────────────────────

async def _find_working_model() -> str | None:
    global _active_model
    if _active_model:
        return _active_model
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY lipsă!")
        return None

    genai.configure(api_key=GEMINI_API_KEY)

    try:
        available_names = {
            m.name.replace("models/", "")
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        }
    except Exception as e:
        logger.warning("Nu s-a putut lista modelele: %s", e)
        available_names = set()

    to_test = [c for c in _MODEL_CANDIDATES if not available_names or c in available_names]
    if not to_test:
        to_test = _MODEL_CANDIDATES

    for candidate in to_test:
        try:
            model = genai.GenerativeModel(candidate)
            resp  = await model.generate_content_async(
                "Răspunde cu exact un cuvânt: OK",
                generation_config=genai.GenerationConfig(max_output_tokens=10),
            )
            if resp.text:
                logger.info("Gemini model activ: %s", candidate)
                _active_model = candidate
                return candidate
        except Exception as e:
            logger.debug("Model %s indisponibil: %s", candidate, str(e)[:100])
            continue

    return None

# ─────────────────────────────────────────────────────────────────────────────
#  FUNCȚIE PRINCIPALĂ
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_food_photo(groq_client, image_data: str) -> dict:
    if not GEMINI_API_KEY:
        return _not_detected("GEMINI_API_KEY lipsă din Render Environment Variables.")

    mime_type = _detect_mime(image_data)
    b64_clean = _strip_data_url(image_data)

    logger.info("Photo vision start: mime=%s b64_len=%d", mime_type, len(b64_clean))

    model_name = await _find_working_model()
    if not model_name:
        return _not_detected("Niciun model Gemini disponibil. Verifică cheia API.")

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(model_name)

        response = await model.generate_content_async(
            [_PROMPT, {"inline_data": {"data": b64_clean, "mime_type": mime_type}}],
            generation_config=genai.GenerationConfig(
                temperature=0.05,
                max_output_tokens=2000,
            ),
        )

        raw = response.text or ""
        logger.info("Photo vision raw (%s): chars=%d | preview: %s",
                    model_name, len(raw), raw[:200].replace("\n", " "))

        if not raw.strip():
            global _active_model
            _active_model = None
            return _not_detected("Modelul nu a returnat răspuns. Încearcă din nou.")

        parsed = _parse_json(raw)
        if not parsed:
            return _not_detected("Nu s-a putut procesa răspunsul AI. Încearcă o fotografie mai clară.")

        result = _validate(parsed)
        logger.info("Photo vision OK (%s): detected=%s foods=%d kcal=%s",
                    model_name, result.get("detected"),
                    len(result.get("foods", [])), result.get("total_calories", 0))
        return result

    except Exception as exc:
        err_str = str(exc)
        logger.error("Photo vision EXCEPTION (%s): %s: %s",
                     model_name, type(exc).__name__, err_str[:300])
        if "404" in err_str or "not available" in err_str.lower():
            _active_model = None
        return _not_detected("Eroare temporară. Încearcă din nou.")

# ─────────────────────────────────────────────────────────────────────────────
#  DEBUG
# ─────────────────────────────────────────────────────────────────────────────

async def debug_vision_models(groq_client) -> dict:
    global _active_model
    _active_model = None

    if not GEMINI_API_KEY:
        return {"working_models": [], "recommended": None, "status": "FAIL",
                "error": "GEMINI_API_KEY lipsă"}

    genai.configure(api_key=GEMINI_API_KEY)
    try:
        all_models = sorted([
            m.name.replace("models/", "")
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ])
    except Exception as e:
        all_models = [f"Eroare: {e}"]

    model_name = await _find_working_model()
    if model_name:
        return {"working_models": [model_name], "recommended": model_name,
                "status": "OK", "provider": "Google Gemini", "all_available": all_models}
    return {"working_models": [], "recommended": None, "status": "FAIL",
            "all_available": all_models, "tried": _MODEL_CANDIDATES}