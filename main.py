import os
import requests
import urllib3
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from database import SessionLocal, engine
from models import Base, ExternalSettlementNotification
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# Configuration
load_dotenv()
OPERATOR_EMAIL = os.getenv("OPERATOR_EMAIL")
HUB_BASE_URL = os.getenv("HUB_BASE_URL")
LEDGER_URL = os.getenv("LEDGER_URL")
API_KEY = os.getenv("API_KEY", "dev-secret-key")

# Configuration SMTP Gmail
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
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

# Schéma de validation pour les notifications 
class SettlementNotificationRequest(BaseModel):
    participantId: str
    amount: float = Field(..., gt=0, description="Le montant doit être supérieur à zéro")
    currency: str = Field(..., min_length=3, max_length=3, description="Code devise ISO (3 lettres)")
    reference: str = Field(..., min_length=1, description="La référence du virement est obligatoire")
    settledAt: Optional[str] = None

# Sécurité simple via API Key (Optionnel)
def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key and x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Clé API invalide")
    return x_api_key

def build_account_to_participant_map():
    """Construit un mapping account_id -> participant_name via le Central Ledger"""
    try:
        resp = requests.get(f"{LEDGER_URL}/participants", verify=False, timeout=5)
        if resp.status_code != 200:
            print(f"[WARN] Impossible de récupérer les participants du Ledger: {resp.status_code}")
            return {}
        
        participants = resp.json()
        mapping = {}
        for p in participants:
            p_name = p.get("name")
            for account in p.get("accounts", []):
                acc_id = str(account.get("id"))
                mapping[acc_id] = {
                    "name": p_name,
                    "currency": account.get("currency"),
                    "ledgerAccountType": account.get("ledgerAccountType")
                }
        return mapping
    except Exception as e:
        print(f"[ERROR] Erreur build_account_map: {e}")
        return {}

def get_participant_endpoint_email(participant_name: str) -> Optional[str]:
    """Récupère l'email de notification spécifique pour un participant"""
    try:
        resp = requests.get(f"{LEDGER_URL}/participants/{participant_name}/endpoints", verify=False, timeout=5)
        if resp.status_code != 200:
            return None
        
        endpoints = resp.json()
        for ep in endpoints:
            if ep.get("type") == "SETTLEMENT_TRANSFER_POSITION_CHANGE_EMAIL":
                email_val = ep.get("value")
                # Filtrer les placeholders comme {$inputs.email} ou les valeurs vides
                if email_val and "@" in email_val and "{" not in email_val:
                    return email_val
        return None
    except Exception:
        return None


def _send_email_smtp(to_email: str, subject: str, html_content: str):
    """Effectue l'envoi réel via le serveur SMTP Gmail."""
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("[ERREUR] SMTP_USERNAME ou SMTP_PASSWORD non configuré dans .env")
        return False

    msg = MIMEMultipart()
    msg['From'] = f"Mojaloop Hub Service <{SMTP_USERNAME}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html'))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()  # Sécurisation de la connexion
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[SMTP ERROR] Impossible d'envoyer à {to_email} : {e}")
        return False

def send_stakeholder_notifications(settlement_id: str, participants: list):
    """
    Envoie des notifications réelles par email via SMTP Gmail.
    """
    print(f"\n[NOTIFICATION ENGINE] Début des alertes SMTP pour le Settlement {settlement_id}")

    account_map = build_account_to_participant_map()
    errors = []

    for p in participants:
        accounts = p.get("accounts", [])
        p_id = p.get("id") or p.get("participantId")
        
        participant_name = None
        # On essaie de trouver le nom via n'importe quel compte du participant
        for acc in accounts:
            acc_id = str(acc.get("id"))
            if acc_id in account_map:
                participant_name = account_map[acc_id]['name']
                break
        
        if participant_name:
            email = get_participant_endpoint_email(participant_name)
            if email:
                success = _send_email_smtp(
                    to_email=email,
                    subject=f"[MOJALOOP] Settlement {settlement_id} — CONFIRMÉ",
                    html_content=_build_participant_email(participant_name, settlement_id, p_id)
                )
                if success:
                    print(f"[OK] Email SMTP envoye a {participant_name}({p_id}) → {email}")
                else:
                    errors.append({"participant": participant_name, "error": "Erreur SMTP"})
            else:
                print(f"[WARN] Pas d'email configure pour {participant_name} — ignoree")
        else:
            print(f"[WARN] Aucun mapping trouve pour les comptes du Participant({p_id})")

    # Notification Opérateur
    if OPERATOR_EMAIL:
        success_op = _send_email_smtp(
            to_email=OPERATOR_EMAIL,
            subject=f"[MOJALOOP] Cycle de règlement {settlement_id} — CLÔTURÉ",
            html_content=_build_operator_email(settlement_id, participants, errors)
        )
        if success_op:
            print(f"[OK] Email SMTP envoye a l'OPERATEUR HUB → {OPERATOR_EMAIL}")
        else:
            print("[ERREUR] Echec notification operateur par SMTP")

    print("-------------------------------------------------------------------\n")


