import os

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-cache")

import io
import secrets
import time
import tempfile
import zipfile
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import bcrypt

import geopandas as gpd
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

from sqlalchemy import create_engine, text


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
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

AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH")

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

COOKIE_SAMESITE = "none" if COOKIE_SECURE else "lax"

ALLOWED = os.getenv(
    "ALLOWED_ORIGINS",
    "http://127.0.0.1:5501,http://localhost:5501",
).split(",")


def verify_origin(request: Request) -> bool:
    """
    Verification supplementaire contre le CSRF : le cookie de session
    est en SameSite=None en production (front et API sur des domaines
    differents), ce qui desactive la protection SameSite habituelle.
    On verifie donc explicitement l'en-tete Origin sur les routes qui
    modifient des donnees.
    """

    origin = request.headers.get("origin")

    if origin and origin not in ALLOWED:

        raise HTTPException(
            403,
            "Origine non autorisee",
        )

    return True


if not DB_URL:
    raise RuntimeError("DATABASE_URL manquant dans .env")


if not AUTH_PASSWORD_HASH:
    raise RuntimeError(
        "AUTH_PASSWORD_HASH manquant dans .env "
        "(genere-le avec generate_password_hash.py, "
        "ne mets jamais le mot de passe en clair)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# PASSWORD
# ─────────────────────────────────────────────────────────────────────────────

def verify_password(plain_password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed.encode("utf-8"),
        )

    except (ValueError, TypeError):
        logger.error(
            "AUTH_PASSWORD_HASH invalide ou mal forme dans .env"
        )
        return False


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
    connect_args={"options": "-c lc_messages=C"},
)


try:
    with engine.connect() as _test_conn:
        _test_conn.execute(text("SELECT 1"))

    logger.info(
        "Connexion PostGIS OK (%s)",
        DB_URL.split("@")[-1],
    )

except Exception:
    logger.exception(
        "ECHEC DE CONNEXION A LA BASE — "
        "verifie DATABASE_URL dans .env "
        "(utilisateur, mot de passe, port, nom de la base)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="SDFCS API",
    docs_url=None,
    redoc_url=None,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PATCH",
        "OPTIONS",
    ],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL ERROR HANDLER
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Erreur non geree sur %s",
        request.url.path,
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "Erreur interne du serveur"
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "sdfcs-api",
    }


# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTIFICATION
# ─────────────────────────────────────────────────────────────────────────────

SESSION_DURATION = 60 * 60 * 8  # 8 heures


