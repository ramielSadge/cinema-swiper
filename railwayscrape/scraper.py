
import csv
import logging
import os
import time

import mysql.connector
import requests
from dotenv import load_dotenv

load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "cinema_scrape"),
    "connection_timeout": 20,
}

TMDB_BASE_URL = "https://api.themoviedb.org/3"
LETTERBOXD_BASE_URL = "https://letterboxd.com/"

CSV_FILE = "favorites.csv"
MAX_FAVORITES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


if not TMDB_API_KEY:
    raise ValueError(
        "TMDB_API_KEY is missing. Add it to your .env file."
    )


def load_favorites(filename):
    """
    Expected CSV columns:

    username,film1,year1,film2,year2,film3,year3,film4,year4

    Each row represents one Letterboxd user.
    """

    users = []

    with open(filename, "r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        required_columns = ["username"]

        for number in range(1, MAX_FAVORITES + 1):
            required_columns.extend([
                f"film{number}",
                f"year{number}",
            ])

        if not reader.fieldnames:
            raise ValueError("The CSV file is empty or has no header.")

        missing_columns = [
            column
            for column in required_columns
            if column not in reader.fieldnames
        ]

        if missing_columns:
            raise ValueError(
                "Missing CSV columns: "
                + ", ".join(missing_columns)
            )

        for row_number, row in enumerate(reader, start=2):
            username = (row.get("username") or "").strip()

            if not username:
                logging.warning(
                    "Skipping row %s because it has no username.",
                    row_number,
                )
                continue

            favorites = []

            for number in range(1, MAX_FAVORITES + 1):
                title = (row.get(f"film{number}") or "").strip()
                year_text = (row.get(f"year{number}") or "").strip()

                if not title:
                    continue

                year = None

                if year_text:
                    try:
                        year = int(year_text)
                    except ValueError:
                        logging.warning(
                            "Invalid year '%s' for %s, film: %s. "
                            "TMDB will be searched without a year.",
                            year_text,
                            username,
                            title,
                        )

                favorites.append({
                    "title": title,
                    "year": year,
                })

            users.append({
                "username": username,
                "favorites": favorites,
            })

    return users



def tmdb_get(endpoint, params=None):
    """Send a request to TMDB."""

    if params is None:
        params = {}

    request_params = params.copy()
    request_params["api_key"] = TMDB_API_KEY

    response = requests.get(
        f"{TMDB_BASE_URL}{endpoint}",
        params=request_params,
        timeout=20,
    )

    response.raise_for_status()
    return response.json()


def search_film(title, year=None):
    """Search TMDB by title and optional release year."""

    params = {
        "query": title,
        "include_adult": False,
    }

    if year:
        params["year"] = year

    try:
        data = tmdb_get("/search/movie", params)
        results = data.get("results", [])

        if not results:
            logging.warning(
                "No TMDB result found for '%s' (%s).",
                title,
                year if year else "year unknown",
            )
            return None

        normalized_title = title.casefold().strip()

        exact_matches = [
            movie for movie in results
            if movie.get("title", "").casefold().strip()
            == normalized_title
        ]

        candidates = exact_matches if exact_matches else results

        if year:
            matching_year = [
                movie for movie in candidates
                if movie.get("release_date", "").startswith(str(year))
            ]

            if matching_year:
                candidates = matching_year

      
        return max(
            candidates,
            key=lambda movie: movie.get("popularity", 0),
        )

    except requests.RequestException as error:
        logging.error(
            "TMDB search failed for '%s': %s",
            title,
            error,
        )
        return None


def get_film_details(tmdb_id):
    """Retrieve film information, director, and first three cast members."""

    try:
        data = tmdb_get(
            f"/movie/{tmdb_id}",
            {"append_to_response": "credits"},
        )

        release_date = data.get("release_date", "")
        year = (
            int(release_date[:4])
            if release_date[:4].isdigit()
            else None
        )

        credits = data.get("credits", {})
        crew = credits.get("crew", [])
        cast = credits.get("cast", [])

        director = next(
            (
                person.get("name")
                for person in crew
                if person.get("job") == "Director"
            ),
            None,
        )

        actors = [
            person.get("name")
            for person in cast[:3]
            if person.get("name")
        ]

        while len(actors) < 3:
            actors.append(None)

        poster_path = data.get("poster_path")

        poster_url = (
            f"https://image.tmdb.org/t/p/w500{poster_path}"
            if poster_path
            else None
        )

        return {
            "title": data.get("title"),
            "year": year,
            "tagline": data.get("tagline"),
            "overview": data.get("overview"),
            "poster": poster_url,
            "url": f"https://www.themoviedb.org/movie/{tmdb_id}",
            "director": director,
            "actor_1": actors[0],
            "actor_2": actors[1],
            "actor_3": actors[2],
        }

    except requests.RequestException as error:
        logging.error(
            "Could not retrieve TMDB details for ID %s: %s",
            tmdb_id,
            error,
        )
        return None




def get_or_create_user(cursor, username):
    """Create a Letterboxd user if needed and return their ID."""

    profile_url = f"{LETTERBOXD_BASE_URL}{username}/"

    cursor.execute(
        """
        INSERT INTO users (username, url)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            url = VALUES(url)
        """,
        (username, profile_url),
    )

    return cursor.lastrowid



def get_or_create_film(cursor, film):
    """Insert a film if needed and return its database ID."""

    if not film.get("title") or not film.get("year"):
        logging.warning(
            "Skipping film because its TMDB title or year is missing: %s",
            film.get("title"),
        )
        return None

    cursor.execute(
        """
        INSERT INTO films (
            title,
            year,
            tagline,
            overview,
            poster,
            url,
            director,
            actor_1,
            actor_2,
            actor_3
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id)
        """,
        (
            film["title"],
            film["year"],
            film.get("tagline"),
            film.get("overview"),
            film.get("poster"),
            film.get("url"),
            film.get("director"),
            film.get("actor_1"),
            film.get("actor_2"),
            film.get("actor_3"),
        ),
    )

    return cursor.lastrowid

def favorite_exists(cursor, user_id, film_id):
    """Check if a user-film relationship already exists."""

    cursor.execute(
        """
        SELECT 1
        FROM user_favorites
        WHERE user_id = %s AND film_id = %s
        """,
        (user_id, film_id),
    )

    return cursor.fetchone() is not None


def get_favorite_count(cursor, user_id):
    """Count the favorites currently saved for a user."""

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM user_favorites
        WHERE user_id = %s
        """,
        (user_id,),
    )

    return cursor.fetchone()[0]


def add_favorite(cursor, user_id, film_id):
    """Add a favorite unless it is a duplicate or exceeds the limit."""

    if favorite_exists(cursor, user_id, film_id):
        return "duplicate"

    if get_favorite_count(cursor, user_id) >= MAX_FAVORITES:
        return "limit"

    cursor.execute(
        """
        INSERT INTO user_favorites (user_id, film_id)
        VALUES (%s, %s)
        """,
        (user_id, film_id),
    )

    return "added"



def process_user(user, cursor, connection):
    username = user["username"]
    favorites = user["favorites"]

    logging.info("Processing Letterboxd user: %s", username)

    if not favorites:
        logging.info(
            "No films were provided for %s. Skipping.",
            username,
        )
        return

    user_id = get_or_create_user(cursor, username)

    added_count = 0
    duplicate_count = 0
    skipped_count = 0

    for favorite in favorites[:MAX_FAVORITES]:
        title = favorite["title"]
        year = favorite["year"]

        logging.info(
            "Searching TMDB for '%s' (%s).",
            title,
            year if year else "year unknown",
        )

        tmdb_match = search_film(title, year)

        if not tmdb_match:
            skipped_count += 1
            continue

        film = get_film_details(tmdb_match["id"])

        if not film:
            skipped_count += 1
            continue

        film_id = get_or_create_film(cursor, film)

        if not film_id:
            skipped_count += 1
            continue

        result = add_favorite(cursor, user_id, film_id)

        if result == "added":
            added_count += 1
            logging.info(
                "Added '%s' to %s's favorites.",
                film["title"],
                username,
            )

        elif result == "duplicate":
            duplicate_count += 1
            logging.info(
                "'%s' is already saved for %s.",
                film["title"],
                username,
            )

        elif result == "limit":
            skipped_count += 1
            logging.warning(
                "%s already has %s favorites. "
                "The maximum is %s.",
                username,
                MAX_FAVORITES,
                MAX_FAVORITES,
            )
            break

        time.sleep(0.25)

    connection.commit()

    logging.info(
        "Finished %s: %s added, %s duplicates, %s skipped.",
        username,
        added_count,
        duplicate_count,
        skipped_count,
    )



def main():
    try:
        users = load_favorites(CSV_FILE)

    except FileNotFoundError:
        logging.error(
            "%s was not found. Put it in the same folder as this script.",
            CSV_FILE,
        )
        return

    except ValueError as error:
        logging.error("CSV error: %s", error)
        return

    if not users:
        logging.warning("No users were found in %s.", CSV_FILE)
        return

    logging.info("Loaded %s users from CSV.", len(users))

    connection = None
    cursor = None

    try:
        logging.info("Connecting to Railway MySQL...")

        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        logging.info("Successfully connected to MySQL.")

        for user in users:
            try:
                process_user(user, cursor, connection)

            except mysql.connector.Error as error:
                connection.rollback()
                logging.error(
                    "Database error for %s: %s",
                    user["username"],
                    error,
                )

            except Exception:
                connection.rollback()
                logging.exception(
                    "Unexpected error for %s.",
                    user["username"],
                )

            time.sleep(1)

    except mysql.connector.Error as error:
        logging.error("Could not connect to MySQL: %s", error)

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

    logging.info("Import process finished.")


if __name__ == "__main__":
    main()