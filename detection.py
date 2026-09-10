import os

import pandas as pd
import geopandas as gpd
import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN

# ─────────────────────────────────────────────────────────────────────────────
# CONNEXION A POSTGIS
# Les identifiants ne sont plus ecrits en dur ici : ils viennent du .env,
# le meme fichier que celui utilise par l'API (main.py).
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    raise RuntimeError("DATABASE_URL manquant dans .env")

engine = create_engine(DB_URL)


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION DES DONNEES DEPUIS POSTGIS
# ─────────────────────────────────────────────────────────────────────────────

query = """
    SELECT
        p.id_parcelle,
        p.nicad,
        p.superficie_officielle,
        ST_Area(p.geom)                              AS superficie_reelle,
        COUNT(t.id_transaction)                      AS nb_transactions,
        COUNT(DISTINCT t.id_vendeur)                 AS nb_vendeurs,
        COALESCE(MAX(t.montant) - MIN(t.montant), 0) AS ecart_prix,
        COUNT(tf.id_titre)                           AS nb_titres,
        COUNT(a.id_alerte)                           AS nb_alertes_existantes,
        p.geom
    FROM parcelle p
    LEFT JOIN transaction_fonciere t  ON t.id_parcelle  = p.id_parcelle
    LEFT JOIN titre_foncier tf        ON tf.id_parcelle = p.id_parcelle
    LEFT JOIN alerte a                ON a.id_parcelle  = p.id_parcelle
    GROUP BY p.id_parcelle, p.nicad, p.superficie_officielle, p.geom
"""

gdf = gpd.read_postgis(query, engine, geom_col='geom')

print("Données extraites avec succès")
print(f"Nombre de parcelles : {len(gdf)}")

if gdf.empty:
    raise SystemExit("Aucune parcelle en base — rien a analyser.")

print(gdf[['nicad', 'superficie_officielle', 'superficie_reelle',
           'nb_transactions', 'nb_titres', 'nb_alertes_existantes']])


# ─────────────────────────────────────────────────────────────────────────────
# NORMALISATION
# ─────────────────────────────────────────────────────────────────────────────

features = [
    'superficie_officielle',
    'superficie_reelle',
    'nb_transactions',
    'nb_vendeurs',
    'ecart_prix',
    'nb_titres',
    'nb_alertes_existantes'
]

X = gdf[features].fillna(0)

scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

print("Données normalisées avec succès")
print(f"Dimensions : {X_scaled.shape}")


# ─────────────────────────────────────────────────────────────────────────────
# ISOLATION FOREST
# contamination='auto' laisse le modele estimer lui-meme la proportion
# d'anomalies a partir des donnees, plutot que d'imposer arbitrairement
# 30% de parcelles suspectes quel que soit le jeu de donnees analyse.
# ─────────────────────────────────────────────────────────────────────────────

model = IsolationForest(
    n_estimators=100,
    contamination='auto',
    random_state=42
)

gdf['anomalie'] = model.fit_predict(X_scaled)
gdf['score_risque'] = model.score_samples(X_scaled)

score_min = gdf['score_risque'].min()
score_max = gdf['score_risque'].max()
denominateur = score_max - score_min

if denominateur == 0:
    gdf['score_risque_norm'] = 0.0
else:
    gdf['score_risque_norm'] = 1 - (gdf['score_risque'] - score_min) / denominateur

print("\nRésultats Isolation Forest :")
print(gdf[['nicad', 'anomalie', 'score_risque_norm']]
      .sort_values('score_risque_norm', ascending=False))


# ─────────────────────────────────────────────────────────────────────────────
# DBSCAN — CLUSTERING SPATIAL DES PARCELLES SUSPECTES
# ─────────────────────────────────────────────────────────────────────────────

suspects = gdf[gdf['anomalie'] == -1].copy()

print(f"\nNombre de parcelles suspectes : {len(suspects)}")

if len(suspects) >= 2:

    coords = np.array([
        [geom.centroid.x, geom.centroid.y]
        for geom in suspects.geometry
    ])

    db = DBSCAN(
        eps=2000,
        min_samples=2,
        metric='euclidean'
    ).fit(coords)

    suspects['cluster'] = db.labels_

    print("\nZones à risque identifiées :")
    print(suspects[['nicad', 'score_risque_norm', 'cluster']]
          .sort_values('score_risque_norm', ascending=False))

else:
    # DBSCAN a besoin d'au moins min_samples points pour former un cluster :
    # avec 0 ou 1 parcelle suspecte, on saute le clustering plutot que de
    # planter sur un tableau de coordonnees vide ou trop court.
    suspects['cluster'] = -1
    print("Pas assez de parcelles suspectes pour un clustering spatial (minimum 2).")


# ─────────────────────────────────────────────────────────────────────────────
# INSERTION DES SCORES ML DANS LA TABLE ALERTE
# ─────────────────────────────────────────────────────────────────────────────

suspects_ml = suspects[['id_parcelle', 'score_risque_norm']].copy()

inserted = 0
errors = 0

with engine.connect() as conn:
    for _, row in suspects_ml.iterrows():
        try:
            conn.execute(text("""
                INSERT INTO alerte (type_anomalie, score_risque, description, id_parcelle)
                VALUES (
                    'scoring_ml',
                    :score,
                    'Anomalie detectee par Isolation Forest',
                    :parcelle
                )
            """), {
                "score": round(float(row['score_risque_norm']), 3),
                "parcelle": int(row['id_parcelle'])
            })
            inserted += 1
        except Exception as e:
            print(f"Erreur insertion parcelle {row['id_parcelle']} : {e}")
            errors += 1

    conn.commit()

print(f"\nAlertes ML insérées avec succès : {inserted}, erreurs : {errors}")