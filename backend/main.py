import os
import secrets
import time
import tempfile
import zipfile
import logging

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import bcrypt
# NOTE : on utilise bcrypt directement plutot que passlib. passlib (derniere
# publication en 2020) a un bug de compatibilite bien documente avec les
# versions recentes de bcrypt (>=4.1) : `AttributeError: module 'bcrypt' has
# no attribute '__about__'`. bcrypt seul evite completement ce probleme et a
# une API suffisante pour notre besoin (hash + verification).

import geopandas as gpd
import pandas as pd

from sqlalchemy import create_engine, text

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING (remplace les print() — indispensable pour debugger en prod)
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sdfcs")

# ─────────────────────────────────────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
AUTH_USER = os.getenv("AUTH_USER", "admin")
AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH")  # hash bcrypt, jamais le mot de passe en clair
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

ALLOWED = os.getenv(
    "ALLOWED_ORIGINS",
    "http://127.0.0.1:5501,http://localhost:5501",
).split(",")

if not DB_URL:
    raise RuntimeError("DATABASE_URL manquant dans .env")

if not AUTH_PASSWORD_HASH:
    raise RuntimeError(
        "AUTH_PASSWORD_HASH manquant dans .env "
        "(génère-le avec generate_password_hash.py, ne mets jamais le mot de passe en clair)"
    )


def verify_password(plain_password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Hash malforme dans le .env (mauvais format, copie incomplete...) :
        # on ne plante jamais le serveur pour ca, on refuse juste la connexion.
        logger.error("AUTH_PASSWORD_HASH invalide ou mal forme dans .env")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
    # Force les messages d'erreur du serveur PostgreSQL en anglais (ASCII).
    # Sans ca, un PostgreSQL configure en francais (lc_messages) renvoie des
    # messages accentues que psycopg2 peut echouer a decoder en UTF-8,
    # provoquant un UnicodeDecodeError qui masque completement la vraie
    # erreur (mauvais mot de passe, base inexistante, etc.).
    connect_args={"options": "-c lc_messages=C"},
)

try:
    with engine.connect() as _test_conn:
        _test_conn.execute(text("SELECT 1"))
    logger.info("Connexion PostGIS OK (%s)", DB_URL.split("@")[-1])
except Exception:
    # On ne bloque pas le demarrage (utile pour /health), mais on previent
    # tres clairement dans les logs : c'est la cause la plus frequente de
    # dashboard qui reste a zero.
    logger.exception(
        "ECHEC DE CONNEXION A LA BASE — verifie DATABASE_URL dans .env "
        "(utilisateur, mot de passe, port, nom de la base)"
    )

# ─────────────────────────────────────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="SDFCS API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


# Filet de securite global : si une route leve une exception non prevue,
# on renvoie une reponse JSON propre (avec les en-tetes CORS, puisque
# CORSMiddleware enveloppe aussi ce handler) au lieu de laisser passer un
# comportement indefini. Sans ca, une erreur inattendue dans une route
# peut ressembler a tort a un probleme CORS cote navigateur.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Erreur non geree sur %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur"},
    )


# Route de diagnostic, sans authentification : permet de verifier que le
# serveur repond bien, y compris en tapant l'URL directement dans le
# navigateur (donc sans passer par fetch/CORS). Si cette route ne repond
# pas, le probleme est cote serveur (pas demarre, plante, mauvais port) et
# non lie a CORS.
@app.get("/health")
def health():
    return {"ok": True, "service": "sdfcs-api"}

# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTIFICATION
# ─────────────────────────────────────────────────────────────────────────────

# NOTE PROD : un dict en mémoire ne survit pas à un redémarrage et ne
# fonctionne pas avec plusieurs workers Uvicorn. Pour un déploiement multi-
# worker, remplacer par Redis (ou une table `session` en base).
SESSIONS: dict[str, dict] = {}
SESSION_DURATION = 60 * 60 * 8  # 8 heures

# Anti brute-force minimal : compteur d'échecs par IP.
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
MAX_ATTEMPTS = 5
ATTEMPTS_WINDOW = 300  # 5 minutes


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"created": time.time()}
    return token


def verify_session(request: Request) -> bool:
    token = request.cookies.get("sdfcs_session")

    if not token:
        raise HTTPException(status_code=401, detail="Authentification requise")

    session = SESSIONS.get(token)

    if not session:
        raise HTTPException(status_code=401, detail="Session invalide")

    if time.time() - session["created"] > SESSION_DURATION:
        del SESSIONS[token]
        raise HTTPException(status_code=401, detail="Session expiree")

    return True


