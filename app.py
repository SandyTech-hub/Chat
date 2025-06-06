from flask import Flask, request, session, redirect, render_template_string
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3, random, time

app = Flask(__name__)
app.secret_key = 'your_very_secure_secret'
socketio = SocketIO(app)
DB_NAME = 'chatchat.db'

# --- DATABASE INITIALIZATION ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            email TEXT,
            phone TEXT,
            is_admin INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS preferences (
            user_id INTEGER,
            category TEXT,
            preference TEXT
        )''')
        conn.commit()

init_db()

# --- AUTH / UTILS ---
ADMIN_CREDENTIALS = {'username': 'admin', 'password': 'admin123'}

def get_user():
    return session.get('user_id')

def is_admin():
    return session.get('is_admin', False)

def get_user_preferences(uid):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT category, preference FROM preferences WHERE user_id=?", (uid,))
        data = {}
        for category, pref in c.fetchall():
            data.setdefault(category, []).append(pref)
        return data

def get_preference_suggestions():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT category, preference, COUNT(*) FROM preferences GROUP BY category, preference ORDER BY COUNT(*) DESC")
        return c.fetchall()

def match_user_by_preferences(user_prefs, exclude_id=None):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        scores = {}
        for category, prefs in user_prefs.items():
            for pref in prefs:
                c.execute("SELECT user_id FROM preferences WHERE category=? AND preference=?", (category, pref))
                for (uid,) in c.fetchall():
                    if uid == exclude_id:
                        continue
                    scores[uid] = scores.get(uid, 0) + 1
        return sorted(scores, key=scores.get, reverse=True)
@app.route('/')
def home():
    session.clear()  # Restart everything on refresh
    return redirect('/captcha')

# Define the CAPTCHA questions
CAPTCHA_QUESTIONS = [
    {"question": "What color is the sky on a clear sunny day?", "answer": "blue"},
    {"question": "How many legs does a typical dog have?", "answer": "4"},
    {"question": "Which is heavier: 1kg of iron or 1kg of cotton?", "answer": "same"},
    {"question": "What comes after the letter 'C' in the alphabet?", "answer": "D"},
    {"question": "What sound does a cat make?", "answer": "meow"},
    {"question": "Type the word 'human' backwards", "answer": "namuh"},
    {"question": "What is 2 + 2?", "answer": "4"},
    {"question": "Which one is a fruit: car, banana, chair?", "answer": "banana"},
    {"question": "Which day comes after Monday?", "answer": "Tuesday"},
    {"question": "What do you use to write: pen, stone, or blanket?", "answer": "pen"},
]

def get_random_captcha():
    question_obj = random.choice(CAPTCHA_QUESTIONS)
    return question_obj

@app.route("/captcha", methods=["GET", "POST"])
def captcha():
    # If already verified, go directly to chatroom
    if session.get("verified"):
        return redirect('/verify')

    error = None

    if request.method == "POST":
        user_answer = request.form.get("captcha", "").strip().lower()
        expected_answer = session.get("captcha_answer", "").lower()
        if user_answer == expected_answer:
            session["human_verified"] = True  # Mark as verified in the session
            return redirect("/verify")
        else:
            error = "Incorrect answer. Please try again."

    # Ask a new question if it's a GET request or answer was wrong
    question_obj = get_random_captcha()
    session["captcha_answer"] = question_obj["answer"]

    CAPTCHA_TEMPLATE = f'''
    <form method="POST">
        <h3>Verify you are human</h3>
        <p>{question_obj["question"]}</p>
        <input name="captcha" placeholder="Answer" required>
        <button type="submit">Verify</button>
        {"<p style='color:red'>" + error + "</p>" if error else ""}
    </form>
    '''

    return render_template_string(CAPTCHA_TEMPLATE)

@app.route("/chatroom")
def chatroom():
    if not session.get("human_verified"):
        return redirect("/captcha")  # Force verification before accessing chatroom
    return redirect('/verify')


@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if not session.get('human_verified'):
        return redirect('/captcha')
    if request.method == 'POST':
        if request.form.get('age_confirm') == 'on':
            session['age_verified'] = True
            return redirect('/auth')
        return render_template_string("<h3>You must confirm age 18+</h3>" + AGE_TEMPLATE)
    return render_template_string(AGE_TEMPLATE)

@app.route('/auth', methods=['GET', 'POST'])
def auth():
    if not session.get('age_verified'):
        return redirect('/verify')
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'skip':
            return redirect('/chat')
        elif action == 'login':
            return redirect('/login')
        elif action == 'register':
            return redirect('/register')
    return render_template_string(AUTH_TEMPLATE)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_CREDENTIALS['username'] and password == ADMIN_CREDENTIALS['password']:
            session['user_id'] = -1
            session['is_admin'] = True
            return redirect('/admin')
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE username=?", (username,))
            user = c.fetchone()
            if user:
                session['user_id'] = user[0]
                return redirect('/chat')
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        phone = request.form.get('phone')
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (username, email, phone) VALUES (?, ?, ?)", (username, email, phone))
            uid = c.lastrowid
            session['user_id'] = uid
        return redirect('/preferences')
    return render_template_string(REGISTER_TEMPLATE)

@app.route('/preferences', methods=['GET', 'POST'])
def preferences():
    uid = get_user()
    if not uid:
        return redirect('/login')
    if request.method == 'POST':
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM preferences WHERE user_id=?", (uid,))
            for category, pref_list in request.form.lists():
                if category == "custom":
                    continue  # We'll handle it separately
                for pref in pref_list:
                    c.execute("INSERT INTO preferences (user_id, category, preference) VALUES (?, ?, ?)",
                              (uid, category, pref))

            # Handle custom preferences (comma separated)
            custom_input = request.form.get("custom", "").strip()
            if custom_input:
                custom_prefs = [p.strip() for p in custom_input.split(",") if p.strip()]
                for cpref in custom_prefs:
                    c.execute("INSERT INTO preferences (user_id, category, preference) VALUES (?, ?, ?)",
                              (uid, 'custom', cpref))

        return redirect('/chat')
    suggestions = get_preference_suggestions()
    return render_template_string(PREFERENCES_TEMPLATE, suggestions=suggestions)

@app.route('/admin')
def admin():
    if not is_admin():
        return "Unauthorized", 403
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT username, email, phone FROM users")
        users = c.fetchall()
        prefs = get_preference_suggestions()
    return render_template_string(ADMIN_TEMPLATE, users=users, prefs=prefs)
@app.route('/chat')
def chat():
    return render_template_string(CHAT_TEMPLATE, user_id=session.get('user_id'), is_admin=is_admin())

active_users = {}

waiting_users = []  # Add this at the top of your file, near active_users

@socketio.on('join')
def on_join():
    uid = session.get('user_id')
    user_prefs = get_user_preferences(uid) if uid else {}

    # Try to match with someone from waiting list
    for other_sid, other_uid, other_prefs in waiting_users:
        if other_sid == request.sid:
            continue
        # Calculate shared preferences
        shared = 0
        for category in user_prefs:
            if category in other_prefs:
                shared += len(set(user_prefs[category]) & set(other_prefs[category]))
        if shared > 0:
            room_id = str(random.randint(10000, 99999))
            join_room(room_id)
            join_room(room_id, sid=other_sid)
            emit('partner-found', {'room': room_id}, room=room_id)
            waiting_users.remove((other_sid, other_uid, other_prefs))
            return

    # No match found, wait
    waiting_users.append((request.sid, uid, user_prefs))
    emit('partner-found', {'room': None})


@socketio.on('message')
def on_message(data):
    emit('message', {'message': data['message']}, room=data['room'])

@socketio.on('typing')
def on_typing(data):
    emit('typing', {}, room=data['room'], include_self=False)

@socketio.on('skip')
def on_skip(data):
    leave_room(data['room'])
    emit('partner-left', {}, room=data['room'])
    global waiting_users
    waiting_users = [u for u in waiting_users if u[0] != request.sid]
    socketio.emit('join')  # Retry joining

@socketio.on('disconnect')
def on_disconnect():
    global waiting_users
    waiting_users = [u for u in waiting_users if u[0] != request.sid]

AGE_TEMPLATE = '''
<form method="POST">
    <h2>Are you 18 years or older?</h2>
    <label><input type="checkbox" name="age_confirm"> I confirm I am 18+</label><br><br>
    <button type="submit">Continue</button>