def _build_participant_email(name: str, settlement_id: str, p_id) -> str:
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto;">
        <h2 style="color: #1F4E79;">Confirmation de Règlement</h2>
        <p>Bonjour <strong>{name}</strong>,</p>
        <p>Le cycle de règlement <strong>{settlement_id}</strong> a été <strong style="color: green;">CONFIRMÉ</strong>.</p>
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background: #D6E4F0;">
                <td style="padding: 8px; border: 1px solid #ccc;"><b>Settlement ID</b></td>
                <td style="padding: 8px; border: 1px solid #ccc;">{settlement_id}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ccc;"><b>Participant ID</b></td>
                <td style="padding: 8px; border: 1px solid #ccc;">{p_id}</td>
            </tr>
            <tr style="background: #D6E4F0;">
                <td style="padding: 8px; border: 1px solid #ccc;"><b>Date</b></td>
                <td style="padding: 8px; border: 1px solid #ccc;">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</td>
            </tr>
        </table>
        <p style="color: #595959; font-size: 12px; margin-top: 20px;">
            Ce message est généré automatiquement par le Hub Mojaloop — BFT Projet.
        </p>
    </div>
    """


def _build_operator_email(settlement_id: str, participants: list, errors: list) -> str:
    error_section = ""
    if errors:
        error_section = f"""
        <h3 style="color: #B71C1C;">Erreurs d'envoi ({len(errors)})</h3>
        <ul>{"".join(f"<li>{e['participant']} : {e['error']}</li>" for e in errors)}</ul>
        """
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto;">
        <h2 style="color: #1F4E79;">Cycle de Règlement Clôturé</h2>
        <p>Le cycle <strong>{settlement_id}</strong> est <strong style="color: green;">CLÔTURÉ</strong>.</p>
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background: #D6E4F0;">
                <td style="padding: 8px; border: 1px solid #ccc;"><b>Settlement ID</b></td>
                <td style="padding: 8px; border: 1px solid #ccc;">{settlement_id}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ccc;"><b>Participants notifies</b></td>
                <td style="padding: 8px; border: 1px solid #ccc;">{len(participants)}</td>
            </tr>
            <tr style="background: #D6E4F0;">
                <td style="padding: 8px; border: 1px solid #ccc;"><b>Date</b></td>
                <td style="padding: 8px; border: 1px solid #ccc;">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</td>
            </tr>
        </table>
        {error_section}
        <p style="color: #595959; font-size: 12px; margin-top: 20px;">
            Ce message est généré automatiquement par le Hub Mojaloop — BFT Projet.
        </p>
    </div>
    """

@app.get("/health")
def health_check():
    """Vérification de l'état de santé du service."""
    return {"status": "OK", "timestamp": datetime.now(timezone.utc)}

