"""
API BookWorld
=============

API REST (Flask) exposant les données de la base finale bookworld_final.db
(produite par pipeline.py).

Authentification : token simple transmis dans l'en-tête HTTP `X-API-Key`.
Le token attendu est lu depuis la variable d'environnement API_TOKEN ; à
défaut, un token de développement est utilisé (un avertissement est loggé
dans ce cas — ne pas utiliser cette valeur par défaut en production).

Lancement local :
    export API_TOKEN="mon-token-secret"
    python api.py

Exemple d'appel :
    curl -H "X-API-Key: mon-token-secret" http://127.0.0.1:5000/sales-by-country
"""

from __future__ import annotations

import logging
import os
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bookworld_final.db"

DEV_DEFAULT_TOKEN = "dev-token-please-change"
API_TOKEN = os.environ.get("API_TOKEN", DEV_DEFAULT_TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("bookworld_api")

if API_TOKEN == DEV_DEFAULT_TOKEN:
    logger.warning(
        "[Auth] API_TOKEN non défini : utilisation du token de développement par défaut. "
        "Définir la variable d'environnement API_TOKEN avant tout déploiement."
    )

app = Flask(__name__)


# --------------------------------------------------------------------------
# Accès base de données
# --------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    """Ouvre (ou réutilise) une connexion SQLite pour la requête en cours."""
    if "db" not in g:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------
# Authentification par token
# --------------------------------------------------------------------------
def require_token(view_func):
    """Décorateur : exige un en-tête `X-API-Key` valide.

    Réponses :
        401 si l'en-tête est absent
        403 si le token est présent mais incorrect
    """

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        provided_token = request.headers.get("X-API-Key")

        if not provided_token:
            logger.warning("[Auth] Requête refusée (token manquant) sur %s", request.path)
            return jsonify({"error": "unauthorized", "message": "En-tête X-API-Key manquant."}), 401

        if provided_token != API_TOKEN:
            logger.warning("[Auth] Requête refusée (token invalide) sur %s", request.path)
            return jsonify({"error": "forbidden", "message": "Token invalide."}), 403

        return view_func(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    """Vérifie que l'API répond et que la base finale est accessible.
    Public (pas de token requis) : c'est un endpoint de supervision.
    """
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        db_status = "connected"
        status_code = 200
        status = "ok"
    except sqlite3.Error as exc:
        logger.error("[Health] Base de données inaccessible : %s", exc)
        db_status = "unavailable"
        status_code = 503
        status = "degraded"

    return jsonify({"status": status, "database": db_status}), status_code


@app.route("/sales-by-country", methods=["GET"])
@require_token
def sales_by_country():
    """Renvoie l'agrégation des ventes par pays (table agg_sales_by_country)."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT country_code, country_name, total_orders, total_quantity, "
            "total_revenue_gbp, total_revenue_eur "
            "FROM agg_sales_by_country "
            "ORDER BY total_revenue_eur DESC;"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.error("[sales-by-country] Erreur SQL : %s", exc)
        return jsonify({"error": "internal_error", "message": "Erreur lors de la lecture des données."}), 500

    data = rows_to_dicts(rows)
    return jsonify({"count": len(data), "results": data}), 200


@app.route("/books", methods=["GET"])
@require_token
def books():
    """Renvoie le catalogue des livres (table dim_books)."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT book_id, book_name, price_gbp, rating, availability FROM dim_books "
            "ORDER BY book_id;"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.error("[books] Erreur SQL : %s", exc)
        return jsonify({"error": "internal_error", "message": "Erreur lors de la lecture des données."}), 500

    data = rows_to_dicts(rows)
    return jsonify({"count": len(data), "results": data}), 200


@app.route("/countries", methods=["GET"])
@require_token
def countries():
    """Renvoie le référentiel pays (table dim_countries)."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT country_code, country_name, currency_code, vat_rate, region "
            "FROM dim_countries ORDER BY country_code;"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.error("[countries] Erreur SQL : %s", exc)
        return jsonify({"error": "internal_error", "message": "Erreur lors de la lecture des données."}), 500

    data = rows_to_dicts(rows)
    return jsonify({"count": len(data), "results": data}), 200


@app.route("/sales", methods=["GET"])
@require_token
def sales():
    """Renvoie les ventes détaillées (table fact_sales), avec filtres optionnels.

    Paramètres de requête optionnels :
        country_code : filtre sur le pays (ex. ?country_code=FR)
        book_id      : filtre sur le livre (ex. ?book_id=BOOK00001)
        limit        : nombre maximum de lignes renvoyées (défaut 100, max 1000)
    """
    country_code = request.args.get("country_code")
    book_id = request.args.get("book_id")

    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        return jsonify({"error": "bad_request", "message": "'limit' doit être un entier."}), 400
    limit = max(1, min(limit, 1000))

    query = (
        "SELECT order_id, order_date, book_id, country_code, channel_code, "
        "quantity, discount_rate, revenue_gbp, revenue_eur FROM fact_sales WHERE 1=1"
    )
    params: list = []

    if country_code:
        query += " AND country_code = ?"
        params.append(country_code)
    if book_id:
        query += " AND book_id = ?"
        params.append(book_id)

    query += " ORDER BY order_date DESC LIMIT ?;"
    params.append(limit)

    try:
        conn = get_db()
        rows = conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        logger.error("[sales] Erreur SQL : %s", exc)
        return jsonify({"error": "internal_error", "message": "Erreur lors de la lecture des données."}), 500

    data = rows_to_dicts(rows)
    return jsonify({"count": len(data), "results": data}), 200


# --------------------------------------------------------------------------
# Gestion des erreurs génériques
# --------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "not_found", "message": "Ressource inconnue."}), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify({"error": "method_not_allowed", "message": "Méthode HTTP non autorisée."}), 405


if __name__ == "__main__":
    if not DB_PATH.exists():
        logger.warning(
            "[Démarrage] Base finale introuvable (%s). Exécuter pipeline.py au préalable.",
            DB_PATH,
        )
    app.run(host="127.0.0.1", port=5000, debug=False)