def ensure_sessions_table():
    try:
        with engine.begin() as c:
            c.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        token TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )

            c.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_sessions_created_at
                    ON sessions(created_at)
                    """
                )
            )

        logger.info("Table sessions OK")

    except Exception:
        logger.exception(
            "Impossible de creer/verifier la table sessions"
        )


ensure_sessions_table()


def create_session() -> str:

    token = secrets.token_urlsafe(32)

    with engine.begin() as c:
        c.execute(
            text(
                """
                INSERT INTO sessions (
                    token,
                    created_at
                )
                VALUES (
                    :token,
                    NOW()
                )
                """
            ),
            {
                "token": token
            },
        )

    return token


def verify_session(request: Request) -> bool:

    token = request.cookies.get("sdfcs_session")

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentification requise",
        )

    try:
        with engine.begin() as c:
            session = c.execute(
                text(
                    """
                    SELECT created_at
                    FROM sessions
                    WHERE token = :token
                    """
                ),
                {
                    "token": token
                },
            ).fetchone()

    except Exception:
        logger.exception(
            "Erreur lors de la verification de session"
        )

        raise HTTPException(
            status_code=500,
            detail="Erreur lors de la verification de session",
        )

    if not session:
        raise HTTPException(
            status_code=401,
            detail="Session invalide",
        )

    created_at = session[0]

    if created_at.tzinfo is None:
        created_at = created_at.replace(
            tzinfo=timezone.utc
        )

    age = (
        datetime.now(timezone.utc) - created_at
    ).total_seconds()

    if age > SESSION_DURATION:

        try:
            with engine.begin() as c:
                c.execute(
                    text(
                        """
                        DELETE FROM sessions
                        WHERE token = :token
                        """
                    ),
                    {
                        "token": token
                    },
                )

        except Exception:
            logger.exception(
                "Erreur lors de la suppression "
                "d'une session expiree"
            )

        raise HTTPException(
            status_code=401,
            detail="Session expiree",
        )

    return True


def verify_key(
    session: bool = Depends(verify_session),
) -> bool:
    return True


# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMIT LOGIN
# ─────────────────────────────────────────────────────────────────────────────

LOGIN_ATTEMPTS: dict[str, list[float]] = {}

MAX_ATTEMPTS = 5

ATTEMPTS_WINDOW = 300  # 5 minutes


def check_rate_limit(ip: str):

    now = time.time()

    attempts = [
        t
        for t in LOGIN_ATTEMPTS.get(ip, [])
        if now - t < ATTEMPTS_WINDOW
    ]

    LOGIN_ATTEMPTS[ip] = attempts

    if len(attempts) >= MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=(
                "Trop de tentatives de connexion, "
                "reessayez plus tard"
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str = Field(
        ...,
        min_length=1,
        max_length=100,
    )

    password: str = Field(
        ...,
        min_length=1,
        max_length=200,
    )


class StatutBody(BaseModel):
    statut: str


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/auth/login")
async def login(
    body: LoginBody,
    request: Request,
    _o: bool = Depends(verify_origin),
):

    ip = (
        request.client.host
        if request.client
        else "unknown"
    )

    check_rate_limit(ip)

    valid = (
        body.username == AUTH_USER
        and verify_password(
            body.password,
            AUTH_PASSWORD_HASH,
        )
    )

    if not valid:

        LOGIN_ATTEMPTS.setdefault(
            ip,
            [],
        ).append(time.time())

        raise HTTPException(
            status_code=401,
            detail="Identifiants invalides",
        )

    token = create_session()

    response = JSONResponse(
        {
            "ok": True,
            "message": "Connexion reussie",
        }
    )

    response.set_cookie(
        key="sdfcs_session",
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=SESSION_DURATION,
        path="/",
    )

    return response


# ─────────────────────────────────────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/auth/logout")
def logout(
    request: Request,
    _o: bool = Depends(verify_origin),
):

    token = request.cookies.get(
        "sdfcs_session"
    )

    if token:

        try:
            with engine.begin() as c:
                c.execute(
                    text(
                        """
                        DELETE FROM sessions
                        WHERE token = :token
                        """
                    ),
                    {
                        "token": token
                    },
                )

        except Exception:
            logger.exception(
                "Erreur lors de la suppression "
                "de la session"
            )

    response = JSONResponse(
        {
            "ok": True
        }
    )

    response.delete_cookie(
        "sdfcs_session",
        path="/",
    )

    return response


# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/stats")
def get_stats(
    key: bool = Depends(verify_key),
):

    try:

        with engine.connect() as c:

            return {
                "total_alertes": c.execute(
                    text(
                        "SELECT COUNT(*) FROM alerte"
                    )
                ).scalar(),

                "critiques": c.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM alerte
                        WHERE score_risque >= 0.8
                        """
                    )
                ).scalar(),

                "chevauchements": c.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM alerte
                        WHERE type_anomalie = 'chevauchement'
                        """
                    )
                ).scalar(),

                "zones_illegales": c.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM alerte
                        WHERE type_anomalie = 'zone_illegale'
                        """
                    )
                ).scalar(),

                "nb_parcelles": c.execute(
                    text(
                        "SELECT COUNT(*) FROM parcelle"
                    )
                ).scalar(),
            }

    except Exception:

        logger.exception(
            "Erreur /stats"
        )

        raise HTTPException(
            500,
            "Erreur lors de la lecture des statistiques",
        )


