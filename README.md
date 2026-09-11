# SDFCS — Système de Détection de Fraude Cadastrale

## Présentation

Le Système de Détection de Fraude Cadastrale (SDFCS) est une application web dédiée à l'analyse et à la détection de situations potentiellement frauduleuses dans les données cadastrales.

Le projet combine les technologies de la géomatique, des bases de données spatiales, du développement web et du machine learning afin de faciliter l'identification d'anomalies dans les données foncières.

L'application permet notamment de gérer les parcelles cadastrales, les propriétaires, les titres fonciers et les transactions foncières, tout en générant des alertes à partir de règles spatiales et de méthodes de détection d'anomalies.

## Objectifs

Le projet a pour objectifs de :

* Centraliser les données cadastrales dans une base de données structurée.
* Exploiter les capacités de PostgreSQL et PostGIS pour les traitements spatiaux.
* Détecter automatiquement les chevauchements de parcelles.
* Identifier les parcelles situées dans des zones administratives suspendues.
* Détecter des anomalies dans les transactions foncières.
* Générer et centraliser des alertes.
* Utiliser une méthode de machine learning pour identifier des comportements atypiques.
* Fournir une interface permettant de consulter et d'analyser les données cadastrales.

## Fonctionnalités

### Tableau de bord

Le tableau de bord permet d'obtenir une vue générale de l'état des données cadastrales et des alertes détectées.

### Gestion des parcelles

L'application permet de consulter les parcelles cadastrales et leurs informations associées.

Les données géographiques peuvent être exploitées directement grâce à PostGIS.

### Analyse spatiale

Le système utilise PostGIS pour effectuer différentes opérations spatiales, notamment :

* Détection des chevauchements entre parcelles.
* Analyse des relations spatiales entre objets géographiques.
* Vérification de l'appartenance à certaines zones administratives.
* Exploitation des géométries cadastrales.

### Détection des alertes

Des règles permettent de générer automatiquement des alertes lorsqu'une situation potentiellement problématique est détectée.

Exemples :

* Chevauchement entre plusieurs parcelles.
* Parcelle située dans une zone suspendue.
* Situation anormale liée à une transaction foncière.
* Anomalie détectée par le modèle de machine learning.

### Détection d'anomalies par Machine Learning

Le projet intègre l'algorithme Isolation Forest afin d'identifier des transactions ou comportements considérés comme atypiques par rapport aux données analysées.

L'objectif n'est pas de déterminer automatiquement qu'une fraude est avérée, mais de fournir un système d'aide à la détection permettant de cibler les situations nécessitant une vérification humaine.

## Architecture

L'application repose sur une architecture séparant le frontend, le backend et la base de données.

```text
                    Utilisateur
                        |
                        v
              Frontend Web
              HTML / CSS / JS
                        |
                        v
                 API REST
                   FastAPI
                        |
            +-----------+-----------+
            |                       |
            v                       v
     PostgreSQL/PostGIS       Module Machine
            |                  Learning
            |
            v
      Données cadastrales
```

## Technologies utilisées

### Backend

* Python
* FastAPI
* SQLAlchemy
* PostgreSQL
* PostGIS
* Isolation Forest
* bcrypt pour la gestion sécurisée des mots de passe et de l'authentification

### Frontend

* HTML5
* CSS3
* JavaScript
* Fetch API
* Leaflet pour la cartographie

### Base de données

* PostgreSQL
* PostGIS

### Déploiement

* Vercel pour le frontend
* Render pour le backend
* Supabase pour la base de données PostgreSQL/PostGIS

## Modèle de données

La base de données contient plusieurs tables principales permettant de structurer les informations cadastrales.

```text
parcelle
    |
    +---- proprietaire
    |
    +---- titre_foncier
    |
    +---- transaction_fonciere
    |
    +---- alerte
    |
    +---- zone_administrative
```

### Principales tables

#### parcelle

Contient les informations relatives aux parcelles cadastrales ainsi que leurs géométries spatiales.

#### proprietaire

Contient les informations relatives aux propriétaires associés aux parcelles.

#### titre_foncier

Permet de gérer les informations liées aux titres fonciers.

#### transaction_fonciere

Regroupe les opérations et transactions associées aux parcelles.

#### alerte

Centralise les anomalies et situations nécessitant une vérification.

#### zone_administrative

Contient les zones géographiques utilisées pour les analyses spatiales et les contrôles administratifs.

## Contraintes et automatisation dans la base de données

Une partie de la logique de détection est directement intégrée à la base de données grâce aux fonctionnalités de PostgreSQL et PostGIS.

