# BookWorld — Pipeline de données & API REST

Pipeline de données bout en bout pour l'éditeur/libraire fictif **BookWorld** :
extraction (CSV, SQLite, scraping, API publique) → nettoyage → enrichissement
→ agrégation → base finale (SQLite, conforme RGPD) → API REST de
consultation.

## Sommaire

- [Structure du projet](#structure-du-projet)
- [1. Installer les dépendances](#1-installer-les-dépendances)
- [2. Exécuter le pipeline](#2-exécuter-le-pipeline)
- [3. Lancer l'API](#3-lancer-lapi)
- [4. Authentification](#4-authentification)
- [RGPD](#rgpd)
- [Documentation complète](#documentation-complète)

## Structure du projet

| Fichier / dossier              | Rôle |
|---------------------------------|------|
| `pipeline.py`                   | Script principal : extraction (CSV, SQLite, scraping books.toscrape.com, API Frankfurter), nettoyage, enrichissement, agrégation `sales_by_country`, et construction de la base finale `bookworld_final.db`. |
| `api.py`                        | API REST (Flask) exposant les données de `bookworld_final.db`, protégée par un token. |
| `queries.sql`                   | Requêtes SQL d'extraction depuis `bookworld_reference.db` (requête filtrée + requête d'enrichissement). |
| `schema_final.sql`              | DDL de la base finale (`bookworld_final.db`) : tables, colonnes, relations. Ne déclare aucune colonne personnelle (voir [RGPD](#rgpd)). |
| `sales_raw.csv`                 | Données brutes de ventes (source d'entrée du pipeline). |
| `bookworld_reference.db`        | Base de référence source (canaux, pays, règles de catégories). |
| `requirements.txt`              | Dépendances Python du projet. |
| `.env.example`                  | Exemple de configuration du token d'API (`API_TOKEN`). |
| `docs/rapport_final.docx`       | Rapport final : méthodologie, agrégation, schéma de données, justification RGPD, documentation des endpoints. |
| `docs/schema_final.png`         | Schéma entité-relation de la base finale. |
| `docs/rgpd_comparaison.png`     | Comparaison des colonnes source vs base finale (preuve RGPD). |
| `docs/sales_by_country_sample.csv` | Exemple de sortie de l'agrégation `sales_by_country`. |
| `bookworld_final.db` *(généré)* | Base finale SQLite, produite par `pipeline.py` — non versionnée (voir `.gitignore`), à régénérer localement. |

## 1. Installer les dépendances

Le projet nécessite **Python 3.10+**.

```bash
git clone <url-du-dépôt>
cd bookworld-pipeline

python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate

pip install -r requirements.txt
```

## 2. Exécuter le pipeline

Le pipeline lit `sales_raw.csv` et `bookworld_reference.db` (déjà présents
dans le dépôt), scrape la première page de
[books.toscrape.com](https://books.toscrape.com/) et interroge l'API
publique [Frankfurter](https://www.frankfurter.app/) pour le taux de
change GBP → EUR. Il produit ensuite `sales_by_country.csv` et la base
finale `bookworld_final.db`.

```bash
python pipeline.py
```

Sortie attendue (extrait) :

```
[CSV] 240 lignes extraites depuis 'sales_raw.csv'
[SQLite] Table 'channels' extraite (3 lignes)
[Scraping] 20 livres extraits depuis 'https://books.toscrape.com/'
[API Frankfurter] Taux récupérés (base GBP) : {'EUR': ...}
[Agrégation] sales_by_country construit : 8 pays
[Base finale] Base finale écrite dans 'bookworld_final.db'
```

> Le pipeline continue de s'exécuter même si une source échoue (pas de
> réseau, site indisponible, etc.) : chaque étape d'extraction est isolée
> et loggue l'erreur sans interrompre les autres.

## 3. Lancer l'API

L'API lit `bookworld_final.db` généré à l'étape précédente.

```bash
export API_TOKEN="mon-token-secret"   # voir .env.example
python api.py
```

L'API écoute par défaut sur `http://127.0.0.1:5000`.

| Méthode | Endpoint            | Auth | Description |
|---------|----------------------|------|--------------|
| GET     | `/health`             | Non  | État de l'API et de la connexion à la base finale. |
| GET     | `/sales-by-country`   | Oui  | Agrégation des ventes par pays. |
| GET     | `/books`               | Oui  | Catalogue des livres. |
| GET     | `/countries`           | Oui  | Référentiel pays. |
| GET     | `/sales`               | Oui  | Ventes détaillées, filtrables par `country_code`, `book_id`, `limit`. |

Exemples :

```bash
curl http://127.0.0.1:5000/health

curl -H "X-API-Key: mon-token-secret" http://127.0.0.1:5000/sales-by-country

curl -H "X-API-Key: mon-token-secret" \
     "http://127.0.0.1:5000/sales?country_code=FR&limit=5"
```

La documentation détaillée des endpoints (codes de retour, exemples de
réponses) est dans `docs/rapport_final.docx`, section 7.

## 4. Authentification

Ce projet utilise **deux authentifications distinctes**, à ne pas
confondre :

### 4.1 Authentification à l'API (token applicatif)

L'API REST (`api.py`) est protégée par un **token simple** transmis dans
l'en-tête HTTP `X-API-Key` :

1. Définir le token côté serveur : `export API_TOKEN="mon-token-secret"`
   (ou copier `.env.example` en `.env` et le charger dans votre shell).
2. Envoyer ce même token dans chaque requête (sauf `/health`) :
   `curl -H "X-API-Key: mon-token-secret" ...`
3. Comportement :
   - en-tête absent → `401 Unauthorized`
   - token incorrect → `403 Forbidden`
   - token correct → `200 OK`

⚠️ Sans variable `API_TOKEN` définie, l'API démarre avec un token de
développement par défaut et affiche un avertissement dans les logs — à ne
jamais utiliser tel quel en production.

### 4.2 Authentification à GitHub (dépôt du projet)

Pour cloner, contribuer ou pousser des changements sur le dépôt GitHub du
projet, deux méthodes sont possibles :

**Option A — Jeton d'accès personnel (HTTPS)**

1. Sur GitHub : *Settings → Developer settings → Personal access tokens →
   Generate new token* (droits `repo` suffisants pour un dépôt privé).
2. Cloner avec l'URL HTTPS : `git clone https://github.com/<user>/<repo>.git`
3. Au premier `git push`, saisir votre nom d'utilisateur GitHub et coller
   le token à la place du mot de passe (ou le stocker via
   `git config --global credential.helper store`).

**Option B — Clé SSH**

1. Générer une paire de clés (si vous n'en avez pas) :
   ```bash
   ssh-keygen -t ed25519 -C "votre.email@example.com"
   ```
2. Ajouter la clé publique à GitHub : *Settings → SSH and GPG keys → New
   SSH key*, en y collant le contenu de `~/.ssh/id_ed25519.pub`.
3. Cloner avec l'URL SSH : `git clone git@github.com:<user>/<repo>.git`
4. Vérifier la connexion : `ssh -T git@github.com`

Une fois authentifié (token ou SSH), les commandes `git push` / `git pull`
fonctionnent normalement.

## RGPD

Les colonnes `customer_first_name` et `customer_last_name` présentes dans
`sales_raw.csv` **ne sont pas conservées** dans la base finale
(`bookworld_final.db`) : elles ne sont pas nécessaires à l'usage analytique
du projet (ventes agrégées par pays / livre / canal) et sont exclues dès le
schéma (`schema_final.sql`) et via une whitelist explicite de colonnes dans
`pipeline.py`. La justification complète (principe de minimisation, article
5.1.c du RGPD) et la preuve technique sont détaillées dans
`docs/rapport_final.docx`, section 5.

## Documentation complète

Le rapport `docs/rapport_final.docx` détaille l'ensemble du projet :
sources de données, nettoyage/enrichissement, agrégation `sales_by_country`,
schéma de la base finale, conformité RGPD, limites connues des données, et
documentation de l'API REST.
