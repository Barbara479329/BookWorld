-- ============================================================
-- schema_final.sql — Schéma de la base finale (bookworld_final.db)
-- Projet BookWorld — Pipeline de données
--
-- RGPD : les données brutes (sales_raw.csv) contiennent les colonnes
-- customer_first_name et customer_last_name. Ces données personnelles ne
-- sont pas nécessaires à l'usage final de la base (analyse des ventes par
-- pays / livre / canal) et ne sont donc PAS reprises dans ce schéma ni
-- dans la table fact_sales ci-dessous. Voir la justification détaillée
-- dans le rapport final (section RGPD).
-- ============================================================

-- Note : les FOREIGN KEY ci-dessous documentent les relations (cohérentes
-- avec le schéma ER du rapport final) mais ne sont pas appliquées en
-- contrainte stricte (PRAGMA foreign_keys laissé à OFF, valeur par défaut
-- de SQLite). Certains country_code des ventes brutes (ex. NL) n'ont pas
-- de ligne correspondante dans le référentiel pays source — voir la
-- section « limites connues » du rapport final.

-- ------------------------------------------------------------
-- Tables de référence (dimensions)
-- ------------------------------------------------------------
DROP TABLE IF EXISTS dim_books;
CREATE TABLE dim_books (
    book_id      TEXT PRIMARY KEY,
    book_name    TEXT NOT NULL,
    price_gbp    REAL,
    rating       INTEGER,
    availability TEXT
);

DROP TABLE IF EXISTS dim_countries;
CREATE TABLE dim_countries (
    country_code  TEXT PRIMARY KEY,
    country_name  TEXT NOT NULL,
    currency_code TEXT,
    vat_rate      REAL,
    region        TEXT
);

DROP TABLE IF EXISTS dim_channels;
CREATE TABLE dim_channels (
    channel_code          TEXT PRIMARY KEY,
    channel_name          TEXT NOT NULL,
    channel_group         TEXT,
    acquisition_cost_gbp  REAL
);

DROP TABLE IF EXISTS dim_category_rules;
CREATE TABLE dim_category_rules (
    category_name         TEXT PRIMARY KEY,
    margin_rate            REAL,
    strategic_flag          INTEGER,
    default_channel_code    TEXT,
    FOREIGN KEY (default_channel_code) REFERENCES dim_channels (channel_code)
);

-- ------------------------------------------------------------
-- Table de faits — ventes
--
-- RGPD : ne contient volontairement ni customer_first_name, ni
-- customer_last_name (présents dans sales_raw.csv mais exclus ici),
-- ni aucun autre identifiant client. La granularité reste la commande,
-- mais sans lien possible vers une personne physique.
-- ------------------------------------------------------------
DROP TABLE IF EXISTS fact_sales;
CREATE TABLE fact_sales (
    order_id       TEXT PRIMARY KEY,
    order_date     TEXT,
    book_id        TEXT,
    country_code   TEXT,
    channel_code   TEXT,
    quantity       INTEGER NOT NULL,
    discount_rate  REAL DEFAULT 0,
    revenue_gbp    REAL,
    revenue_eur    REAL,
    FOREIGN KEY (book_id)      REFERENCES dim_books (book_id),
    FOREIGN KEY (country_code) REFERENCES dim_countries (country_code),
    FOREIGN KEY (channel_code) REFERENCES dim_channels (channel_code)
);

-- ------------------------------------------------------------
-- Table agrégée — jeu de données final exploitable
-- ------------------------------------------------------------
DROP TABLE IF EXISTS agg_sales_by_country;
CREATE TABLE agg_sales_by_country (
    country_code        TEXT PRIMARY KEY,
    country_name        TEXT,
    total_orders         INTEGER,
    total_quantity        INTEGER,
    total_revenue_gbp      REAL,
    total_revenue_eur      REAL,
    FOREIGN KEY (country_code) REFERENCES dim_countries (country_code)
);
