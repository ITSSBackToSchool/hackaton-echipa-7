import os
import requests
from dotenv import load_dotenv

# ------------------------------------------------------------
# 🔹 Încărcare cheie API din fișierul .env
# ------------------------------------------------------------
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
ENV_ENDPOINT = os.getenv("GEMINI_ENDPOINT")  # ex.: v1 / v1beta
ENV_MODEL = os.getenv("GEMINI_MODEL")        # ex.: gemini-2.5-flash
CACHE_PATH = os.path.join(os.path.dirname(__file__), ".gemini_model_cache.json")

if not API_KEY:
    raise Exception("❌ Nu s-a găsit cheia GOOGLE_API_KEY în fișierul .env")

# --- Global system instruction (persona) ---
BASE_SYSTEM_INSTRUCTION = """
Ești Chef Asistent (ChefGPT), un agent culinar prietenos și practic.

Reguli generale:
- Răspunde în română, clar și concis; preferă bullet points și pași numerotați.
- Întreabă doar când e necesar (alergii, timp, câți oameni mănâncă).
- Sugerează 1–3 rețete relevante, nu te opri niciodată doar la listarea ingredientelor.
- Include timp total, dificultate, pași (1..N), și opțional listă scurtă de cumpărături/înlocuiri.

Scenarii:
- Cu inventar (din frigider): folosește DOAR ingredientele date ca bază; marchează clar ce lipsește și oferă alternative.
- Fără inventar (cerere tip „cum fac X?” sau „idee de cină rapidă”): răspunde direct cu rețeta/ideile cerute, fără a cere inventarul.
"""

# ------------------------------------------------------------
# 🔹 Funcție care testează automat ce endpoint și modele merg
# ------------------------------------------------------------
def detect_working_model():
    """
    Selectează automat modelul cu cea mai mică latență dintre candidați,
    pe oricare dintre endpoint-urile suportate. Măsoară timpul efectiv al
    unui request minimal și alege cel mai rapid care răspunde 200.
    """
    import time
    endpoints = ["v1beta", "v1"]  # v1beta e adesea mai liber
    # Preferăm modelele FLASH (mai rapide, mai ieftine). Scoatem PRO din autodetect ca să evităm 404/permisiuni.
    candidates = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]

    test_payload = {"contents": [{"parts": [{"text": "ping"}]}]}
    headers = {"Content-Type": "application/json"}

    best = None  # (elapsed, endpoint, model)
    for endpoint in endpoints:
        for model in candidates:
            try:
                url = f"https://generativelanguage.googleapis.com/{endpoint}/models/{model}:generateContent?key={API_KEY}"
                t0 = time.perf_counter()
                resp = requests.post(url, headers=headers, json=test_payload, timeout=8)
                elapsed = time.perf_counter() - t0
                if resp.status_code == 200:
                    if best is None or elapsed < best[0]:
                        best = (elapsed, endpoint, model)
                else:
                    print(f"↪︎ {endpoint}/{model} status {resp.status_code}")
            except Exception as e:
                print(f"⚠️ Eroare la testarea {endpoint}/{model}: {e}")

    if best:
        print(f"✅ Aleg cel mai rapid: '{best[2]}' pe '{best[1]}' (≈{best[0]:.2f}s)")
        return best[1], best[2]
    raise Exception("❌ Nu am putut inițializa niciun model Gemini. Verifică GOOGLE_API_KEY și cotele.")

# ------------------------------------------------------------
# 🔹 Selectează automat endpointul și modelul compatibil
#    Permite override din .env (GEMINI_ENDPOINT, GEMINI_MODEL)
# ------------------------------------------------------------
def _load_cached_model():
    try:
        import json
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                ep, md = data.get('endpoint'), data.get('model')
                if ep and md:
                    print(f"ℹ️ Folosesc modelul din cache: '{md}' pe '{ep}'")
                    return ep, md
    except Exception:
        pass
    return None, None