@app.post("/external-settlement/{settlement_id}")
async def notify_external_settlement(
    settlement_id: str,
    payload: SettlementNotificationRequest,
    db: Session = Depends(get_db),
    # auth: str = Depends(verify_api_key) # Décommentez pour activer la sécurité
    ):
    """
    Reçoit une notification de règlement d'un participant et met à jour le Hub Mojaloop.
    """
    participant_id = payload.participantId
    amount = payload.amount
    currency = payload.currency
    reference = payload.reference
    settled_at_str = payload.settledAt

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

    # 2. Validation du statut (On autorise PENDING_SETTLEMENT pour les tests de matrice)
    state = settlement_data.get("state")
    allowed_states = [
        "PS_TRANSFERS_RECORDED",
        "PS_TRANSFERS_RESERVED",
        "PS_TRANSFERS_COMMITTED",
        "SETTLED"
    ]
    if state not in allowed_states:
        raise HTTPException(status_code=400, detail=f"Statut settlement invalide pour notification: {state}")

    # 3. Vérification du participant et conformité métier
    participants_list = settlement_data.get("participants") or settlement_data.get("participantSettlements") or []
    
    # Recherche du participant spécifique dans les données du Hub
    target_participant = next((p for p in participants_list if str(p.get("id") or p.get("participantId")) == str(participant_id)), None)
    
    if not target_participant:
        raise HTTPException(status_code=403, detail=f"Le participant {participant_id} n'appartient pas à ce règlement")

    # VALIDATION MÉTIER : Comparaison du montant et de la devise
    participant_accounts = target_participant.get("accounts", [])
    if not participant_accounts:
        raise HTTPException(status_code=400, detail="Données de compte manquantes pour ce participant dans le Hub")

    # Extraction du compte correspondant à la devise notifiée
    target_account = next((acc for acc in participant_accounts if acc.get("netSettlementAmount", {}).get("currency") == currency), None)
    
    if not target_account:
        raise HTTPException(
            status_code=400, 
            detail=f"Le participant n'a pas de position en devise {currency} pour ce règlement"
        )

    net_amount_obj = target_account.get("netSettlementAmount", {})
    hub_amount = net_amount_obj.get("amount")
    hub_currency = net_amount_obj.get("currency")

    if hub_amount is None:
        raise HTTPException(status_code=400, detail="Impossible de vérifier le montant attendu auprès du Hub")

    # On utilise abs() car le Hub affiche des montants négatifs pour les payeurs
    # et les participants notifient généralement le montant absolu transféré.
    if abs(float(amount) - abs(float(hub_amount))) > 0.01:
        raise HTTPException(
            status_code=400, 
            detail=f"Incohérence métier : Montant notifié ({amount}) != Montant Hub ({abs(float(hub_amount))})"
        )
    
    if currency != hub_currency:
        raise HTTPException(
            status_code=400, 
            detail=f"Incohérence métier : Devise notifiée ({currency}) != Devise Hub ({hub_currency})"
        )

    # 4. Gestion de l'idempotence et vérification du quorum
    existing = db.query(ExternalSettlementNotification).filter(
        ExternalSettlementNotification.settlement_id == settlement_id,
        ExternalSettlementNotification.participant_id == participant_id
    ).first()

    if not existing:
        # 5. Enregistrement de la notification
        dt_settled = datetime.fromisoformat(settled_at_str.replace("Z", "")) if settled_at_str else datetime.now(timezone.utc)
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

        # [NOUVEAU] Mise à jour du participant dans le Hub Mojaloop
        # Cette étape est cruciale pour que le Hub autorise ensuite la clôture globale.
        try:
            # On cherche l'ID de compte utilisé (on prend le premier pour la mise à jour d'état)
            acc_id = participant_accounts[0].get("id")
            update_resp = requests.put(
                f"{HUB_BASE_URL}/settlements/{settlement_id}/participants/{participant_id}/accounts/{acc_id}",
                json={
                    "state": "SETTLED",
                    "reason": f"Notification externe recue: {reference}"
                },
                verify=False,
                timeout=5
            )
            if update_resp.status_code in [200, 204]:
                print(f"[OK HUB] Statut SETTLED enregistre pour le participant {participant_id} sur le Hub.")
            else:
                print(f"[WARN HUB] Impossible de mettre à jour le participant {participant_id} sur le Hub : {update_resp.status_code}")
        except Exception as e:
            print(f"[ERR HUB] Erreur appel participant Hub: {e}")

    # 6. Vérification de la clôture
    notified_count = db.query(ExternalSettlementNotification).filter(
        ExternalSettlementNotification.settlement_id == settlement_id
    ).count()
    total_needed = len(participants_list)

    if notified_count >= total_needed:
        # Action de finalisation sur le Hub si le statut n'est pas déjà SETTLED
        if state != "SETTLED":
            print(f"[QUORUM ATTEINT] {notified_count}/{total_needed}. Tentative de clôture globale du règlement {settlement_id}...")
            try:
                # Essayer différents états si SETTLED est rejeté (parfois selon la phase du Hub)
                for target_state in ["SETTLED", "PS_TRANSFERS_COMMITTED"]:
                    resp_put = requests.put(
                        f"{HUB_BASE_URL}/settlements/{settlement_id}",
                        json={"state": target_state},
                        verify=False,
                        timeout=5
                    )
                    if resp_put.status_code in [200, 204]:
                        print(f"[OK HUB] Règlement {settlement_id} passé en état {target_state} sur le Hub.")
                        break
                    else:
                        print(f"[INFO HUB] Tentative {target_state} : {resp_put.status_code} - {resp_put.text}")
            except Exception as e:
                print(f"[EXCEPT HUB] Erreur lors de l'appel PUT final : {e}")
        
        # Envoi des notifications aux parties prenantes
        send_stakeholder_notifications(settlement_id, participants_list)
        
        return {"message": "Règlement finalisé et synchronisé avec le Hub.", "status": "FINALIZED"}

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