</form>
'''

AUTH_TEMPLATE = '''
<form method="POST">
    <h2>Welcome to Chat Chat</h2>
    <button name="action" value="login">Login</button>
    <button name="action" value="register">Register</button>
    <button name="action" value="skip">Continue as Guest</button>
</form>
'''

LOGIN_TEMPLATE = '''
<h2>Login</h2>
<form method="POST">
    <input name="username" placeholder="Username" required><br>
    <input name="password" type="password" placeholder="Password (admin only)" required><br>
    <button type="submit">Login</button>
</form>
'''

REGISTER_TEMPLATE = '''
<h2>Register</h2>
<form method="POST">
    <input name="username" placeholder="Username" required><br>
    <input name="email" placeholder="Email"><br>
    <input name="phone" placeholder="Phone"><br>
    <button type="submit">Register</button>
</form>
'''

PREFERENCES_TEMPLATE = '''
<h2>Set Preferences</h2>
<form method="POST">
    <label>Category: Interest</label><br>
    <input name="interest" value="gaming"> Gaming<br>
    <input name="interest" value="movies"> Movies<br>
    <input name="interest" value="books"> Books<br><br>
    <label>Custom Preferences:</label><br>
    <input name="custom" placeholder="e.g. anime, hiking, chess"><br>
    <small>Separate multiple preferences with commas</small><br><br>
    <button type="submit">Save</button>
