import datetime
from typing import List, Dict, Any
from recipe_ai import generate_meal_suggestions, generate_chat_reply, generate_creative_recipes

CULINARY_KEYWORDS = [
    'reteta','rețetă','rețete','ingrediente','fridge','frigider', 
    'ce pot gati','ce pot găti','ce gătesc','mic dejun','cina','cină','prânz','pranz',
    'gatit','meniu','micul dejun','cina','meniu','propuneri','sugestii','idee de cina','idee de pranz'
]

# -------------------- TOOLS --------------------
class FridgeTool:
    def __init__(self, items: List[Dict[str, Any]]):
        self.items = items or []

    def list_items(self) -> str:
        if not self.items:
            return "Frigiderul este gol."
        return ', '.join([f"{i['name']} ({i['quantity']} {i['unit']})" for i in self.items])

    def how_many(self, name: str) -> str:
        name_l = (name or '').lower()
        found = next((x for x in self.items if name_l in x['name'].lower()), None)
        if found:
            return f"Ai {found['quantity']} {found['unit']} de {found['name']}."
        return f"Nu am găsit '{name}' în frigiderul tău."

    def names(self) -> List[str]:
        return [i['name'] for i in self.items]


class RecipesTool:
    def __init__(self, recipes_db: List[Dict[str, Any]]):
        self.recipes = recipes_db or []

    def list_all_names(self, limit: int = 12) -> str:
        if not self.recipes:
            return "Nu ai rețete salvate încă. Spune-mi ce masă dorești și îți propun idei generale."
        names = [r.get('name', 'Rețetă') for r in self.recipes][:limit]
        extra = '' if len(self.recipes) <= limit else f" (+{len(self.recipes)-limit} rețete)"
        return "Rețetele tale: " + ', '.join(names) + extra


# -------------------- AGENT --------------------
class ChefAgent:
    def __init__(self, recipes_db: List[Dict[str, Any]] = None):
        self.recipes_tool = RecipesTool(recipes_db or [])

    def get_reply(self, message: str, fridge_items: List[Dict[str, Any]], time_of_day: int = None) -> str:
        msg = (message or '').lower().strip()
        time_of_day = time_of_day if time_of_day is not None else datetime.datetime.now().hour
        meal_hint = self._infer_meal(time_of_day, msg)

        fridge = FridgeTool(fridge_items)
        # Intent: counts ("cate X am")
        import re
        how_many_match = re.search(r"cate ([a-zăîâșț]+) am", msg)
        if how_many_match:
            return fridge.how_many(how_many_match.group(1))

        # If user asks to cook with what's in fridge, prioritize generation
        has_fridge_hint = ('cu ce am' in msg) or ('din frigider' in msg) or ('frigider' in msg)
        cook_keywords = ['reteta','rețetă','gati','găti','gatesc','gătesc','pot face','fa-mi','fă-mi','pregateste','pregătește']
        meal_keywords = ['mic dejun','breakfast','pranz','prânz','cina','cină','masa','pranzul','cina']
        if has_fridge_hint and (any(k in msg for k in cook_keywords) or any(k in msg for k in meal_keywords)):
            try:
                return generate_meal_suggestions(fridge.names(), self.recipes_tool.recipes, meal_hint)
            except Exception as e:
                return f"A apărut o problemă la generarea rețetelor cu Gemini. Detalii: {e}"

        # Creative/specific recipe queries (do NOT use inventory)
        recipe_triggers = [
            'cum fac', 'reteta ', 'rețeta ', 'retete', 'rețete', 'o reteta cu', 'o rețetă cu',
            'idee de cina', 'idee de pranz', 'idee de cină', 'idee de prânz',
            'vreau reteta', 'vreau o reteta', 'vreau doua retete', 'vreau două rețete', 'doua retete', 'două rețete', 'retete de', 'rețete de', 'reteta de', 'rețeta de'
        ]
        if any(k in msg for k in recipe_triggers):
            try:
                return generate_creative_recipes(message, k=2)
            except Exception as e:
                return f"Nu am putut genera răspunsul cu Gemini acum. Detalii: {e}"

        # Intent: explicit list of all recipes
        if any(k in msg for k in ['toate rețetele', 'toate retetele', 'lista rețete', 'lista retete', 'arată rețetele', 'arata retetele']):
            return self.recipes_tool.list_all_names()

        # Intent: list fridge
        if any(k in msg for k in ['ce am in frigider', 'ce am în frigider', 'lista frigider', 'ce am in frigider?']):
            return "Iată ce ai: " + fridge.list_items()

        # Conversational agent for NON-culinary topics
        if not any(k in msg for k in CULINARY_KEYWORDS):
            try:
                return generate_chat_reply(message)
            except Exception:
                return "Bună! Spune-mi orice dorești, discutăm!"

        # Culinary/gastronomic requests
        try:
            return generate_meal_suggestions(fridge.names(), self.recipes_tool.recipes, meal_hint)
        except Exception as e:
            return f"Serverul AI este ocupat sau a apărut o eroare. Detalii: {e}"

    def _infer_meal(self, hour: int, msg: str) -> str:
        if any(k in msg for k in ['mic dejun','breakfast','diminea']):
            return 'mic dejun'
        if any(k in msg for k in ['pranz','prânz','lunch']):
            return 'prânz'
        if any(k in msg for k in ['cina','cină','dinner','seara']):
            return 'cină'
        if 5 <= hour < 11:
            return 'mic dejun'
        if 11 <= hour < 16:
            return 'prânz'
        if 16 <= hour <= 23:
            return 'cină'
        return ''

    def _algorithmic_suggestions(self, meal_hint: str) -> str:
        suggestions = []
        if meal_hint == 'mic dejun' or not meal_hint:
            suggestions += ['Omletă/omletă vegană cu legume', 'Iaurt cu fulgi de ovăz și fructe', 'Toast cu avocado sau brânză']
        if meal_hint in ('prânz', ''):
            suggestions += ['Paste rapide cu sos de roșii și usturoi', 'Orez cu legume la tigaie', 'Supă cremă de legume']
        if meal_hint in ('cină', ''):
            suggestions += ['Salată consistentă cu proteină la alegere', 'Tocăniță rapidă de legume', 'Cartofi la cuptor cu ierburi']
        text = "Iată câteva idei rapide: \n" + '\n'.join([f"• {s}" for s in suggestions[:5]])
        return text