# ─────────────────────────────────────────────────────────────────────────────
# ALERTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/alertes")
def get_alertes(
    key: bool = Depends(verify_key),
):

    try:

        with engine.connect() as c:

            rows = c.execute(
                text(
                    """
                    SELECT
                        a.id_alerte,
                        a.type_anomalie,
                        CAST(a.score_risque AS FLOAT)
                            AS score_risque,
                        a.description,
                        a.statut,
                        TO_CHAR(
                            a.date_detection,
                            'YYYY-MM-DD HH24:MI'
                        ) AS date_detection,
                        p.nicad
                    FROM alerte a
                    JOIN parcelle p
                        ON a.id_parcelle = p.id_parcelle
                    ORDER BY a.score_risque DESC
                    """
                )
            ).fetchall()

        return [
            dict(r._mapping)
            for r in rows
        ]

    except Exception:

        logger.exception(
            "Erreur /alertes"
        )

        raise HTTPException(
            500,
            "Erreur lors de la lecture des alertes",
        )


# ─────────────────────────────────────────────────────────────────────────────
# MODIFICATION STATUT ALERTE
# ─────────────────────────────────────────────────────────────────────────────

@app.patch("/alertes/{id_alerte}/statut")
def patch_statut(
    id_alerte: int,
    body: StatutBody,
    key: bool = Depends(verify_key),
    _o: bool = Depends(verify_origin),
):

    statuts_valides = {
        "ouverte",
        "en_cours",
        "resolue",
        "fausse_alerte",
    }

    if body.statut not in statuts_valides:

        raise HTTPException(
            400,
            "Statut invalide",
        )

    with engine.begin() as c:

        result = c.execute(
            text(
                """
                UPDATE alerte
                SET statut = :s
                WHERE id_alerte = :i
                """
            ),
            {
                "s": body.statut,
                "i": id_alerte,
            },
        )

    if result.rowcount == 0:

        raise HTTPException(
            404,
            "Alerte introuvable",
        )

    return {
        "ok": True
    }


# ─────────────────────────────────────────────────────────────────────────────
# PARCELLES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/parcelles")
def get_parcelles(
    key: bool = Depends(verify_key),
):

    try:

        with engine.connect() as c:

            rows = c.execute(
                text(
                    """
                    SELECT
                        p.id_parcelle,
                        p.nicad,
                        p.statut,

                        CAST(
                            p.superficie_officielle
                            AS FLOAT
                        ) AS superficie_officielle,

                        CAST(
                            ROUND(
                                ST_Area(p.geom)::numeric,
                                2
                            )
                            AS FLOAT
                        ) AS superficie_reelle,

                        CAST(
                            ST_Y(
                                ST_Transform(
                                    ST_Centroid(p.geom),
                                    4326
                                )
                            )
                            AS FLOAT
                        ) AS lat,

                        CAST(
                            ST_X(
                                ST_Transform(
                                    ST_Centroid(p.geom),
                                    4326
                                )
                            )
                            AS FLOAT
                        ) AS lon,

                        z.nom AS zone,
                        z.statut_legal,

                        COUNT(a.id_alerte)
                            AS nb_alertes,

                        CAST(
                            COALESCE(
                                MAX(a.score_risque),
                                0
                            )
                            AS FLOAT
                        ) AS score_max

                    FROM parcelle p

                    JOIN zone_administrative z
                        ON p.id_zone_admin = z.id_zone

                    LEFT JOIN alerte a
                        ON a.id_parcelle = p.id_parcelle

                    GROUP BY
                        p.id_parcelle,
                        p.nicad,
                        p.statut,
                        p.superficie_officielle,
                        p.geom,
                        z.nom,
                        z.statut_legal

                    ORDER BY score_max DESC
                    """
                )
            ).fetchall()

        return [
            dict(r._mapping)
            for r in rows
        ]

    except Exception:

        logger.exception(
            "Erreur /parcelles"
        )

        raise HTTPException(
            500,
            "Erreur lors de la lecture des parcelles",
        )


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT SHAPEFILE
# ─────────────────────────────────────────────────────────────────────────────

