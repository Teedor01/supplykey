from . import config as _config  # Load backend/.env before checking APP_ENV.
import os,sys
from sqlalchemy import select
from .db import SessionLocal,init_db,User
from .main import hash_password

def seed():
    if os.getenv('APP_ENV','development')=='production': raise SystemExit('Refusing to seed demo users in production.')
    init_db();s=SessionLocal()
    demo=[('buyer@supplykey.demo','Buyer Demo','USER'),('supplier@supplykey.demo','Supplier Demo','USER'),('moderator@supplykey.demo','Moderator Demo','MODERATOR'),('senior@supplykey.demo','Senior Moderator Demo','SENIOR_MODERATOR'),('admin@supplykey.demo','Admin Demo','ADMIN')]
    for email,name,role in demo:
        u=s.scalar(select(User).where(User.email==email))
        if not u:
            u=User(id=__import__('uuid').uuid4().__str__(),email=email,phone=None,password_hash=hash_password('SupplyKeyDemo!2026'),display_name=name,role=role,verified=True,profile={'display_name':name},mfa_secret='JBSWY3DPEHPK3PXP' if role in ['MODERATOR','SENIOR_MODERATOR','ADMIN'] else None);s.add(u)
    s.commit();s.close();print('Seeded demo users. Password: SupplyKeyDemo!2026. Staff TOTP secret: JBSWY3DPEHPK3PXP (development only).')
if __name__=='__main__':seed()