def _save_cached_model(endpoint: str, model: str):
    try:
        import json
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump({"endpoint": endpoint, "model": model}, f)
    except Exception:
        pass

def _select_endpoint_and_model():
    # 1) .env override
    if ENV_ENDPOINT and ENV_MODEL:
        print(f"✅ Folosesc modelul (din .env) '{ENV_MODEL}' pe endpoint '{ENV_ENDPOINT}'")
        return ENV_ENDPOINT, ENV_MODEL
    # 2) cache de la ultima rulare reușită
    ep, md = _load_cached_model()
    if ep and md:
        return ep, md
    # 3) autodetect
    return detect_working_model()

ENDPOINT, MODEL = _select_endpoint_and_model()

# ------------------------------------------------------------
# 🔹 Helper comun: trimite prompt către Gemini cu retry + fallback modele
# ------------------------------------------------------------
def _generate_with_retries(prompt: str, timeout_s: int = 30) -> str:
    import time

    # Ordinea candidaților: modelul curent, apoi alte variante FLASH
    candidates = [m for m in [MODEL, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"] if m]
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    for model_name in candidates:
        url = f"https://generativelanguage.googleapis.com/{ENDPOINT}/models/{model_name}:generateContent?key={API_KEY}"
        for attempt in range(1, 3):  # două încercări/model
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
            if resp.status_code == 200:
                data = resp.json()
                try:
                    # cachează modelul reușit pentru a fi preferat la startup
                    _save_cached_model(ENDPOINT, model_name)
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except KeyError:
                    raise Exception(f"⚠️ Format neașteptat al răspunsului API: {data}")
            if resp.status_code in (429, 503):
                # backoff scurt și reîncercare
                time.sleep(2 * attempt)
                continue
            # alte erori – propagă imediat
            raise Exception(f"API error {_current_func_name()}: {resp.status_code} - {resp.text}")
        # trecem la următorul model
    raise Exception("Toate modelele sunt ocupate momentan (429/503). Încearcă din nou mai târziu.")

def _current_func_name() -> str:
    # mic utilitar pentru mesaje de eroare
    import inspect
    for frame in inspect.stack():
        if frame.function.startswith('generate_'):
            return frame.function
    return 'generate'

# ------------------------------------------------------------
# 🔹 Funcția principală de generare rețete (moștenită)
# ------------------------------------------------------------
def generate_recipes(ingredients):
    """
    Generează rețete creative folosind Gemini 2.5 / Flash.
    Dacă modelul principal e supraîncărcat (503), reîncearcă automat.
    """
    prompt = f"""
    Ești ChefGPT, un asistent culinar inteligent.
    Având următoarele ingrediente: {', '.join(ingredients)},
    creează 3 rețete creative care să includă:
    - Titlu și descriere
    - Lista completă de ingrediente
    - Pași de preparare numerotați
    - Timp de preparare și calorii
    - Sugestii de servire
    Răspunde în limba română, frumos formatat în Markdown.
    """

    url = f"https://generativelanguage.googleapis.com/{ENDPOINT}/models/{MODEL}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}

    # 🔁 Reîncercare automată de 3 ori, cu fallback la model mai mic
    attempts = 0
    max_attempts = 3
    fallback_model = "gemini-2.0-flash"

    while attempts < max_attempts:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        data = response.json()

        # ✅ Succes
        if response.status_code == 200:
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except KeyError:
                raise Exception(f"⚠️ Format neașteptat al răspunsului API: {data}")

        # ⚠️ Model supraîncărcat
        elif response.status_code == 503:
            attempts += 1
            print(f"⚠️ Modelul {MODEL} este supraîncărcat ({attempts}/{max_attempts})... reîncerc în 5 secunde.")
            import time
            time.sleep(5)
            if attempts == max_attempts:
                print("⏳ Trec pe modelul de rezervă:", fallback_model)
                url = f"https://generativelanguage.googleapis.com/{ENDPOINT}/models/{fallback_model}:generateContent?key={API_KEY}"
                continue

        # ❌ Altă eroare API
        else:
            raise Exception(f"API error generate_recipes: {response.status_code} - {response.text}")

    raise Exception("❌ Toate încercările au eșuat. Încearcă mai târziu.")

# ------------------------------------------------------------
# 🔹 Funcție nouă: idei adaptate cu frigider + rețete din DB + masa vizată
# ------------------------------------------------------------
def generate_meal_suggestions(ingredients, user_recipes=None, meal_hint=None):
    """
    Generează sugestii/meniuri folosind atât ingredientele din frigider, cât și rețetele utilizatorului (DB),
    având opțional o masă vizată (mic dejun / prânz / cină / snack).
    Prompt extins pentru a obține rezultate cât mai generative și utile.
    """
    user_recipes = user_recipes or []
    short_recipes = []
    for r in user_recipes:
        name = r.get('name', 'Rețetă')
        ingreds = r.get('ingredients', [])
        short_recipes.append(f"- {name}: {', '.join([str(x) for x in ingreds])}")

    meal_line = f"Masa vizată: {meal_hint}." if meal_hint else "(masa la alegere)"
    prompt = f"""
    {BASE_SYSTEM_INSTRUCTION}

    Context:
    - Ingrediente disponibile (frigider): {', '.join(ingredients) if ingredients else '—'}
    - Rețete ale utilizatorului (din baza de date):
      {chr(10).join(short_recipes) if short_recipes else '- (niciuna)'}
    - {meal_line}

    Cerințe pentru răspuns (Markdown, concis, executabil):
    1) Propune 1–3 rețete FEZABILE pe baza inventarului, nu te opri la listă de ingrediente.
    2) Pentru fiecare rețetă oferă:
       - Titlu
       - Timp total | Dificultate
       - Ingrediente folosite din frigider
       - Ingrediente lipsă/opționale (cu înlocuiri posibile)
       - Pași 1..N clari (max 6)
    3) Dacă vezi potriviri cu rețetele utilizatorului, menționează „Compatibil cu rețeta ta: <nume>”.
    4) Încheie întrebând: „Alege o rețetă (1–3) ca să-ți dau cantitățile exacte și pașii detaliați.”
    """

    return _generate_with_retries(prompt)

# ------------------------------------------------------------
# 🔹 Fără inventar: rețete/idei creative direct din întrebare
# ------------------------------------------------------------
def generate_creative_recipes(user_query: str, k: int = 2):
    """
    Generează idei/retete plecând DOAR de la cererea utilizatorului, fără a apela inventarul.
    """
    prompt = f"""
    {BASE_SYSTEM_INSTRUCTION}

    Cerere utilizator: "{user_query}"

    Oferă {k} rețete/idei relevante. Pentru fiecare:
    - Titlu
    - Timp total | Dificultate
    - Ingrediente
    - Pași 1..N (clari, max 7)
    - Variații/înlocuiri dacă e util
    """

    return _generate_with_retries(prompt)

# ------------------------------------------------------------
# 🔹 Chat-only generic text (Gemini prompt minimalist)
# ------------------------------------------------------------
def generate_chat_reply(message):
    """
    Companion chat: răspunde liber la orice subiect. Dacă utilizatorul aduce mâncarea în discuție,
    oferă idei, dar nu forța subiectul. Ton cald, empatic, scurt, cu eventuală întrebare de follow-up.
    """
    prompt = f"""
    {BASE_SYSTEM_INSTRUCTION}

    Conversație liberă. Răspunde la mesajul de mai jos ca un companion AI:

    „{message}”

    Stil: răspuns scurt-mediu, în română, o întrebare de follow-up când are sens. Nu forța subiectul culinar.
    """
    return _generate_with_retries(prompt)


# ------------------------------------------------------------
# 🔹 Test local (doar dacă rulezi acest fișier direct)
# ------------------------------------------------------------
if __name__ == "__main__":
    print("🍳 Testare ChefGPT...\n")
    rezultat = generate_recipes(["cartofi", "ouă", "brânză"])
    print(rezultat)
