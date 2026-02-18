# Mojaloop External Settlement Notification Service

Ce microservice FastAPI permet de gérer les **notifications de règlement externes** pour un Hub Mojaloop. Il assure la passerelle entre les règlements effectués hors-ligne (ex: virement bancaire RTGS) et le statut des transactions dans le Hub Mojaloop.

## 🚀 Fonctionnalités

- **Réception de notifications** : Endpoint pour que les participants (DFSPs) déclarent avoir effectué leur règlement.
- **Vérification croisée** : Validation automatique auprès du Hub Mojaloop (API v2) pour vérifier l'existence du règlement et l'appartenance du participant.
- **Gestion du Quorum** : Déclenche automatiquement la clôture du règlement (`SETTLED`) sur le Hub uniquement lorsque **tous** les participants concernés ont envoyé leur notification.
- **Idempotence** : Gestion des doublons et des reprises sur erreur.
- **Suivi en temps réel** : Endpoint de statut pour suivre l'avancement des notifications pour un `settlementId`.

## 🛠 Architecture & Tech Stack

- **Framework** : FastAPI (Python 3.12+)
- **Base de données** : SQLite (via SQLAlchemy ORM) pour le stockage local des notifications.
- **Communication** : Requests (intéraction avec Central Settlement Mojaloop).
- **Conteneurisation** : Docker & Docker Compose prêts.

## 📋 Pré-requis

- Python 3.12+
- Accès au Hub Mojaloop (Central Settlement Service)
- Un tunnel `kubectl port-forward` ou une route directe vers le Hub.

## ⚙️ Installation

1. **Cloner le projet** :
   ```bash
   git clone <url-du-repo>
   cd external-settlement-service
   ```

2. **Configurer l'environnement** :
   Créez un fichier `.env` à la racine :
   ```env
   HUB_BASE_URL=http://localhost:3000/v2
   API_KEY=votre-cle-secrete-si-besoin
   ```

3. **Installer les dépendances** :
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Lancer le service** :
   ```bash
   uvicorn main:app --reload
   ```

## 📖 Utilisation de l'API

### 1. Notifier un règlement
**POST** `/external-settlement/{settlementId}`

```bash
curl -X POST http://localhost:8000/external-settlement/32 \
     -H "Content-Type: application/json" \
     -d '{
       "participantId": "mtn-ci",
       "amount": 50000,
       "currency": "XOF",
       "reference": "RTGS-2024-001"
     }'
```

### 2. Vérifier l'avancement
**GET** `/external-settlement/{settlement_id}/status`

### 3. État de santé
**GET** `/health`

## 🐳 Docker (Optionnel)

Pour lancer le service via Docker :
```bash
docker-compose up --build
```

## 🔐 Sécurité
En production, il est recommandé d'activer la dépendance `verify_api_key` dans `main.py` et de fournir un `X-API-Key` dans les headers des requêtes.
