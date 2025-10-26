import re
import random
import requests
import mysql.connector
from flask import Flask, render_template_string, request, redirect
from playwright.sync_api import sync_playwright
import os

TMDB_API_KEY = os.getenv("TMDB_API_KEY")

app = Flask(__name__)

# ---------------------- Database Connection ---------------------- #


import os
from dotenv import load_dotenv
load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")

db = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"),
    user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_DB")
)

cursor = db.cursor(dictionary=True)

user_queue = []

# ---------------------- Utility Functions ---------------------- #

def user_exists(username):
    """Check if a Letterboxd user exists using a fast HTTP request."""
    url = f"https://letterboxd.com/{username}/"
    try:
        r = requests.get(url, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def split_favorites(fav_text):
    """
    Split favorites using commas only after the closing parenthesis,
    and extract both title and year.
    Example input: "The Thing (1982), Alien (1979), ..."
    Returns: [{'title': 'The Thing', 'year': '1982'}, ...]
    """
    films = re.split(r'\),\s*', fav_text)
    films = [f + ')' if not f.endswith(')') else f for f in films]

    results = []
    for f in films[:4]:  # limit to 4 favorites
        match = re.match(r'(.+?)\s*\((\d{4})\)', f.strip())
        if match:
            title, year = match.groups()
            results.append({"title": title.strip(), "year": year})
        else:
            results.append({"title": f.strip(), "year": None})
    return results


def get_favorites(username):
    """Scrape 4 favorite films from Letterboxd using Playwright."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            page = browser.new_page()
            page.goto(f"https://letterboxd.com/{username}/", timeout=30000)
            content = page.locator("meta[name='description']").get_attribute("content")
            browser.close()

            if not content:
                return None

            match = re.search(r"Favorites:\s*(.+?)(?:\.?\s*Bio:|$)", content)
            if not match:
                return None

            fav_text = match.group(1).replace("…", "...")
            return split_favorites(fav_text)
    except Exception:
        return None


def get_tmdb_info(title, year=None):
    """Fetch TMDb poster and link for a film title, using year if available."""
    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}
    if year:
        params["primary_release_year"] = year  # filters results by year

    r = requests.get(search_url, params=params)
    if r.status_code != 200 or not r.json().get("results"):
        return None

    movie = r.json()["results"][0]
    poster_path = movie.get("poster_path")
    tmdb_id = movie.get("id")
    return {
        "title": f"{title} ({year})" if year else title,
        "poster": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None,
        "url": f"https://www.themoviedb.org/movie/{tmdb_id}" if tmdb_id else None
    }


def add_user_to_db(username):
    """Scrape favorites and store in DB if not already present."""
    cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    if user:
        return "already"

    favs = get_favorites(username)
    if not favs:
        return "not_found"

    cursor.execute("INSERT INTO users (username) VALUES (%s)", (username,))
    user_id = cursor.lastrowid

    for film in favs:
        info = get_tmdb_info(film["title"], film["year"])
        if info:
            cursor.execute(
                "INSERT INTO favorites (user_id, title, poster, url) VALUES (%s, %s, %s, %s)",
                (user_id, info["title"], info["poster"], info["url"])
            )
    db.commit()
    return "found"


def load_users_from_db():
    """Load all users and favorites from DB into memory and shuffle."""
    global user_queue
    cursor.execute(
        "SELECT u.id as user_id, u.username, f.title, f.poster, f.url "
        "FROM users u LEFT JOIN favorites f ON u.id=f.user_id ORDER BY u.id"
    )
    rows = cursor.fetchall()

    users = {}
    for row in rows:
        uid = row["user_id"]
        if uid not in users:
            users[uid] = {"username": row["username"], "favorites": []}
        users[uid]["favorites"].append({
            "title": row["title"],
            "poster": row["poster"],
            "url": row["url"]
        })

    user_queue = list(users.values())
    random.shuffle(user_queue)


# ---------------------- Flask Routes ---------------------- #

@app.route("/", methods=["GET", "POST"])
def index():
    global user_queue
    message = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username:
            if not user_exists(username):
                message = f"User '{username}' not found!"
            else:
                result = add_user_to_db(username)
                if result == "found":
                    message = f"User '{username}' found!"
                elif result == "already":
                    message = f"User '{username}' is already in the database."
                else:
                    message = f"User '{username}' has no favorites."
            load_users_from_db()
        return redirect(f"/?msg={message}" if message else "/")

    if not user_queue:
        load_users_from_db()

    if user_queue:
        user = user_queue.pop(0)
        favorites = user["favorites"]
        username = user["username"]
    else:
        favorites = []
        username = None

    from flask import request as req
    msg = req.args.get("msg")

    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Letterboxd Favorites Viewer</title>
<style>
body { font-family: Arial, sans-serif; background-color: #0d1117; color: white; text-align: center; margin: 0; padding: 0; overflow-x: hidden; }
h1 { margin-top: 30px; color: #58a6ff; }
h2 { color: #ff4c4c; }
.poster-container { display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 20px; margin-top: 40px; }
.poster { width: 200px; height: 300px; border-radius: 12px; object-fit: cover; transition: transform 0.3s ease; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
.poster:hover { transform: scale(1.05); }
.buttons { margin-top: 40px; }
button { background-color: #1f6feb; color: white; border: none; padding: 12px 24px; margin: 0 10px; border-radius: 8px; font-size: 16px; cursor: pointer; }
button:hover { background-color: #2d81f7; }
.footer { position: fixed; bottom: 15px; left: 15px; opacity: 0.8; font-size: 12px; color: #aaa; }
form { margin-top: 30px; }
input { padding: 8px; border-radius: 6px; border: none; font-size: 16px; width: 200px; }
.status { margin-top: 10px; font-size: 16px; }
.status.found { color: #4CAF50; }
.status.not_found { color: #ff4c4c; }
</style>
</head>
<body>

{% if username %}
<h1>@{{ username }}'s Favorites</h1>
<div class="poster-container">
{% for film in favorites %}
    <a href="{{ film.url }}" target="_blank">
        <img class="poster" src="{{ film.poster }}" alt="{{ film.title }}">
    </a>
{% endfor %}
</div>

<div class="buttons">
    <button onclick="window.location.reload()">← Next User</button>
    <button onclick="window.open('https://letterboxd.com/{{ username }}/', '_blank')">→ View Profile</button>
</div>
{% else %}
<h2>No users yet. Add a username below!</h2>
{% endif %}

<form method="POST">
    <input type="text" name="username" placeholder="Add Letterboxd username" required>
    <button type="submit">Add User</button>
</form>

{% if msg %}
<div class="status {% if 'not found' in msg.lower() %}not_found{% elif 'found' in msg.lower() %}found{% endif %}">{{ msg }}</div>
{% endif %}

<div class="footer">Source: TMDB</div>

<script>
document.addEventListener('keydown', function(e) {
    if (e.key === 'ArrowRight' && '{{ username }}') {
        window.open('https://letterboxd.com/{{ username }}/', '_blank');
    } else if (e.key === 'ArrowLeft') {
        window.location.reload();
    }
});
</script>

</body>
</html>
''', favorites=favorites, username=username, msg=msg)


# ---------------------- Run App ---------------------- #

if __name__ == "__main__":
    load_users_from_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
