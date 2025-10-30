from flask import Flask, render_template, request, redirect, url_for, flash, session
from fridge_vision import detect_ingredients
from recipe_ai import generate_recipes
from voice_assistant import speak
import os
from datetime import datetime
import sqlite3
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
import json
from agent import ChefAgent

app = Flask(__name__)

UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['SECRET_KEY'] = 'chef-gpt-secret'  # inlocuieste pentru productie
DB = 'chef_gpt.db'
bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)

# --- UTIL LOCAL USER MODEL ---
class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

    @staticmethod
    def get(user_id):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute('SELECT id, username, password FROM users WHERE id=?', (user_id,))
        row = cur.fetchone()
        conn.close()
        return User(*row) if row else None

    @staticmethod
    def find_by_username(username):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute('SELECT id, username, password FROM users WHERE username=?', (username,))
        row = cur.fetchone()
        conn.close()
        return User(*row) if row else None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# --- INIT DB ---
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    quantity REAL,
                    unit TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    instructions TEXT,
                    ingredients_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )''')
    conn.commit()
    conn.close()
init_db()

# --- REGISTER (UI NOU) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if not username or not password:
            flash('Username and password required!', 'error')
            return render_template('register.html')
        if User.find_by_username(username):
            flash('User already exists!', 'error')
            return render_template('register.html')
        pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, pw_hash))
        conn.commit()
        conn.close()
        flash('Account created! You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', active_page='')

# --- LOGIN (UI NOU) ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.find_by_username(username)
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('home'))
        flash('Invalid credentials!', 'error')
        return render_template('login.html')
    return render_template('login.html', active_page='')

# --- LOGOUT ---
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return 'Logged out!'

@app.route('/')
def root():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return redirect(url_for('home'))

@app.route('/home')
@login_required
def home():
    return render_template('index.html', active_page='instant')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['image']
    if not file:
        return "No file uploaded", 400

    filename = f"fridge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    image_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(image_path)

    # 1️⃣ Detectează ingredientele
    ingredients = detect_ingredients(image_path)

    # 2️⃣ Generează rețetele (cu handling pentru timeouts/erori)
    try:
        recipes_text = generate_recipes(ingredients)
    except Exception as e:
        recipes_text = (
            "Nu am putut genera rețete acum (serviciul AI a răspuns lent).\n\n"
            f"Ingrediente detectate: {', '.join(ingredients) if ingredients else '—'}.\n"
            "Te rog reîncearcă în câteva secunde."
        )

    # 3️⃣ Creează voce
    try:
        audio_path = speak(f"I found {', '.join(ingredients)}. Here are some recipe ideas!")
    except Exception:
        audio_path = None

    return render_template('result.html',
                           image_path=image_path,
                           ingredients=ingredients,
                           recipes=recipes_text,
                           audio_path=audio_path)

# --- CRUD INVENTAR ---
@app.route('/fridge', methods=['GET','POST'])
@login_required
def fridge():
    user_id = int(current_user.id)
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form['name']
            quantity = request.form['quantity']
            unit = request.form['unit']
            cur.execute('INSERT INTO ingredients (user_id, name, quantity, unit) VALUES (?, ?, ?, ?)', (user_id, name, quantity, unit))
        elif action == 'edit':
            ing_id = request.form['id']
            name = request.form['name']
            quantity = request.form['quantity']
            unit = request.form['unit']
            cur.execute('UPDATE ingredients SET name=?, quantity=?, unit=? WHERE id=? AND user_id=?', (name, quantity, unit, ing_id, user_id))
        elif action == 'delete':
            ing_id = request.form['id']
            cur.execute('DELETE FROM ingredients WHERE id=? AND user_id=?', (ing_id, user_id))
        conn.commit()
        conn.close()
        return redirect(url_for('fridge'))
    cur.execute('SELECT id, name, quantity, unit FROM ingredients WHERE user_id=?', (user_id,))
    items = [{'id': r[0],'name': r[1],'quantity': r[2],'unit': r[3]} for r in cur.fetchall()]
    # SELECT RECIPES for this user
    cur.execute('SELECT id, name, description, instructions, ingredients_json FROM recipes WHERE user_id=? ORDER BY created_at DESC', (user_id,))
    recipes = [{'id': r[0], 'name': r[1], 'description': r[2], 'instructions': r[3], 'ingredients': json.loads(r[4]) if r[4] else ''} for r in cur.fetchall()]
    conn.close()
    return render_template('fridge.html', items=items, recipes=recipes, active_page='fridge')

@app.route('/my_recipes', methods=['GET','POST'])
@login_required
def my_recipes():
    user_id = int(current_user.id)
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form['name']
            description = request.form.get('description','')
            instructions = request.form.get('instructions','')
            ingreds = request.form.get('ingredients','')
            cur.execute('INSERT INTO recipes (user_id, name, description, instructions, ingredients_json) VALUES (?, ?, ?, ?, ?)', (user_id, name, description, instructions, json.dumps(ingreds)))
        elif action == 'edit':
            rec_id = request.form['id']
            name = request.form['name']
            description = request.form.get('description','')
            instructions = request.form.get('instructions','')
            ingreds = request.form.get('ingredients','')
            cur.execute('UPDATE recipes SET name=?, description=?, instructions=?, ingredients_json=? WHERE id=? AND user_id=?', (name, description, instructions, json.dumps(ingreds), rec_id, user_id))
        elif action == 'delete':
            rec_id = request.form['id']
            cur.execute('DELETE FROM recipes WHERE id=? AND user_id=?', (rec_id, user_id))
        conn.commit()
    cur.execute('SELECT id, name, description, instructions, ingredients_json FROM recipes WHERE user_id=? ORDER BY created_at DESC', (user_id,))
    recipes = [{'id': r[0], 'name': r[1], 'description': r[2], 'instructions': r[3], 'ingredients': json.loads(r[4]) if r[4] else ''} for r in cur.fetchall()]
    conn.close()
    return render_template('my_recipes.html', recipes=recipes, active_page='recipes')

@app.route('/assistant', methods=['GET', 'POST'])
@login_required
def assistant_chat():
    if request.method == 'GET':
        session['chat_history'] = []
    if 'chat_history' not in session:
        session['chat_history'] = []
    chat_history = session['chat_history']
    user_id = int(current_user.id)
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute('SELECT name, quantity, unit FROM ingredients WHERE user_id=?', (user_id,))
    fridge_items = [{'name': r[0], 'quantity': r[1], 'unit': r[2]} for r in cur.fetchall()]
    # fetch recipes from DB
    cur.execute('SELECT name, description, instructions, ingredients_json FROM recipes WHERE user_id=?', (user_id,))
    db_recipes = []
    for row in cur.fetchall():
        try:
            ingred = json.loads(row[3]) if row[3] else []
            if isinstance(ingred, str):
                # handle plain text ingredients lists
                parts = [p.strip() for p in ingred.split(',') if p.strip()]
                ingred = parts
        except Exception:
            ingred = []
        db_recipes.append({
            'name': row[0],
            'description': row[1],
            'instructions': row[2],
            'ingredients': ingred
        })
    conn.close()
    agent = ChefAgent(recipes_db=db_recipes)
    if request.method == 'POST':
        user_message = request.form['message']
        chat_history.append({'role': 'user', 'text': user_message})
        import datetime
        try:
            reply = agent.get_reply(user_message, fridge_items, time_of_day=datetime.datetime.now().hour)
        except Exception as e:
            reply = 'A apărut o problemă la generarea răspunsului. Încearcă din nou.'
        chat_history.append({'role': 'assistant', 'text': reply})
        session['chat_history'] = chat_history
    return render_template('assistant.html', chat_history=chat_history, active_page='assistant', fridge_items=fridge_items)

if __name__ == "__main__":
    app.run(debug=True)