def _safe_extract(
    zip_path: str,
    dest_dir: str,
) -> None:

    dest_root = os.path.realpath(
        dest_dir
    )

    with zipfile.ZipFile(
        zip_path,
        "r",
    ) as z:

        for member in z.namelist():

            target = os.path.realpath(
                os.path.join(
                    dest_dir,
                    member,
                )
            )

            if not (
                target == dest_root
                or target.startswith(
                    dest_root + os.sep
                )
            ):

                raise HTTPException(
                    400,
                    "Fichier ZIP invalide : "
                    "contient un chemin non autorise",
                )

        z.extractall(
            dest_dir
        )


@app.post("/import/shapefile")
async def import_shapefile(
    file: UploadFile = File(...),
    key: bool = Depends(verify_key),
    _o: bool = Depends(verify_origin),
):

    if not file.filename.endswith(".zip"):

        raise HTTPException(
            400,
            "Format attendu : ZIP contenant .shp .dbf .prj",
        )

    if (
        file.size
        and file.size > 50 * 1024 * 1024
    ):

        raise HTTPException(
            413,
            "Fichier trop volumineux (max 50 Mo)",
        )

    with tempfile.TemporaryDirectory() as tmp:

        zp = os.path.join(
            tmp,
            "upload.zip",
        )

        with open(zp, "wb") as f:
            f.write(
                await file.read()
            )

        try:

            _safe_extract(
                zp,
                tmp,
            )

        except zipfile.BadZipFile:

            raise HTTPException(
                400,
                "Fichier ZIP invalide ou corrompu",
            )

        shps = []

        for root, dirs, files in os.walk(tmp):
            for f in files:
                if f.endswith(".shp"):
                    shps.append(os.path.join(root, f))

        if not shps:

            raise HTTPException(
                400,
                "Aucun .shp trouve dans le ZIP "
                "(verifie qu'il contient bien .shp/.dbf/.prj, "
                "meme dans un sous-dossier)",
            )

        gdf = gpd.read_file(
            shps[0]
        )

        if gdf.crs is None:

            raise HTTPException(
                400,
                "Projection manquante dans le shapefile",
            )

        gdf = gdf.to_crs(
            epsg=32628
        )

        inserted = 0
        errors = 0

        with engine.begin() as c:

            zone_id = c.execute(
                text(
                    """
                    SELECT id_zone
                    FROM zone_administrative
                    LIMIT 1
                    """
                )
            ).scalar()

            if not zone_id:

                raise HTTPException(
                    400,
                    "Aucune zone administrative en base",
                )

            for idx, row in gdf.iterrows():

                try:

                    nicad = str(
                        row.get("nicad")
                        or row.get("NICAD")
                        or f"IMP-{idx+1:04d}"
                    )

                    sup = float(
                        row.get("superficie")
                        or row.get("SUPERFICIE")
                        or row.geometry.area
                    )

                    result = c.execute(
                        text(
                            """
                            INSERT INTO parcelle
                                (
                                    nicad,
                                    superficie_officielle,
                                    statut,
                                    geom,
                                    id_zone_admin
                                )
                            VALUES
                                (
                                    :n,
                                    :s,
                                    'active',
                                    ST_GeomFromText(:g, 32628),
                                    :z
                                )
                            ON CONFLICT (nicad)
                            DO NOTHING
                            """
                        ),
                        {
                            "n": nicad,
                            "s": sup,
                            "g": row.geometry.wkt,
                            "z": zone_id,
                        },
                    )

                    if result.rowcount > 0:
                        inserted += 1

                except Exception:

                    logger.exception(
                        "Echec import parcelle ligne %s",
                        idx,
                    )

                    errors += 1

        return {
            "message": (
                f"{inserted} parcelle(s) importee(s), "
                f"{errors} erreur(s)"
            ),
            "inserees": inserted,
            "erreurs": errors,
        }


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT CSV
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {
    "transaction": {
        "id_parcelle"
    },

    "proprietaire": {
        "nom_complet"
    },
}