Des mécanismes permettent notamment de détecter :

* Les chevauchements géométriques entre parcelles.
* Les parcelles se trouvant dans des zones suspendues.

L'utilisation de triggers permet d'automatiser certaines vérifications lors de l'insertion ou de la modification des données.

## API

Le backend est développé avec FastAPI et expose une API REST utilisée par le frontend.

Exemples de routes :

```text
GET  /health
POST /auth/login
POST /auth/logout

GET  /stats
GET  /alertes
GET  /parcelles

POST /detection/run
```

La documentation interactive de l'API FastAPI est disponible à l'adresse :

```text
/docs
```

## Authentification

L'application utilise une authentification basée sur une session côté serveur.

Après une connexion réussie, une session sécurisée est créée et associée à l'utilisateur.

Le cookie de session utilise notamment les paramètres :

* HttpOnly
* Secure en production
* SameSite adapté à l'architecture frontend/backend

Les informations sensibles telles que les mots de passe, clés et variables d'environnement ne sont pas stockées dans le dépôt GitHub.

## Structure du projet

```text
sdfcs/
│
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── ...
│
├── frontend/
│   ├── index.html
│   ├── sdfcs.js
│   ├── vercel.json
│   └── ...
│
├── README.md
└── ...
```

## Installation locale

### Prérequis

Avant de lancer le projet, installer :

* Python 3.11 ou version ultérieure
* PostgreSQL
* PostGIS
* Git

### Cloner le dépôt

```bash
git clone https://github.com/SeydinaAliouneDiop/sdfcs.git
cd sdfcs
```

### Installer les dépendances Python

```bash
cd backend
pip install -r requirements.txt
```

### Variables d'environnement

Créer un fichier `.env` dans le backend avec les variables nécessaires à la connexion à la base de données et à l'authentification.

Exemple :

```env
DATABASE_URL=your_database_url
AUTH_USER=your_username
AUTH_PASSWORD_HASH=your_password_hash
COOKIE_SECURE=false
ALLOWED_ORIGINS=http://127.0.0.1:5501
```

Ne jamais publier les valeurs réelles des variables sensibles sur GitHub.

### Lancer le backend

```bash
uvicorn main:app --reload
```

Le backend sera alors accessible localement via :

```text
http://127.0.0.1:8000
```

## Déploiement

L'architecture actuelle du projet sépare les différents composants :

```text
Frontend
Vercel
    |
    v
Backend
Render
    |
    v
PostgreSQL/PostGIS
Supabase
```

Cette séparation permet de déployer indépendamment l'interface utilisateur, l'API et la base de données.

## Sécurité

Le projet prend en compte plusieurs aspects liés à la sécurité :

* Authentification utilisateur.
* Sessions sécurisées.
* Hashage des mots de passe.
* Variables sensibles stockées dans les variables d'environnement.
* Validation des accès aux routes protégées.
* Séparation entre frontend et backend.
* Utilisation de HTTPS en production.
* Contrôle des origines via CORS.

## Limites du projet

Le SDFCS constitue un système d'aide à la détection et ne remplace pas une procédure officielle de vérification cadastrale.

Une alerte indique une situation potentiellement anormale qui doit être analysée et vérifiée par un professionnel ou une autorité compétente.

Les résultats du modèle de machine learning doivent également être interprétés comme des indicateurs d'anomalies et non comme une preuve automatique de fraude.

## Perspectives d'évolution

Plusieurs améliorations peuvent être envisagées :

* Intégration de données cadastrales réelles à plus grande échelle.
* Amélioration des modèles de détection d'anomalies.
* Ajout de modèles supervisés lorsque des données de fraude labellisées seront disponibles.
* Ajout d'un système de scoring des risques.
* Historisation complète des modifications cadastrales.
* Ajout d'un système de rôles et de permissions.
* Amélioration des analyses spatiales.
* Ajout d'outils avancés de visualisation cartographique.
* Mise en place de traitements automatisés sur de grands volumes de données.
* Intégration avec des services géospatiaux supplémentaires.

## Captures d'écran

Des captures d'écran de l'application peuvent être ajoutées ici afin de présenter les principales interfaces :

```text
Dashboard
Carte cadastrale
Gestion des parcelles
Alertes
Détection d'anomalies
```

## Auteur

Seydina Alioune Diop

Étudiant en BTS Géomatique

Projet orienté vers la géomatique, les bases de données spatiales, le développement web et l'intelligence artificielle.

## Licence

Ce projet est développé dans un cadre académique et de démonstration.
