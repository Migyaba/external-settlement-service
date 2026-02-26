# Mojaloop External Settlement Notification Service

Ce microservice est le module de **confirmation et de notification** pour les règlements externes hors Hub au sein de l'écosystème Mojaloop. Il permet aux participants de confirmer le transfert réel des fonds et automatise la clôture des cycles de règlement.

## Objectifs du Projet
- **Quorum de Validation** : Attend que tous les participants d'un cycle (Payeurs et Payés) confirment le règlement pour finaliser le cycle.
- **Synchronisation Hub** : Met à jour automatiquement les états des participants et du cycle sur le Hub Mojaloop.
- **Alertes Automatisées** : Envoie des emails de confirmation aux participants (via SMTP Gmail) et des rapports de clôture à l'opérateur du Hub.

## Stack Technique
- **Framework** : FastAPI (Python 3.12+)
- **Base de données** : SQLite (via SQLAlchemy) pour l'idempotence des notifications.
- **Emails** : SMTP Gmail avec sécurisation TLS (Port 587).
- **Intégration** : Central Ledger et Central Settlement (Hub Mojaloop).

## Installation

1. **Environnement Virtuel** :
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Dépendances** :
   ```bash
   pip install fastapi[standard] sqlalchemy requests python-dotenv
   ```

3. **Lancement** :
   ```bash
   uvicorn main:app --reload --port 8001
   ```

## Configuration (.env)
Pour activer les emails réels, créez un fichier `.env` :
```env
HUB_BASE_URL=http://localhost:3001/v2
LEDGER_URL=http://localhost:4001
OPERATOR_EMAIL=votre-email@gmail.com
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=votre-email@gmail.com
SMTP_PASSWORD=votre-mot-de-passe-d-application
```

## Utilisation de l'API

### 1. Envoyer une Notification (POST)
**Endpoint** : `/external-settlement/{settlement_id}`  
**Description** : Utilisé par le participant pour confirmer son paiement.
```json
{
  "participantId": "3",
  "amount": 780.12,
  "currency": "XXX",
  "reference": "BANK-TRANSFER-998"
}
```

### 2. Consulter l'état des notifications (GET)
**Endpoint** : `/settlement/{settlement_id}`  
**Description** : Affiche le nombre de confirmations reçues sur le total attendu (Quorum).

## Sécurité et Robustesse
- **Validation Métier** : Le service vérifie que le montant et la devise correspondent exactement à ce qui est attendu par le Hub.
- **Idempotence** : Un participant ne peut pas notifier deux fois le même règlement.
- **Filtrage SMTP** : Le moteur d'email ignore automatiquement les adresses erronées ou les placeholders pour garantir la délivrabilité.

---
*Projet développé dans le cadre du module Settlement Matrix — BFT Projet.*
