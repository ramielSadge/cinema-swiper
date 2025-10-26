import os
import re
import random
import requests
import subprocess
import mysql.connector
from flask import Flask, render_template_string, request, redirect
from playwright.sync_api import sync_playwright

# ---------------------- Ensure Playwright Browsers Exist ---------------------- #
try:
    if not os.path.exists("/root/.cache/ms-playwright/chromium_headless_shell-1187"):
        print("[INFO] Playwright browsers not found. Installing now...")
        subprocess.run(
            ["python", "-m", "playwright", "install", "--with-deps"],
            check=True
        )
        print("[INFO] Playwright browsers installed successfully.")
except Exception as e:
    print("[WARN] Could not preinstall Playwright browsers:", e)

# ---------------------- TMDB API Key ---------------------- #
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "0860b24129791ad61907f7425b4dda17")

# ---------------------- Flask App ---------------------- #
app = Flask(__name__)

# ---------------------- Database Connection ---------------------- #
db = mysql.connector.connect(
    host=os.getenv("MYSQLHOST", "localhost"),
    user=os.getenv("MYSQLUSER", "root"),
    password=os.getenv("MYSQLPASSWORD", ""),
    database=os.getenv("MYSQLDATABASE", ""),
    port=int(os.getenv("MYSQLPORT", 3306))
)
cursor = db.cursor()

# ---------------------- Create Tables if not exist ---------------------- #
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(255) UNIQUE,
    favorites TEXT
)
""")
db.commit()

# ---------------------- Helper Functions ---------------------- #
def get_favorites_from_letterboxd(username):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = browser.new_page()
            url = f"https://letterboxd.com/{username}/"
            page.goto(url, timeout=60000)

            # Wait for favorite films section
            page.wait_for_selector(".poster-list -p40 -p70 -p90", timeout=10000)
            html = page.content()

            # Extract favorites
            favorites = re.findall(r'data-film-name="([^"]+)"', html)
            browser.close()

            if not favorites:
                raise Exception("No favorites found.")
            return favorites[:4]
    except Exception as e:
        print(f"[ERROR] Failed to fetch favorites for {username}: {e}")
        return []

def search_tmdb(title):
    query = requests.utils.quote(title)
    url = f"https://api.themoviedb.org/3/search/movie?query={query}&api_key={TMDB_API_KEY}"
    response = requests.get(url)
    data = response.json()
    if data.get("results"):
        return data["results"][0].get("title", title)
    return title

# ---------------------- Routes ---------------------- #
@app.route("/", methods=["GET", "POST"])
def index():
    msg = request.args.get("msg", "")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if not username:
            return redirect("/?msg=Please enter a username.")

        cursor.execute("SELECT favorites FROM users WHERE username = %s", (username,))
        result = cursor.fetchone()

        if result:
            favorites = result[0].split(",")
        else:
            favorites = get_favorites_from_letterboxd(username)
            if not favorites:
                return redirect(f"/?msg=User '{username}' has no favorites.")
            cursor.execute("INSERT INTO users (username, favorites) VALUES (%s, %s)",
                           (username, ",".join(favorites)))
            db.commit()

        # Search TMDB for movie titles
        matched = [search_tmdb(f) for f in favorites]

        html = """
        <html>
        <head><title>Favorites for {{ username }}</title></head>
        <body style="font-family:sans-serif; text-align:center; margin-top:50px;">
            <h2>Favorites for {{ username }}</h2>
            <ul style="list-style:none;">
                {% for f in favorites %}
                    <li>{{ f }}</li>
                {% endfor %}
            </ul>
            <a href="/">Back</a>
        </body>
        </html>
        """
        return render_template_string(html, username=username, favorites=matched)

    html = """
    <html>
    <head><title>Letterboxd Favorites Finder</title></head>
    <body style="font-family:sans-serif; text-align:center; margin-top:50px;">
        <h1>Letterboxd Favorites Finder</h1>
        <form method="POST">
            <input type="text" name="username" placeholder="Enter Letterboxd username" required>
            <button type="submit">Find Favorites</button>
        </form>
        {% if msg %}<p style="color:red;">{{ msg }}</p>{% endif %}
    </body>
    </html>
    """
    return render_template_string(html, msg=msg)

# ---------------------- Run Server ---------------------- #
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