@app.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    type_donnee: str = "transaction",
    key: bool = Depends(verify_key),
    _o: bool = Depends(verify_origin),
):

    if not file.filename.endswith(".csv"):

        raise HTTPException(
            400,
            "Fichier CSV requis",
        )

    if type_donnee not in REQUIRED_COLUMNS:

        raise HTTPException(
            400,
            "type_donnee invalide",
        )

    content = await file.read()

    if len(content) > 20 * 1024 * 1024:

        raise HTTPException(
            413,
            "Fichier trop volumineux (max 20 Mo)",
        )

    with tempfile.NamedTemporaryFile(
        suffix=".csv",
        delete=False,
    ) as tmp:

        tmp.write(content)

        tmp_path = tmp.name

    try:

        df = pd.read_csv(
            tmp_path
        )

        missing = (
            REQUIRED_COLUMNS[type_donnee]
            - set(df.columns)
        )

        if missing:

            raise HTTPException(
                400,
                "Colonnes manquantes dans le CSV : "
                + ", ".join(missing),
            )

        inserted = 0
        errors = 0

        with engine.begin() as c:

            if type_donnee == "transaction":

                for idx, row in df.iterrows():

                    try:

                        c.execute(
                            text(
                                """
                                INSERT INTO transaction_fonciere
                                    (
                                        date_transaction,
                                        type,
                                        montant,
                                        id_parcelle,
                                        id_vendeur,
                                        id_acheteur
                                    )
                                VALUES
                                    (
                                        :dt,
                                        :tp,
                                        :mn,
                                        :pa,
                                        :ve,
                                        :ac
                                    )
                                """
                            ),
                            {
                                "dt": row.get(
                                    "date_transaction"
                                ),

                                "tp": row.get(
                                    "type",
                                    "vente",
                                ),

                                "mn": (
                                    float(row["montant"])
                                    if pd.notna(
                                        row.get("montant")
                                    )
                                    else None
                                ),

                                "pa": int(
                                    row["id_parcelle"]
                                ),

                                "ve": (
                                    int(
                                        row["id_vendeur"]
                                    )
                                    if pd.notna(
                                        row.get(
                                            "id_vendeur"
                                        )
                                    )
                                    else None
                                ),

                                "ac": (
                                    int(
                                        row["id_acheteur"]
                                    )
                                    if pd.notna(
                                        row.get(
                                            "id_acheteur"
                                        )
                                    )
                                    else None
                                ),
                            },
                        )

                        inserted += 1

                    except Exception:

                        logger.exception(
                            "Echec import transaction ligne %s",
                            idx,
                        )

                        errors += 1

            elif type_donnee == "proprietaire":

                for idx, row in df.iterrows():

                    try:

                        c.execute(
                            text(
                                """
                                INSERT INTO proprietaire
                                    (
                                        nom_complet,
                                        type,
                                        nin,
                                        contact
                                    )
                                VALUES
                                    (
                                        :n,
                                        :t,
                                        :ni,
                                        :co
                                    )
                                ON CONFLICT (nin)
                                DO NOTHING
                                """
                            ),
                            {
                                "n": row.get(
                                    "nom_complet"
                                ),

                                "t": row.get(
                                    "type",
                                    "physique",
                                ),

                                "ni": str(
                                    row.get(
                                        "nin",
                                        "",
                                    )
                                ),

                                "co": str(
                                    row.get(
                                        "contact",
                                        "",
                                    )
                                ),
                            },
                        )

                        inserted += 1

                    except Exception:

                        logger.exception(
                            "Echec import proprietaire ligne %s",
                            idx,
                        )

                        errors += 1

        return {
            "message": (
                f"{inserted} enregistrement(s) importe(s), "
                f"{errors} erreur(s)"
            ),
            "inserees": inserted,
            "erreurs": errors,
        }

    finally:

        os.unlink(
            tmp_path
        )


