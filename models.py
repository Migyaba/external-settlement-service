from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from database import Base

class ExternalSettlementNotification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    settlement_id = Column(String, index=True)
    participant_id = Column(String, index=True)
    amount = Column(String)
    currency = Column(String)
    reference = Column(String)
    settled_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
