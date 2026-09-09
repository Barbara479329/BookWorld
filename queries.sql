-- ============================================================
-- queries.sql — Requêtes d'extraction (base bookworld_reference.db)
-- Projet BookWorld — Pipeline de données
-- ============================================================

-- ------------------------------------------------------------
-- 1) Requête avec filtre
--    Canaux de vente actifs uniquement.
-- ------------------------------------------------------------
SELECT
    channel_code,
    channel_name,
    acquisition_cost_gbp,
    channel_group
FROM channels
WHERE is_active = 1;

-- ------------------------------------------------------------
-- 1 bis) Requête avec filtre
--    Pays actifs uniquement (référentiel géographique/fiscal à jour).
-- ------------------------------------------------------------
SELECT
    country_code,
    country_name,
    currency_code,
    vat_rate,
    region
FROM countries
WHERE is_active = 1;

-- ------------------------------------------------------------
-- 2) Requête d'enrichissement
--    Associe chaque catégorie de livre à son canal de vente par
--    défaut, en joignant category_rules et channels.
--    Utile pour enrichir chaque commande (une fois sa catégorie
--    connue) avec la marge attendue, son caractère stratégique et
--    les caractéristiques du canal auquel elle est habituellement
--    rattachée.
-- ------------------------------------------------------------
SELECT
    cr.category_name,
    cr.margin_rate,
    cr.strategic_flag,
    cr.default_channel_code,
    ch.channel_name,
    ch.channel_group,
    ch.acquisition_cost_gbp
FROM category_rules AS cr
JOIN channels AS ch
    ON cr.default_channel_code = ch.channel_code
WHERE cr.is_active = 1
  AND ch.is_active = 1;

-- ------------------------------------------------------------
-- 2 bis) Requête d'enrichissement
--    Répartition des pays actifs par devise, utile pour ne
--    demander à l'API de taux de change que les devises
--    réellement présentes dans le référentiel.
-- ------------------------------------------------------------
SELECT
    currency_code,
    COUNT(*) AS nb_pays,
    GROUP_CONCAT(country_code) AS pays
FROM countries
WHERE is_active = 1
GROUP BY currency_code;
