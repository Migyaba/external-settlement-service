import os
import requests
import urllib3
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database import SessionLocal, engine
from models import Base, ExternalSettlementNotification

# Configuration
load_dotenv()
HUB_BASE_URL = os.getenv("HUB_BASE_URL", "http://localhost:3000/v2")
API_KEY = os.getenv("API_KEY", "dev-secret-key")
# Désactiver les avertissements SSL pour les environnements de test/cluster
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Initialisation de la base de données
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Mojaloop External Settlement Service",
    description="Service de notification pour les règlements externes Mojaloop",
    version="1.0.0"
)

# Dépendance pour la base de données
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Sécurité simple via API Key (Optionnel)
def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key and x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Clé API invalide")
    return x_api_key

@app.get("/health")
def health_check():
    """Vérification de l'état de santé du service."""
    return {"status": "OK", "timestamp": datetime.utcnow()}

@app.post("/external-settlement/{settlement_id}")
def notify_external_settlement(
    settlement_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    # auth: str = Depends(verify_api_key) # Décommentez pour activer la sécurité
):
    """
    Reçoit une notification de règlement d'un participant et met à jour le Hub Mojaloop.
    """
    participant_id = payload.get("participantId")
    amount = payload.get("amount")
    currency = payload.get("currency")
    reference = payload.get("reference")
    settled_at_str = payload.get("settledAt")

    if not participant_id:
        raise HTTPException(status_code=400, detail="participantId est requis")

    # 1. Vérification du Settlement dans le Hub
    try:
        hub_response = requests.get(
            f"{HUB_BASE_URL}/settlements/{settlement_id}", 
            verify=False,
            timeout=5
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Erreur connexion Hub: {str(e)}")

    if hub_response.status_code != 200:
        raise HTTPException(status_code=404, detail="Settlement non trouvé dans le Hub")
    
    settlement_data = hub_response.json()

    # 2. Validation du statut
    state = settlement_data.get("state")
    allowed_states = ["PS_TRANSFERS_RECORDED", "PS_TRANSFERS_COMMITTED", "SETTLED"]
    if state not in allowed_states:
        raise HTTPException(status_code=400, detail=f"Statut settlement invalide pour notification: {state}")

    # 3. Vérification du participant
    participants_list = settlement_data.get("participants") or settlement_data.get("participantSettlements") or []
    participant_exists = any(str(p.get("id") or p.get("participantId")) == str(participant_id) for p in participants_list)

    if not participant_exists:
        raise HTTPException(status_code=403, detail=f"Le participant {participant_id} n'appartient pas à ce règlement")

    # 4. Gestion de l'idempotence et vérification du quorum
    existing = db.query(ExternalSettlementNotification).filter(
        ExternalSettlementNotification.settlement_id == settlement_id,
        ExternalSettlementNotification.participant_id == participant_id
    ).first()

    if not existing:
        # 5. Enregistrement de la notification
        dt_settled = datetime.fromisoformat(settled_at_str.replace("Z", "")) if settled_at_str else datetime.utcnow()
        notification = ExternalSettlementNotification(
            settlement_id=settlement_id,
            participant_id=participant_id,
            amount=str(amount) if amount else None,
            currency=currency,
            reference=reference,
            settled_at=dt_settled
        )
        db.add(notification)
        db.commit()

    # 6. Vérification de la clôture
    notified_count = db.query(ExternalSettlementNotification).filter(
        ExternalSettlementNotification.settlement_id == settlement_id
    ).count()
    total_needed = len(participants_list)

    if notified_count >= total_needed:
        # Action de finalisation sur le Hub si le statut n'est pas déjà SETTLED
        if state != "SETTLED":
            try:
                requests.put(
                    f"{HUB_BASE_URL}/settlements/{settlement_id}",
                    json={"state": "SETTLED"},
                    verify=False,
                    timeout=5
                )
            except Exception:
                pass # Échec non bloquant si déjà traité par un autre thread
        
        return {"message": "Règlement finalisé. Tous les participants ont notifié.", "status": "FINALIZED"}

    return {"message": "Notification enregistrée avec succès", "status": "PENDING_QUORUM"}

@app.get("/external-settlement/{settlement_id}/status")
def get_settlement_status(settlement_id: str, db: Session = Depends(get_db)):
    """Affiche l'état des notifications pour un règlement donné."""
    notifications = db.query(ExternalSettlementNotification).filter(
        ExternalSettlementNotification.settlement_id == settlement_id
    ).all()
    
    return {
        "settlementId": settlement_id,
        "notificationCount": len(notifications),
        "details": [
            {
                "participantId": n.participant_id,
                "reference": n.reference,
                "settledAt": n.settled_at
            } for n in notifications
        ]
    }