# ─────────────────────────────────────────────────────────────────────────────
# DETECTION ML
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/detection/run")
def run_detection(
    key: bool = Depends(verify_key),
    _o: bool = Depends(verify_origin),
):

    try:

        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler

        gdf = gpd.read_postgis(
            """
            SELECT
                p.id_parcelle,
                p.nicad,
                p.superficie_officielle,
                ST_Area(p.geom) AS superficie_reelle,
                COUNT(t.id_transaction)
                    AS nb_transactions,
                COUNT(DISTINCT t.id_vendeur)
                    AS nb_vendeurs,
                COALESCE(
                    MAX(t.montant) - MIN(t.montant),
                    0
                ) AS ecart_prix,
                COUNT(tf.id_titre)
                    AS nb_titres,
                COUNT(a.id_alerte)
                    AS nb_alertes_existantes,
                p.geom

            FROM parcelle p

            LEFT JOIN transaction_fonciere t
                ON t.id_parcelle = p.id_parcelle

            LEFT JOIN titre_foncier tf
                ON tf.id_parcelle = p.id_parcelle

            LEFT JOIN alerte a
                ON a.id_parcelle = p.id_parcelle

            GROUP BY
                p.id_parcelle,
                p.nicad,
                p.superficie_officielle,
                p.geom
            """,
            engine,
            geom_col="geom",
        )

        if gdf.empty:

            return {
                "message": "Aucune parcelle a analyser",
                "alertes_ml": 0,
            }

        features = [
            "superficie_officielle",
            "superficie_reelle",
            "nb_transactions",
            "nb_vendeurs",
            "ecart_prix",
            "nb_titres",
            "nb_alertes_existantes",
        ]

        X = gdf[
            features
        ].fillna(0)

        Xs = StandardScaler().fit_transform(
            X
        )

        model = IsolationForest(
            n_estimators=100,
            contamination="auto",
            random_state=42,
        )

        gdf["anomalie"] = (
            model.fit_predict(Xs)
        )

        sc = model.score_samples(
            Xs
        )

        denominator = (
            sc.max() - sc.min()
        )

        gdf["score_norm"] = (
            0
            if denominator == 0
            else (
                1
                - (
                    (sc - sc.min())
                    / denominator
                )
            )
        )

        suspects = gdf[
            gdf["anomalie"] == -1
        ]

        inserted = 0

        with engine.begin() as c:

            for _, row in suspects.iterrows():

                c.execute(
                    text(
                        """
                        INSERT INTO alerte
                            (
                                type_anomalie,
                                score_risque,
                                description,
                                id_parcelle
                            )
                        VALUES
                            (
                                'scoring_ml',
                                :s,
                                'Anomalie detectee par Isolation Forest',
                                :p
                            )
                        """
                    ),
                    {
                        "s": round(
                            float(
                                row["score_norm"]
                            ),
                            3,
                        ),

                        "p": int(
                            row["id_parcelle"]
                        ),
                    },
                )

                inserted += 1

        return {
            "message": (
                f"Detection terminee — "
                f"{inserted} alerte(s) ML generee(s)"
            ),
            "alertes_ml": inserted,
        }

    except Exception as e:

        logger.exception(
            "Erreur pendant la detection ML"
        )

        raise HTTPException(
            500,
            "Erreur interne pendant la detection",
        ) from e


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT CARTE (image statique GeoPandas + Matplotlib, legende + titre)
# ─────────────────────────────────────────────────────────────────────────────

def _couleur_score(score: float) -> str:

    if score >= 0.8:
        return "#A82820"

    if score >= 0.5:
        return "#9A7420"

    return "#9A8868"


def _echelle_ronde(valeur_m: float) -> float:
    """
    Arrondit une distance (en metres) vers le multiple 1/2/5.10^n le
    plus proche, pour obtenir une barre d'echelle avec un chiffre
    rond (ex: 500 m, 1 km, 2 km) plutot qu'une valeur arbitraire.
    """

    import math

    if valeur_m <= 0:
        return 100.0

    exposant = math.floor(math.log10(valeur_m))
    base = valeur_m / (10 ** exposant)

    if base < 1.5:
        nice = 1
    elif base < 3.5:
        nice = 2
    elif base < 7.5:
        nice = 5
    else:
        nice = 10

    return nice * (10 ** exposant)


