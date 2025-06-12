from flask import Flask, request, session, redirect, render_template, render_template_string, flash, get_flashed_messages
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash
import sqlite3, random, time

app = Flask(__name__)
app.secret_key = 'your_very_secure_secret'
socketio = SocketIO(app, manage_session=False)
DB_NAME = 'db.sqlite'

# ---------- Layout Wrapper Function ----------
def render_with_layout(title, body_html, **kwargs):
    layout = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{title}</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ padding: 30px; }}
            nav a {{ margin-right: 15px; }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-dark mb-4">
            <div class="container-fluid">
                <a class="navbar-brand" href="/admin">Admin Panel</a>
                <div class="navbar-nav">
                    <a class="nav-link" href="/admin">Dashboard</a>
                    <a class="nav-link" href="/admin/users">Users Manage</a>
                    <a class="nav-link" href="/admin/preferences">Preferences</a>
                </div>
            </div>
        </nav>
        <div class="container">
            {body_html}
        </div>
    </body>
    </html>
    """
    return render_template_string(layout, **kwargs)

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

BASE_LAYOUT_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>{{ title or "Chat Chat"  }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body {
            background: linear-gradient(to right, #0f2027, #203a43, #2c5364);
            font-family: Arial, sans-serif;
            color: white;
            text-align: center;
            margin: 0;
            padding: 40px;
        }
        .container {
            animation: fadeIn 0.8s ease;
            background: rgba(0,0,0,0.6);
            padding: 30px;
            border-radius: 10px;
            max-width: 500px;
            margin: auto;
        }
        form {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        input, button {
            padding: 10px;
            border-radius: 5px;
            border: none;
            font-size: 16px;
        }
        input {
            background: #333;
            color: white;
            border: 1px solid #555;
        }
        button {
            background: #2a9df4;
            color: white;
            cursor: pointer;
        }
        button:disabled {
            background: #777;
            cursor: not-allowed;
        }
        .error {
            color: #ff6666;
            font-weight: bold;
        }
        a {
            color: #aad4ff;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes shake {
            0% { transform: translateX(0); }
            25% { transform: translateX(-5px); }
            50% { transform: translateX(5px); }
            75% { transform: translateX(-5px); }
            100% { transform: translateX(0); }
        }
        .shake {
            animation: shake 0.4s;
        }
    </style>
</head>
<body>
    <div class="container {{ extra_class|default('') }}">
        {{ content|safe }}
    </div>
</body>
</html>
'''

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
        session['captcha_attempts'] = session.get('captcha_attempts', 0) + 1
        if user_answer == expected_answer:
            session["human_verified"] = True  # Mark as verified in the session
            session.pop("captcha_attempts", None)  # Reset attempts on success
            return redirect("/verify")
        else:
            error = "Incorrect answer. Please try again."
            if session['captcha_attempts'] >= 2:
                error += " <br><small>Hint: Keep it simple and literal (e.g. 'blue', '4').</small>"

    # Ask a new question if it's a GET request or answer was wrong
    question_obj = get_random_captcha()
    session["captcha_answer"] = question_obj["answer"]

    CAPTCHA_TEMPLATE = f'''
    <h3>Verify you are human</h3>
    <p>{question_obj["question"]}</p>
    <form method="POST">
        <input name="captcha" placeholder="Answer" required>
        <button type="submit">Verify</button>
        {"<p style='color:red'>" + error + "</p>" if error else ""}
    </form>
    '''
    return render_template_string(BASE_LAYOUT_TEMPLATE, title="CAPTCHA", content=CAPTCHA_TEMPLATE, extra_class='shake')

@app.route('/chat')
def chat():
    if not session.get("human_verified"):
        return redirect("/captcha")  # Ensure CAPTCHA passed
    return render_template_string(CHAT_TEMPLATE, user_id=session.get('user_id'))

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if not session.get('human_verified'):
        return redirect('/captcha')
    error_html = ''
    extra_class = ''
    if request.method == 'POST':
        if request.form.get('age_confirm') == 'on':
            session['age_verified'] = True
            return redirect('/auth')
        else:
            error_html = "<p class='error'>You must confirm you are 18+ to continue.</p>"
            extra_class = 'shake'
    AGE_TEMPLATE = '''
        <h3>Are you 18 or older?</h3>
        <form method="POST">
            <label>
                <input type="checkbox" id="age_confirm" name="age_confirm" onchange="toggleButton()"> I confirm I am 18+
            </label><br><br>
            <button type="submit" id="continueBtn" disabled>Continue</button>
        </form>

    <script>
        function toggleButton() {{
            const checkbox = document.getElementById('age_confirm');
            const button = document.getElementById('continueBtn');
            button.disabled = !checkbox.checked;
        }}
    </script>
    '''.format(error=error_html)
    return render_template_string(BASE_LAYOUT_TEMPLATE, title="Age Verification", content=AGE_TEMPLATE, extra_class=extra_class )

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
    return render_template_string(BASE_LAYOUT_TEMPLATE, title="Auth", content=AUTH_TEMPLATE, extra_class='shake')

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
    return render_template_string(BASE_LAYOUT_TEMPLATE, title="Login", content=LOGIN_TEMPLATE, extra_class='shake')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')
        hashed_pw = generate_password_hash(password)

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (username, email, phone, password) VALUES (?, ?, ?, ?)", (username, email, phone, hashed_pw))
            uid = c.lastrowid
            session['user_id'] = uid
        return redirect('/preferences')
    return render_template_string(BASE_LAYOUT_TEMPLATE, title="Register", content=REGISTER_TEMPLATE, extra_class='shake')

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
        flash("Preferences saved! Connecting you to a match...")
        return redirect('/chat')
    suggestions = get_preference_suggestions()
    return render_template_string(BASE_LAYOUT_TEMPLATE, title="Preferences", content=PREFERENCES_TEMPLATE, suggestions=suggestions, extra_class='shake')

# ---------- Admin Check ----------
def is_admin():
    return True  # Replace with real authentication

# ---------- Admin Routes ----------
@app.route('/admin')
def admin_dashboard():
    if not is_admin():
        return "Unauthorized", 403
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        user_count = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT user_id) FROM preferences")
        pref_users = c.fetchone()[0]
    body = """
    <h2>Dashboard Overview</h2>
    <div class="row">
        <div class="col-md-6">
            <div class="card text-bg-primary mb-3">
                <div class="card-body">
                    <h5 class="card-title">Total Users</h5>
                    <p class="card-text display-6">{{ user_count }}</p>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card text-bg-success mb-3">
                <div class="card-body">
                    <h5 class="card-title">Users with Preferences</h5>
                    <p class="card-text display-6">{{ pref_users }}</p>
                </div>
            </div>
        </div>
    </div>
    """
    return render_with_layout("Admin Dashboard", body, user_count=user_count, pref_users=pref_users)

@app.route('/admin/users')
def admin_users():
    if not is_admin():
        return "Unauthorized", 403
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, username, email, phone FROM users")
        users = c.fetchall()
    body = """
    <h2>User Management</h2>
    <table class="table table-striped">
        <thead><tr><th>Username</th><th>Email</th><th>Phone</th><th>Action</th></tr></thead>
        <tbody>
        {% for user in users %}
        <tr>
            <td>{{ user[1] }}</td>
            <td>{{ user[2] }}</td>
            <td>{{ user[3] }}</td>
            <td>
                <form action="/admin/delete_user/{{ user[0] }}" method="post" onsubmit="return confirm('Delete this user?');">
                    <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    """
    return render_with_layout("User Management", body, users=users)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not is_admin():
        return "Unauthorized", 403
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM preferences WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    return redirect('/admin/users')

@app.route('/admin/preferences')
def admin_preferences():
    if not is_admin():
        return "Unauthorized", 403
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT user_id, category, preference FROM preferences")
        prefs = c.fetchall()
    body = """
    <h2>User Preferences</h2>
    <table class="table table-bordered">
        <thead><tr><th>User ID</th><th>Preference Key</th><th>Value</th></tr></thead>
        <tbody>
        {% for p in prefs %}
        <tr>
            <td>{{ p[0] }}</td>
            <td>{{ p[1] }}</td>
            <td>{{ p[2] }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    """
    return render_with_layout("User Preferences", body, prefs=prefs)

active_users = {}

waiting_users = []  # Add this at the top of your file, near active_users

@socketio.on('join')
def on_join():
    uid = session.get('user_id')
    print(f"[JOIN] {request.sid} (user_id={uid})")

    user_prefs = get_user_preferences(uid) if uid else {}

    # Check if there's a waiting user
    for other in waiting_users:
        other_sid, other_uid, other_prefs = other
        if other_sid == request.sid:
            continue

        # Calculate shared preferences
        shared = 0
        for cat in user_prefs:
            if cat in other_prefs:
                shared += len(set(user_prefs[cat]) & set(other_prefs[cat]))

        if shared > 0:
            room_id = str(random.randint(10000, 99999))

            # Join both users to the same room
            socketio.server.enter_room(request.sid, room_id)
            socketio.server.enter_room(other_sid, room_id)

            # Remove the matched user from waiting list
            waiting_users.remove(other)

            # Notify both users
            emit('partner-found', {'room': room_id}, room=room_id)
            print(f"[MATCH] {request.sid} matched with {other_sid} in room {room_id}")
            return

    # No match found; add this user to waiting list
    waiting_users.append((request.sid, uid, user_prefs))
    emit('partner-found', {'room': None})
    print(f"[WAITING] {request.sid} is waiting")


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

    print(f"[SKIP] {request.sid} skipped and left room {data['room']}")

    on_join()  # Try matching again

@socketio.on('disconnect')
def on_disconnect():
    global waiting_users
    waiting_users = [u for u in waiting_users if u[0] != request.sid]

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
    <input name="password" type="password" placeholder="Password" required><br>
    <button type="submit">Login</button>
</form>
<br>
<a href="/register">Don't have an account? Register →</a>
<br>
<p><small><strong>Login</strong> to save your preferences and connect with similar users. Or</small></p>
<a href="/chat" style="color: #2a9df4;">Continue as Guest (no saved preferences)</a>
'''

REGISTER_TEMPLATE = '''
<h2>Register</h2>
<form method="POST">
    <input name="username" placeholder="Username" required><br>
    <input name="email" placeholder="Email"><br>
    <input name="phone" placeholder="Phone"><br>
    <input name="password" type="password" placeholder="Password" required><br>
    <button type="submit">Register</button>
</form>
<br>
<a href="/login">Already have an account? Login →</a>
<br>
<a href="/chat" style="color: #2a9df4;">Chat as Guest</a>
'''

PREFERENCES_TEMPLATE = '''
<h2>Set Preferences</h2>
<form method="POST">
    <label>Category: Interest</label><br>
    <input name="interest" value="gaming"> Gaming<br>
    <input name="interest" value="movies"> Movies<br>
    <input name="interest" value="books"> Books<br><br>
    <label>Custom Preferences:</label><br>
    <input name="custom" placeholder="e.g. anime, hiking, cooking, sci-fi"><br>
    <small>Separate multiple values with commas. These help match you with others who like the same things!</small><br><br>
    <button type="submit">Save</button>
</form>
<br><h3>Suggestions:</h3>
<ul>
{% set last_cat = None %}
{% for cat, pref, count in suggestions %}
    {% if cat != last_cat %}
        {% if not loop.first %}</ul>{% endif %}
        <li><strong>{{ cat }}</strong>:
        <ul>
        {% set last_cat = cat %}
    {% endif %}
    <li>{{ pref }} ({{ count }} users)</li>
    {% if loop.last %}</ul>{% endif %}
{% endfor %}
</ul>
'''

CHAT_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
<title>Chat Chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    * {
        box-sizing: border-box;
    }

    body {
        background: #0e0e0e;
        color: white;
        font-family: Arial, sans-serif;
        text-align: center;
        padding: 10px;
        margin: 0;
    }

    #chat-box {
        width: 100%;
        max-width: 800px;
        margin: 0 auto;
        height: 50vh;
        background: #1f1f1f;
        padding: 10px;
        overflow-y: auto;
        border-radius: 8px;
        border: 1px solid #444;
    }

    .message {
        text-align: left;
        margin: 5px 0;
        word-wrap: break-word;
    }

    .you {
        color: #81f781;
    }

    .stranger {
        color: #81bef7;
    }

    .system {
        color: #ffa500;
        font-style: italic;
    }

    #typing {
        font-style: italic;
        color: #aaa;
        margin-top: 5px;
    }

    .bar {
        display: flex;
        flex-direction: row;
        align-items: center;
        justify-content: center;
        max-width: 800px;
        margin: 10px auto;
        flex-wrap: wrap;
        gap: 10px;
    }

    input {
        flex: 1;
        min-width: 50px;
        padding: 10px;
        border-radius: 5px;
        border: 1px solid #555;
        background-color: #1a1a1a;
        color: white;
        font-size: 16px;
    }

    button {
        padding: 10px 20px;
        background: #2a9df4;
        color: white;
        border: none;
        border-radius: 5px;
        cursor: pointer;
        font-size: 16px;
    }

    button:hover {
        background: #007acc;
    }

    @media (max-width: 600px) {
        #chat-box {
            height: 40vh;
        }

        .bar {
            flex-direction: column;
            align-items: stretch;
        }

        input {
            width: 100%;
        }

        button {
            width: 100%;
        }
    }
</style>
</head>
<body>
<h2>Welcome to Chat Chat</h2>
{% with messages = get_flashed_messages() %}
  {% if messages %}
    <div style="color: #00ff99; font-weight: bold;">
        {{ messages[0] }}
    </div>
  {% endif %}
{% endwith %}
{% if not user_id %}
    <div style="margin-bottom: 15px;">
        <a href="/login">
            <button style="margin-right: 10px;">Login</button>
        </a>
        <a href="/register">
            <button>Register</button>
        </a>
    </div>
{% else %}
    <p style="color: #aaa;">You are logged in.</p>
{% endif %}
<div id="chat-box"></div>
<div id="typing"></div>
<div class="bar">
    <button id="skip">Skip</button>
</div>

<div class="bar chat-controls" style="display:none;">
    <input id="input" placeholder="Type a message...">
    <button id="send">Send</button>
</div>

<script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
<script>
    const socket = io();
    let room = '';
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('send');

    socket.emit('join');

    socket.on('partner-found', data => {
        console.log("Partner-found data:", data);
        room = data && data.room;

        const controls = document.querySelector('.chat-controls');

        if (room) {
            append("System: Connected to a stranger", 'system');
            controls.style.display = 'flex';
            input.disabled = false;
            sendBtn.disabled = false;
        } else {
            append("System: Waiting for a match...", 'system');
            controls.style.display = 'none';
            input.disabled = true;
            sendBtn.disabled = true;
        }
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

    input.addEventListener('keypress', e => {
        if (e.key === 'Enter') sendMsg();
        else socket.emit('typing', { room });
    });

    function sendMsg() {
        const val = input.value;
        if (val.trim() && room) {
            append("You: " + val, 'you');
            socket.emit('message', { message: val, room });
            input.value = '';
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
    app.run(debug=True)
