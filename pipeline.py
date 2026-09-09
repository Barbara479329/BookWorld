"""
Pipeline de données BookWorld
==============================

Étape 1.1 — Extraction des données depuis les différentes sources :
    - fichier CSV des ventes (sales_raw.csv)
    - base de données SQLite de référence (bookworld_reference.db)
    - scraping de la première page du catalogue (https://books.toscrape.com/)
    - taux de change via l'API publique Frankfurter (livres -> euro)

Chaque fonction d'extraction est isolée, gère ses propres erreurs et renvoie
un objet "vide mais typé" (DataFrame vide / dict vide) en cas d'échec, afin
que le pipeline puisse continuer à s'exécuter sur les autres sources plutôt
que de s'arrêter brutalement.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

SALES_CSV_PATH = BASE_DIR / "sales_raw.csv"
REFERENCE_DB_PATH = BASE_DIR / "bookworld_reference.db"
CATALOGUE_URL = "https://books.toscrape.com/"
FRANKFURTER_API_URL = "https://api.frankfurter.app/latest"
SCHEMA_SQL_PATH = BASE_DIR / "schema_final.sql"
FINAL_DB_PATH = BASE_DIR / "bookworld_final.db"

REQUEST_TIMEOUT = 10  # secondes

# RGPD : colonnes personnelles présentes dans sales_raw.csv mais qui ne
# doivent JAMAIS être écrites dans la base finale (voir schema_final.sql
# et la justification dans le rapport final).
PII_COLUMNS = ["customer_first_name", "customer_last_name"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("bookworld_pipeline")


# --------------------------------------------------------------------------
# 1. Extraction CSV — ventes
# --------------------------------------------------------------------------
def extract_sales_csv(csv_path: Path = SALES_CSV_PATH) -> pd.DataFrame:
    """Lit le fichier CSV des ventes brutes.

    Retourne un DataFrame vide en cas d'échec (fichier manquant, CSV
    malformé, etc.) afin de ne pas interrompre le reste du pipeline.
    """
    try:
        df = pd.read_csv(csv_path)
        logger.info("[CSV] %s lignes extraites depuis '%s'", len(df), csv_path.name)
        return df
    except FileNotFoundError:
        logger.error("[CSV] Fichier introuvable : %s", csv_path)
    except pd.errors.EmptyDataError:
        logger.error("[CSV] Fichier vide : %s", csv_path)
    except pd.errors.ParserError as exc:
        logger.error("[CSV] Erreur de parsing pour %s : %s", csv_path, exc)
    return pd.DataFrame()


# --------------------------------------------------------------------------
# 2. Extraction SQLite — données de référence
# --------------------------------------------------------------------------
# Requêtes utilisées pour l'extraction (version finale regroupée dans
# queries.sql). On ne conserve ici que les enregistrements actifs
# (is_active = 1) pour ne travailler qu'avec des référentiels à jour.
REFERENCE_QUERIES = {
    "channels": "SELECT * FROM channels WHERE is_active = 1;",
    # Table complète (pas de filtre is_active) : les country_code des
    # ventes doivent tous pouvoir être rattachés à un pays de référence,
    # même si ce pays est actuellement marqué inactif (ex. PT).
    "countries": "SELECT * FROM countries;",
    # Requête d'enrichissement : associe chaque catégorie à son canal de
    # vente par défaut (utile pour rattacher, plus tard, une commande de
    # vente à une marge et à un canal de référence).
    "category_rules": """
        SELECT
            cr.category_name,
            cr.margin_rate,
            cr.strategic_flag,
            cr.default_channel_code,
            ch.channel_name,
            ch.channel_group
        FROM category_rules AS cr
        JOIN channels AS ch
            ON cr.default_channel_code = ch.channel_code
        WHERE cr.is_active = 1;
    """,
}


def extract_reference_sqlite(db_path: Path = REFERENCE_DB_PATH) -> dict[str, pd.DataFrame]:
    """Extrait les tables de référence depuis la base SQLite.

    Retourne un dict {nom_table: DataFrame}. Une table en échec renvoie un
    DataFrame vide sans bloquer l'extraction des autres tables.
    """
    tables: dict[str, pd.DataFrame] = {}

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        logger.error("[SQLite] Connexion impossible à %s : %s", db_path, exc)
        return {name: pd.DataFrame() for name in REFERENCE_QUERIES}

    try:
        for name, query in REFERENCE_QUERIES.items():
            try:
                tables[name] = pd.read_sql_query(query, conn)
                logger.info("[SQLite] Table '%s' extraite (%s lignes)", name, len(tables[name]))
            except (sqlite3.Error, pd.errors.DatabaseError) as exc:
                logger.error("[SQLite] Erreur sur la requête '%s' : %s", name, exc)
                tables[name] = pd.DataFrame()
    finally:
        conn.close()

    return tables


# --------------------------------------------------------------------------
# 3. Scraping — catalogue de livres (première page)
# --------------------------------------------------------------------------
RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def scrape_catalogue(url: str = CATALOGUE_URL) -> pd.DataFrame:
    """Scrape la première page du catalogue books.toscrape.com.

    Le book_id est reconstruit à partir du rang d'affichage sur la page
    (1 à 20), au format 'BOOKxxxxx', afin de correspondre aux identifiants
    utilisés dans sales_raw.csv (BOOK00001 = 1er livre de la page, etc.).
    """
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.error("[Scraping] Impossible d'accéder à %s : %s", url, exc)
        return pd.DataFrame()

    soup = BeautifulSoup(response.text, "html.parser")
    articles = soup.select("article.product_pod")

    if not articles:
        logger.warning("[Scraping] Aucun livre trouvé sur %s (structure HTML modifiée ?)", url)
        return pd.DataFrame()

    books = []
    for rank, article in enumerate(articles, start=1):
        try:
            title = article.h3.a["title"]

            price_tag = article.select_one("p.price_color")
            price_text = price_tag.get_text(strip=True) if price_tag else ""
            price_gbp = float(price_text.replace("£", "").replace("Â", "").strip())

            availability_tag = article.select_one("p.instock.availability")
            availability = availability_tag.get_text(strip=True) if availability_tag else None

            rating_tag = article.select_one("p.star-rating")
            rating_classes = rating_tag.get("class", []) if rating_tag else []
            rating_word = next((c for c in rating_classes if c in RATING_WORDS), None)
            rating = RATING_WORDS.get(rating_word)

            relative_link = article.h3.a["href"]
            product_url = urljoin(url, relative_link)

            books.append(
                {
                    "book_id": f"BOOK{rank:05d}",
                    "book_name": title,
                    "price_gbp": price_gbp,
                    "availability": availability,
                    "rating": rating,
                    "product_url": product_url,
                }
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("[Scraping] Livre au rang %s ignoré (erreur : %s)", rank, exc)
            continue

    logger.info("[Scraping] %s livres extraits depuis '%s'", len(books), url)
    return pd.DataFrame(books)


# --------------------------------------------------------------------------
# 4. API publique — taux de change (Frankfurter)
# --------------------------------------------------------------------------
def extract_exchange_rates(
    base_currency: str = "GBP",
    target_currencies: list[str] | None = None,
) -> dict:
    """Récupère les taux de change depuis l'API publique Frankfurter.

    Les livres du catalogue sont affichés en GBP (£) ; on récupère donc par
    défaut le taux GBP -> EUR pour pouvoir convertir les prix.
    Retourne un dict {devise_cible: taux} (vide en cas d'échec).
    """
    if target_currencies is None:
        target_currencies = ["EUR"]

    params = {"from": base_currency, "to": ",".join(target_currencies)}

    try:
        response = requests.get(FRANKFURTER_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        logger.error("[API Frankfurter] Erreur réseau/HTTP : %s", exc)
        return {}
    except ValueError as exc:
        logger.error("[API Frankfurter] Réponse JSON invalide : %s", exc)
        return {}

    rates = data.get("rates", {})
    if not rates:
        logger.warning("[API Frankfurter] Aucun taux renvoyé pour base=%s", base_currency)
    else:
        logger.info("[API Frankfurter] Taux récupérés (base %s) : %s", base_currency, rates)

    return rates


# --------------------------------------------------------------------------
# 5. Nettoyage
# --------------------------------------------------------------------------
def clean_sales(sales_df: pd.DataFrame) -> pd.DataFrame:
    """Nettoyage simple des ventes brutes :
    suppression des doublons de commande, des lignes sans book_id /
    country_code / quantity, et typage des colonnes numériques."""
    if sales_df.empty:
        return sales_df

    df = sales_df.copy()
    before = len(df)

    df = df.drop_duplicates(subset="order_id")
    df = df.dropna(subset=["order_id", "book_id", "country_code", "quantity"])

    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["discount_rate"] = pd.to_numeric(df["discount_rate"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["quantity"])
    df = df[df["quantity"] > 0]
    df["discount_rate"] = df["discount_rate"].clip(lower=0.0, upper=1.0)

    logger.info("[Nettoyage] %s -> %s lignes de ventes après nettoyage", before, len(df))
    return df


# --------------------------------------------------------------------------
# 6. Enrichissement — jointure ventes / catalogue / pays / taux de change
# --------------------------------------------------------------------------
def build_sales_enriched(
    sales_df: pd.DataFrame,
    catalogue_df: pd.DataFrame,
    countries_df: pd.DataFrame,
    exchange_rates: dict,
) -> pd.DataFrame:
    """Enrichit chaque ligne de vente avec le prix du livre (catalogue),
    le nom du pays (référentiel SQLite) et le chiffre d'affaires calculé
    en GBP puis en EUR."""
    if sales_df.empty:
        logger.warning("[Enrichissement] Aucune vente à enrichir")
        return pd.DataFrame()

    df = sales_df.copy()

    # Prix par livre (catalogue scrapé)
    if not catalogue_df.empty:
        df = df.merge(catalogue_df[["book_id", "price_gbp"]], on="book_id", how="left")
    else:
        logger.warning("[Enrichissement] Catalogue vide : price_gbp sera manquant")
        df["price_gbp"] = pd.NA

    missing_price = df["price_gbp"].isna().sum()
    if missing_price:
        logger.warning("[Enrichissement] %s lignes sans prix catalogue (book_id inconnu)", missing_price)

    # Nom du pays (référentiel SQLite)
    if not countries_df.empty:
        df = df.merge(
            countries_df[["country_code", "country_name"]],
            on="country_code",
            how="left",
        )
    else:
        logger.warning("[Enrichissement] Référentiel pays vide : country_name sera manquant")
        df["country_name"] = pd.NA

    # Chiffre d'affaires par ligne : quantité x prix x (1 - remise)
    df["revenue_gbp"] = df["quantity"] * df["price_gbp"] * (1 - df["discount_rate"])

    # Conversion en euros via le taux GBP -> EUR de l'API Frankfurter
    gbp_to_eur = exchange_rates.get("EUR")
    if gbp_to_eur is None:
        logger.warning("[Enrichissement] Taux GBP->EUR indisponible : revenue_eur sera manquant")
        df["revenue_eur"] = pd.NA
    else:
        df["revenue_eur"] = df["revenue_gbp"] * gbp_to_eur

    return df


# --------------------------------------------------------------------------
# 7. Agrégation finale — sales_by_country
# --------------------------------------------------------------------------
def aggregate_sales_by_country(sales_enriched: pd.DataFrame) -> pd.DataFrame:
    """Construit l'agrégation sales_by_country :
    country_code, country_name, total_orders, total_quantity,
    total_revenue_gbp, total_revenue_eur."""
    if sales_enriched.empty:
        logger.warning("[Agrégation] Rien à agréger (jeu de données enrichi vide)")
        return pd.DataFrame(
            columns=[
                "country_code",
                "country_name",
                "total_orders",
                "total_quantity",
                "total_revenue_gbp",
                "total_revenue_eur",
            ]
        )

    grouped = (
        sales_enriched.groupby(["country_code", "country_name"], dropna=False)
        .agg(
            total_orders=("order_id", "nunique"),
            total_quantity=("quantity", "sum"),
            total_revenue_gbp=("revenue_gbp", "sum"),
            total_revenue_eur=("revenue_eur", "sum"),
        )
        .reset_index()
        .sort_values("total_revenue_eur", ascending=False)
        .reset_index(drop=True)
    )

    grouped["total_revenue_gbp"] = grouped["total_revenue_gbp"].round(2)
    grouped["total_revenue_eur"] = grouped["total_revenue_eur"].round(2)

    logger.info("[Agrégation] sales_by_country construit : %s pays", len(grouped))
    return grouped


# --------------------------------------------------------------------------
# 8. Base finale — création + alimentation (RGPD by design)
# --------------------------------------------------------------------------
# Colonnes de fact_sales, dans l'ordre du schéma : volontairement limitées
# aux clés étrangères et aux mesures. Les colonnes personnelles (PII_COLUMNS)
# et les colonnes redondantes (book_name, déjà dans dim_books) sont
# explicitement exclues via ce whitelist plutôt que par simple `drop`, afin
# qu'un futur ajout de colonne dans sales_enriched ne puisse pas être écrit
# en base par accident.
FACT_SALES_COLUMNS = [
    "order_id",
    "order_date",
    "book_id",
    "country_code",
    "channel_code",
    "quantity",
    "discount_rate",
    "revenue_gbp",
    "revenue_eur",
]
DIM_BOOKS_COLUMNS = ["book_id", "book_name", "price_gbp", "rating", "availability"]
DIM_COUNTRIES_COLUMNS = ["country_code", "country_name", "currency_code", "vat_rate", "region"]
DIM_CHANNELS_COLUMNS = ["channel_code", "channel_name", "channel_group", "acquisition_cost_gbp"]
DIM_CATEGORY_RULES_COLUMNS = ["category_name", "margin_rate", "strategic_flag", "default_channel_code"]
AGG_SALES_BY_COUNTRY_COLUMNS = [
    "country_code",
    "country_name",
    "total_orders",
    "total_quantity",
    "total_revenue_gbp",
    "total_revenue_eur",
]


def build_final_database(
    sales_enriched: pd.DataFrame,
    catalogue_df: pd.DataFrame,
    reference_tables: dict,
    sales_by_country: pd.DataFrame,
    db_path: Path = FINAL_DB_PATH,
    schema_path: Path = SCHEMA_SQL_PATH,
) -> Path | None:
    """Crée (ou recrée) la base finale SQLite à partir de schema_final.sql
    et l'alimente avec les jeux de données produits par le pipeline.

    RGPD : customer_first_name / customer_last_name (présents dans
    sales_raw.csv) ne sont jamais lus depuis sales_enriched pour
    l'insertion — seules les colonnes whitelistées ci-dessus sont écrites.
    """
    present_pii = [c for c in PII_COLUMNS if c in sales_enriched.columns]
    if present_pii:
        logger.info(
            "[RGPD] Colonnes personnelles détectées dans les données sources et "
            "exclues de la base finale : %s",
            present_pii,
        )

    try:
        schema_sql = schema_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("[Base finale] Schéma introuvable : %s", schema_path)
        return None

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        logger.error("[Base finale] Connexion impossible à %s : %s", db_path, exc)
        return None

    try:
        conn.executescript(schema_sql)
        logger.info("[Base finale] Schéma appliqué depuis '%s'", schema_path.name)

        def _insert(df: pd.DataFrame, table: str, columns: list[str]) -> None:
            if df is None or df.empty:
                logger.warning("[Base finale] '%s' non alimentée (données vides)", table)
                return
            payload = df.reindex(columns=columns)
            try:
                payload.to_sql(table, conn, if_exists="append", index=False)
                logger.info("[Base finale] %s lignes insérées dans '%s'", len(payload), table)
            except sqlite3.Error as exc:
                logger.error("[Base finale] Erreur d'insertion dans '%s' : %s", table, exc)

        _insert(catalogue_df, "dim_books", DIM_BOOKS_COLUMNS)
        _insert(reference_tables.get("countries"), "dim_countries", DIM_COUNTRIES_COLUMNS)
        _insert(reference_tables.get("channels"), "dim_channels", DIM_CHANNELS_COLUMNS)
        _insert(reference_tables.get("category_rules"), "dim_category_rules", DIM_CATEGORY_RULES_COLUMNS)
        _insert(sales_enriched, "fact_sales", FACT_SALES_COLUMNS)  # RGPD : whitelist, pas de PII
        _insert(sales_by_country, "agg_sales_by_country", AGG_SALES_BY_COUNTRY_COLUMNS)

        conn.commit()
    finally:
        conn.close()

    logger.info("[Base finale] Base finale écrite dans '%s'", db_path)
    return db_path


# --------------------------------------------------------------------------
# Point d'entrée du pipeline
# --------------------------------------------------------------------------
def run_pipeline() -> dict:
    """Exécute l'ensemble des extractions, le nettoyage, l'enrichissement
    et l'agrégation finale, et renvoie tous les jeux de données produits."""
    logger.info("=== Démarrage du pipeline d'extraction BookWorld ===")

    sales_df = extract_sales_csv()
    reference_tables = extract_reference_sqlite()
    catalogue_df = scrape_catalogue()
    exchange_rates = extract_exchange_rates(base_currency="GBP", target_currencies=["EUR"])

    logger.info(
        "=== Extraction terminée : %s ventes | %s livres catalogués | taux GBP->EUR = %s ===",
        len(sales_df),
        len(catalogue_df),
        exchange_rates.get("EUR", "N/A"),
    )

    sales_clean = clean_sales(sales_df)
    sales_enriched = build_sales_enriched(
        sales_clean,
        catalogue_df,
        reference_tables.get("countries", pd.DataFrame()),
        exchange_rates,
    )
    sales_by_country = aggregate_sales_by_country(sales_enriched)

    final_db_path = build_final_database(
        sales_enriched, catalogue_df, reference_tables, sales_by_country
    )

    logger.info("=== Pipeline terminé ===")

    return {
        "sales": sales_df,
        "reference": reference_tables,
        "catalogue": catalogue_df,
        "exchange_rates": exchange_rates,
        "sales_enriched": sales_enriched,
        "sales_by_country": sales_by_country,
        "final_db_path": final_db_path,
    }


if __name__ == "__main__":
    result = run_pipeline()
    # Sauvegarde du jeu de données final exploitable
    output_path = BASE_DIR / "sales_by_country.csv"
    result["sales_by_country"].to_csv(output_path, index=False)
    logger.info("[Sauvegarde] sales_by_country.csv écrit dans %s", output_path)