</form>
<br><h3>Suggestions:</h3>
<ul>
{% for cat, pref, count in suggestions %}
    <li>{{ cat }} → {{ pref }} ({{ count }} users)</li>
{% endfor %}
</ul>
'''

ADMIN_TEMPLATE = '''
<h2>Admin Dashboard</h2>
<h3>Users</h3>
<ul>
{% for u in users %}
    <li>{{ u[0] }} | {{ u[1] }} | {{ u[2] }}</li>
{% endfor %}
</ul>
<h3>Preferences Summary</h3>
<ul>
{% for cat, pref, count in prefs %}
    <li>{{ cat }}: {{ pref }} ({{ count }})</li>
{% endfor %}
</ul>
'''

CHAT_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
<title>Chat Chat</title>
<style>
    body { background: #0e0e0e; color: white; font-family: Arial, sans-serif; text-align: center; padding: 20px; }
    #chat-box { width: 80%; margin: auto; height: 300px; background: #1f1f1f; padding: 10px; overflow-y: auto; border-radius: 8px; border: 1px solid #444; }
    .message { text-align: left; margin: 5px 0; }
    .you { color: #81f781; }
    .stranger { color: #81bef7; }
    .system { color: #ffa500; font-style: italic; }
    #typing { font-style: italic; color: #aaa; margin-top: 5px; }
    .bar { display: flex; justify-content: space-between; align-items: center; width: 80%; margin: 10px auto; }
    input { flex: 1; padding: 10px; margin: 0 10px; border-radius: 5px; border: 1px solid #555; background-color: #1a1a1a; color: white; }
    button { padding: 10px 20px; background: #2a9df4; color: white; border: none; border-radius: 5px; cursor: pointer; }
    button:hover { background: #007acc; }
</style>
</head>
<body>
<h2>Welcome to Chat Chat</h2>
{% if not user_id %}
    <a href="/login" style="color: #2a9df4;">Login</a>
{% endif %}
<div id="chat-box"></div>
<div id="typing"></div>
<div class="bar">
    <button id="skip">Skip</button>
    <input id="input" placeholder="Type a message...">
    <button id="send">Send</button>
</div>
<script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
<script>
    const socket = io();
    let room = '';
    socket.emit('join');

    socket.on('partner-found', data => {
        room = data.room;
        append("System: Connected to a stranger", 'system');
    });

    socket.on('message', data => {
        append("Stranger: " + data.message, 'stranger');
    });

    socket.on('typing', () => {
        document.getElementById('typing').innerText = "Stranger is typing...";
        setTimeout(() => document.getElementById('typing').innerText = "", 2000);
    });

    document.getElementById('send').onclick = sendMsg;
    document.getElementById('skip').onclick = () => {
        socket.emit('skip', { room });
        socket.emit('join');
        document.getElementById('chat-box').innerHTML = '';
    };

    document.getElementById('input').addEventListener('keypress', e => {
        if (e.key === 'Enter') sendMsg();
        else socket.emit('typing', { room });
    });

    function sendMsg() {
        const val = document.getElementById('input').value;
        if (val.trim()) {
            append("You: " + val, 'you');
            socket.emit('message', { message: val, room });
            document.getElementById('input').value = '';
        }
    }

    function append(text, cls) {
        const div = document.createElement('div');
        div.innerText = text;
        div.className = 'message ' + cls;
        const box = document.getElementById('chat-box');
        box.appendChild(div);
        box.scrollTop = box.scrollHeight;
    }
</script>
</body>
</html>
'''
if __name__ == '__main__':
    socketio.run(app, debug=True)