def verify_key(session: bool = Depends(verify_session)) -> bool:
    return True


def check_rate_limit(ip: str):
    now = time.time()
    attempts = [t for t in LOGIN_ATTEMPTS.get(ip, []) if now - t < ATTEMPTS_WINDOW]
    LOGIN_ATTEMPTS[ip] = attempts

    if len(attempts) >= MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Trop de tentatives de connexion, reessayez plus tard",
        )


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class StatutBody(BaseModel):
    statut: str


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN / LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/auth/login")
async def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)

    valid = body.username == AUTH_USER and verify_password(
        body.password, AUTH_PASSWORD_HASH
    )

    if not valid:
        LOGIN_ATTEMPTS.setdefault(ip, []).append(time.time())
        raise HTTPException(status_code=401, detail="Identifiants invalides")

    token = create_session()
    response = JSONResponse({"ok": True, "message": "Connexion reussie"})
    response.set_cookie(
        key="sdfcs_session",
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,  # True obligatoire des que le site est servi en HTTPS
        samesite="lax",
        max_age=SESSION_DURATION,
    )
    return response


@app.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get("sdfcs_session")

    if token and token in SESSIONS:
        del SESSIONS[token]

    response = JSONResponse({"ok": True})
    response.delete_cookie("sdfcs_session")
    return response


# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/stats")
def get_stats(key: bool = Depends(verify_key)):
    try:
        with engine.connect() as c:
            return {
                "total_alertes": c.execute(text("SELECT COUNT(*) FROM alerte")).scalar(),
                "critiques": c.execute(
                    text("SELECT COUNT(*) FROM alerte WHERE score_risque >= 0.8")
                ).scalar(),
                "chevauchements": c.execute(
                    text("SELECT COUNT(*) FROM alerte WHERE type_anomalie = 'chevauchement'")
                ).scalar(),
                "zones_illegales": c.execute(
                    text("SELECT COUNT(*) FROM alerte WHERE type_anomalie = 'zone_illegale'")
                ).scalar(),
                "nb_parcelles": c.execute(text("SELECT COUNT(*) FROM parcelle")).scalar(),
            }
    except Exception:
        logger.exception("Erreur /stats")
        raise HTTPException(500, "Erreur lors de la lecture des statistiques")


# ─────────────────────────────────────────────────────────────────────────────
# ALERTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/alertes")
def get_alertes(key: bool = Depends(verify_key)):
    try:
        with engine.connect() as c:
            rows = c.execute(text("""
                SELECT
                    a.id_alerte, a.type_anomalie,
                    CAST(a.score_risque AS FLOAT) AS score_risque,
                    a.description, a.statut,
                    TO_CHAR(a.date_detection, 'YYYY-MM-DD HH24:MI') AS date_detection,
                    p.nicad
                FROM alerte a
                JOIN parcelle p ON a.id_parcelle = p.id_parcelle
                ORDER BY a.score_risque DESC
            """)).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        logger.exception("Erreur /alertes")
        raise HTTPException(500, "Erreur lors de la lecture des alertes")


@app.patch("/alertes/{id_alerte}/statut")
def patch_statut(id_alerte: int, body: StatutBody, key: bool = Depends(verify_key)):
    statuts_valides = {"ouverte", "en_cours", "resolue", "fausse_alerte"}

    if body.statut not in statuts_valides:
        raise HTTPException(400, "Statut invalide")

    with engine.connect() as c:
        result = c.execute(
            text("UPDATE alerte SET statut = :s WHERE id_alerte = :i"),
            {"s": body.statut, "i": id_alerte},
        )
        c.commit()

    if result.rowcount == 0:
        raise HTTPException(404, "Alerte introuvable")

    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# PARCELLES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/parcelles")