@app.get("/export/carte")
def export_carte(
    key: bool = Depends(verify_key),
):
    """
    Genere une image PNG de la carte des parcelles avec les elements
    cartographiques standards : cartouche titre, legende encadree,
    fleche du nord, barre d'echelle, cadre net (neatline), et un
    etiquetage limite aux parcelles a risque pour rester lisible.

    Reste volontairement en UTM 28N (SRID natif de la base, EPSG:32628)
    plutot que de reprojeter en WGS84 : c'est la projection correcte
    pour une carte locale de ce type (pas de distorsion des surfaces
    et des distances), et ca rend la barre d'echelle triviale a
    calculer puisque les unites sont deja des metres.
    """

    try:

        gdf = gpd.read_postgis(
            """
            SELECT
                p.id_parcelle,
                p.nicad,
                p.geom,
                CAST(
                    COALESCE(MAX(a.score_risque), 0)
                    AS FLOAT
                ) AS score_max,
                z.nom AS zone
            FROM parcelle p
            LEFT JOIN alerte a
                ON a.id_parcelle = p.id_parcelle
            JOIN zone_administrative z
                ON p.id_zone_admin = z.id_zone
            GROUP BY p.id_parcelle, p.nicad, p.geom, z.nom
            """,
            engine,
            geom_col="geom",
        )

        zones = gpd.read_postgis(
            """
            SELECT nom, statut_legal, geom
            FROM zone_administrative
            """,
            engine,
            geom_col="geom",
        )

        if gdf.empty:

            raise HTTPException(
                400,
                "Aucune parcelle a exporter",
            )

        gdf = gdf[
            gdf.geometry.notna()
            & gdf.geometry.is_valid
        ]

        if gdf.empty:

            raise HTTPException(
                400,
                "Aucune parcelle avec une geometrie valide",
            )

        plt.rcParams["font.family"] = "monospace"

        fig, ax = plt.subplots(figsize=(12, 10))

        fig.patch.set_facecolor("#F0E8D8")
        ax.set_facecolor("#E4D8BC")

        minx, miny, maxx, maxy = gdf.total_bounds

        pad_x = max((maxx - minx) * 0.18, 50)
        pad_y = max((maxy - miny) * 0.18, 50)

        ax.set_xlim(minx - pad_x, maxx + pad_x)

        # Marge du haut plus large : c'est la que vit le cartouche titre
        ax.set_ylim(miny - pad_y, maxy + pad_y * 1.6)

        zones.boundary.plot(
            ax=ax,
            color="#5A4E38",
            linewidth=1.3,
            linestyle="--",
            zorder=2,
        )

        for _, z in zones.iterrows():

            zc = z["geom"].centroid

            ax.annotate(
                str(z["nom"]).upper(),
                (zc.x, zc.y),
                fontsize=8,
                fontweight="bold",
                ha="center",
                color="#5A4E38",
                alpha=0.75,
                zorder=2,
            )

        gdf["couleur"] = gdf["score_max"].apply(
            _couleur_score
        )

        gdf.plot(
            ax=ax,
            color=gdf["couleur"],
            edgecolor="#1E1A12",
            linewidth=0.6,
            zorder=3,
        )

        # Etiquettes uniquement pour les parcelles a risque : au-dela
        # d'une poignee de parcelles normales etiquetees, une carte
        # devient illisible sans rien apporter de plus a la lecture.
        a_etiqueter = gdf[gdf["score_max"] >= 0.5]

        for _, row in a_etiqueter.iterrows():

            c = row["geom"].centroid

            ax.annotate(
                row["nicad"],
                (c.x, c.y),
                xytext=(0, 9),
                textcoords="offset points",
                fontsize=7,
                fontweight="bold",
                ha="center",
                color="#1E1A12",
                zorder=4,
            )

        # ── Cadre net (neatline) ──
        for spine in ax.spines.values():
            spine.set_edgecolor("#1E1A12")
            spine.set_linewidth(1.4)

        ax.set_xticks([])
        ax.set_yticks([])

        # ── Legende encadree ──
        legende = [
            Patch(
                facecolor="#A82820",
                edgecolor="#1E1A12",
                label="Critique (score >= 0.8)",
            ),
            Patch(
                facecolor="#9A7420",
                edgecolor="#1E1A12",
                label="Modere (score >= 0.5)",
            ),
            Patch(
                facecolor="#9A8868",
                edgecolor="#1E1A12",
                label="Faible / normal",
            ),
            Line2D(
                [0], [0],
                color="#5A4E38",
                lw=1.3,
                linestyle="--",
                label="Limite de zone administrative",
            ),
        ]

        leg = ax.legend(
            handles=legende,
            loc="lower left",
            fontsize=8,
            framealpha=0.95,
            facecolor="#F0E8D8",
            edgecolor="#1E1A12",
            title="LEGENDE",
            title_fontsize=8,
        )

        leg.get_title().set_fontweight("bold")

        # ── Fleche du nord ──
        ax.annotate(
            "N",
            xy=(0.955, 0.90),
            xytext=(0.955, 0.78),
            xycoords="axes fraction",
            fontsize=14,
            fontweight="bold",
            ha="center",
            color="#1E1A12",
            arrowprops=dict(
                arrowstyle="-|>",
                color="#1E1A12",
                lw=2.2,
            ),
        )

        # ── Barre d'echelle (en metres : UTM 28N, pas de distorsion) ──
        long_barre = _echelle_ronde((maxx - minx) * 0.22)

        sx0 = minx + (maxx - minx) * 0.04
        sy0 = miny - pad_y * 0.55

        ax.plot(
            [sx0, sx0 + long_barre], [sy0, sy0],
            color="#1E1A12", lw=3, solid_capstyle="butt", zorder=5,
        )

        for x_tick in (sx0, sx0 + long_barre):

            ax.plot(
                [x_tick, x_tick],
                [sy0 - pad_y * 0.04, sy0 + pad_y * 0.04],
                color="#1E1A12", lw=3, zorder=5,
            )

        label_echelle = (
            f"{int(long_barre)} m"
            if long_barre < 1000
            else f"{long_barre / 1000:.1f} km"
        )

        ax.annotate(
            label_echelle,
            (sx0 + long_barre / 2, sy0),
            xytext=(0, -14),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            fontweight="bold",
            color="#1E1A12",
        )

        # ── Cartouche titre ──
        ax.set_title(
            "SDFCS — CARTOGRAPHIE DES PARCELLES ET ALERTES\n"
            "Dakar, Senegal · Projection UTM Zone 28N (EPSG:32628)",
            fontsize=13,
            fontweight="bold",
            color="#1E1A12",
            pad=14,
        )

        fig.text(
            0.01, 0.01,
            "SDFCS · CEDT / Le G15 · genere le "
            + datetime.now().strftime("%d/%m/%Y %H:%M"),
            fontsize=7.5,
            color="#5A4E38",
        )

        fig.text(
            0.99, 0.01,
            f"{len(gdf)} parcelle(s) — {len(a_etiqueter)} a risque (score >= 0.5)",
            fontsize=7.5,
            color="#5A4E38",
            ha="right",
        )

        buf = io.BytesIO()

        plt.savefig(
            buf,
            format="png",
            dpi=220,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
        )

        plt.close(fig)

        buf.seek(0)

        return StreamingResponse(
            buf,
            media_type="image/png",
            headers={
                "Content-Disposition": (
                    "attachment; filename=carte_sdfcs.png"
                )
            },
        )

    except HTTPException:

        raise

    except Exception as e:

        logger.exception(
            "Erreur pendant l'export carte"
        )

        raise HTTPException(
            500,
            f"Erreur export carte : {type(e).__name__}: {e}",
        ) from e


# ─────────────────────────────────────────────────────────────────────────────
# START
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
    )