def get_parcelles(key: bool = Depends(verify_key)):
    try:
        with engine.connect() as c:
            rows = c.execute(text("""
                SELECT
                    p.id_parcelle, p.nicad, p.statut,
                    CAST(p.superficie_officielle AS FLOAT) AS superficie_officielle,
                    CAST(ROUND(ST_Area(p.geom)::numeric, 2) AS FLOAT) AS superficie_reelle,
                    CAST(ST_Y(ST_Transform(ST_Centroid(p.geom), 4326)) AS FLOAT) AS lat,
                    CAST(ST_X(ST_Transform(ST_Centroid(p.geom), 4326)) AS FLOAT) AS lon,
                    z.nom AS zone, z.statut_legal,
                    COUNT(a.id_alerte) AS nb_alertes,
                    CAST(COALESCE(MAX(a.score_risque), 0) AS FLOAT) AS score_max
                FROM parcelle p
                JOIN zone_administrative z ON p.id_zone_admin = z.id_zone
                LEFT JOIN alerte a ON a.id_parcelle = p.id_parcelle
                GROUP BY p.id_parcelle, p.nicad, p.statut, p.superficie_officielle,
                         p.geom, z.nom, z.statut_legal
                ORDER BY score_max DESC
            """)).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        logger.exception("Erreur /parcelles")
        raise HTTPException(500, "Erreur lors de la lecture des parcelles")


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT SHAPEFILE
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/import/shapefile")
async def import_shapefile(file: UploadFile = File(...), key: bool = Depends(verify_key)):
    if not file.filename.endswith(".zip"):
        raise HTTPException(400, "Format attendu : ZIP contenant .shp .dbf .prj")

    if file.size and file.size > 50 * 1024 * 1024:
        raise HTTPException(413, "Fichier trop volumineux (max 50 Mo)")

    with tempfile.TemporaryDirectory() as tmp:
        zp = os.path.join(tmp, "upload.zip")

        with open(zp, "wb") as f:
            f.write(await file.read())

        try:
            with zipfile.ZipFile(zp, "r") as z:
                z.extractall(tmp)
        except zipfile.BadZipFile:
            raise HTTPException(400, "Fichier ZIP invalide ou corrompu")

        shps = [f for f in os.listdir(tmp) if f.endswith(".shp")]

        if not shps:
            raise HTTPException(400, "Aucun .shp trouve dans le ZIP")

        gdf = gpd.read_file(os.path.join(tmp, shps[0]))

        if gdf.crs is None:
            raise HTTPException(400, "Projection manquante dans le shapefile")

        gdf = gdf.to_crs(epsg=32628)

        inserted = 0
        errors = 0

        with engine.connect() as c:
            zone_id = c.execute(
                text("SELECT id_zone FROM zone_administrative LIMIT 1")
            ).scalar()

            if not zone_id:
                raise HTTPException(400, "Aucune zone administrative en base")

            for idx, row in gdf.iterrows():
                try:
                    nicad = str(row.get("nicad") or row.get("NICAD") or f"IMP-{idx+1:04d}")
                    sup = float(row.get("superficie") or row.get("SUPERFICIE") or row.geometry.area)

                    result = c.execute(
                        text("""
                            INSERT INTO parcelle
                                (nicad, superficie_officielle, statut, geom, id_zone_admin)
                            VALUES
                                (:n, :s, 'active', ST_GeomFromText(:g, 32628), :z)
                            ON CONFLICT (nicad) DO NOTHING
                        """),
                        {"n": nicad, "s": sup, "g": row.geometry.wkt, "z": zone_id},
                    )

                    # Ne compter que les lignes reellement inserees (pas les
                    # conflits ignores par ON CONFLICT DO NOTHING).
                    if result.rowcount > 0:
                        inserted += 1

                except Exception:
                    logger.exception("Echec import parcelle ligne %s", idx)
                    errors += 1

            c.commit()

    return {
        "message": f"{inserted} parcelle(s) importee(s), {errors} erreur(s)",
        "inserees": inserted,
        "erreurs": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT CSV
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {
    "transaction": {"id_parcelle"},
    "proprietaire": {"nom_complet"},
}


@app.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    type_donnee: str = "transaction",
    key: bool = Depends(verify_key),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Fichier CSV requis")

    if type_donnee not in REQUIRED_COLUMNS:
        raise HTTPException(400, "type_donnee invalide")

    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        df = pd.read_csv(tmp_path)

        missing = REQUIRED_COLUMNS[type_donnee] - set(df.columns)
        if missing:
            raise HTTPException(400, f"Colonnes manquantes dans le CSV : {', '.join(missing)}")

        inserted = 0
        errors = 0

        with engine.connect() as c:
            if type_donnee == "transaction":
                for idx, row in df.iterrows():
                    try:
                        c.execute(
                            text("""
                                INSERT INTO transaction_fonciere
                                    (date_transaction, type, montant, id_parcelle, id_vendeur, id_acheteur)
                                VALUES
                                    (:dt, :tp, :mn, :pa, :ve, :ac)
                            """),
                            {
                                "dt": row.get("date_transaction"),
                                "tp": row.get("type", "vente"),
                                "mn": float(row["montant"]) if pd.notna(row.get("montant")) else None,
                                "pa": int(row["id_parcelle"]),
                                "ve": int(row["id_vendeur"]) if pd.notna(row.get("id_vendeur")) else None,
                                "ac": int(row["id_acheteur"]) if pd.notna(row.get("id_acheteur")) else None,
                            },
                        )
                        inserted += 1
                    except Exception:
                        logger.exception("Echec import transaction ligne %s", idx)
                        errors += 1

            elif type_donnee == "proprietaire":
                for idx, row in df.iterrows():
                    try:
                        c.execute(
                            text("""
                                INSERT INTO proprietaire (nom_complet, type, nin, contact)
                                VALUES (:n, :t, :ni, :co)
                                ON CONFLICT (nin) DO NOTHING
                            """),
                            {
                                "n": row.get("nom_complet"),
                                "t": row.get("type", "physique"),
                                "ni": str(row.get("nin", "")),
                                "co": str(row.get("contact", "")),
                            },
                        )
                        inserted += 1
                    except Exception:
                        logger.exception("Echec import proprietaire ligne %s", idx)
                        errors += 1

            c.commit()

        return {
            "message": f"{inserted} enregistrement(s) importe(s), {errors} erreur(s)",
            "inserees": inserted,
            "erreurs": errors,
        }

    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# DETECTION ML
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/detection/run")
def run_detection(key: bool = Depends(verify_key)):
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler

        gdf = gpd.read_postgis("""
            SELECT
                p.id_parcelle, p.nicad, p.superficie_officielle,
                ST_Area(p.geom) AS superficie_reelle,
                COUNT(t.id_transaction) AS nb_transactions,
                COUNT(DISTINCT t.id_vendeur) AS nb_vendeurs,
                COALESCE(MAX(t.montant) - MIN(t.montant), 0) AS ecart_prix,
                COUNT(tf.id_titre) AS nb_titres,
                COUNT(a.id_alerte) AS nb_alertes_existantes,
                p.geom
            FROM parcelle p
            LEFT JOIN transaction_fonciere t ON t.id_parcelle = p.id_parcelle
            LEFT JOIN titre_foncier tf ON tf.id_parcelle = p.id_parcelle
            LEFT JOIN alerte a ON a.id_parcelle = p.id_parcelle
            GROUP BY p.id_parcelle, p.nicad, p.superficie_officielle, p.geom
        """, engine, geom_col="geom")

        if gdf.empty:
            return {"message": "Aucune parcelle a analyser", "alertes_ml": 0}

        features = [
            "superficie_officielle", "superficie_reelle", "nb_transactions",
            "nb_vendeurs", "ecart_prix", "nb_titres", "nb_alertes_existantes",
        ]

        X = gdf[features].fillna(0)
        Xs = StandardScaler().fit_transform(X)

        # 'auto' laisse le modele estimer la proportion d'anomalies a partir
        # des donnees, au lieu d'imposer arbitrairement 30% de parcelles
        # suspectes quelle que soit la realite du jeu de donnees.
        model = IsolationForest(n_estimators=100, contamination="auto", random_state=42)

        gdf["anomalie"] = model.fit_predict(Xs)
        sc = model.score_samples(Xs)
        denominator = sc.max() - sc.min()
        gdf["score_norm"] = 0 if denominator == 0 else (1 - (sc - sc.min()) / denominator)

        suspects = gdf[gdf["anomalie"] == -1]
        inserted = 0

        with engine.connect() as c:
            for _, row in suspects.iterrows():
                c.execute(
                    text("""
                        INSERT INTO alerte (type_anomalie, score_risque, description, id_parcelle)
                        VALUES ('scoring_ml', :s, 'Anomalie detectee par Isolation Forest', :p)
                    """),
                    {"s": round(float(row["score_norm"]), 3), "p": int(row["id_parcelle"])},
                )
                inserted += 1

            c.commit()

        return {
            "message": f"Detection terminee — {inserted} alerte(s) ML generee(s)",
            "alertes_ml": inserted,
        }

    except Exception as e:
        logger.exception("Erreur pendant la detection ML")
        raise HTTPException(500, "Erreur interne pendant la detection") from e


# ─────────────────────────────────────────────────────────────────────────────
# START
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # reload=False volontairement ici : avec reload=True, une erreur de
    # syntaxe ou un import manquant dans un fichier surveille (y compris
    # .env) peut faire planter le worker rechargé sans que le processus
    # parent ne s'arrete franchement, ce qui donne l'impression trompeuse
    # d'un probleme CORS cote navigateur alors que le serveur ne repond
    # simplement plus. Utiliser `uvicorn main:app --reload` en ligne de
    # commande pendant le developpement actif, et surveiller sa console.
    uvicorn.run(app, host="127.0.0.1", port=8000)