from . import config as _config  # Load backend/.env before reading settings.
import os, json, hmac, hashlib, secrets, uuid, time, threading, base64
from datetime import datetime, timedelta, timezone
from typing import Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request, Header, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import select
from .db import (SessionLocal, init_db, User, SessionToken, Aggregate, Payment, Idempotency, Audit, Evidence, Instruction, Dispute, Notification, Journal, LedgerEntry, ProviderEvent, RefreshSession, PagaSubsidiaryAccount, ProviderEventProcessing, now)
from .providers.paga import PagaClient, PagaConfig, PagaError, PagaUnknownOutcome
from .providers import simulated as sim
from . import payment_state as ps
from sqlalchemy.exc import IntegrityError
from decimal import Decimal, InvalidOperation
from cryptography.fernet import Fernet, InvalidToken

APP_ENV=os.getenv('APP_ENV','development'); SECRET=os.getenv('SUPPLYKEY_SECRET','dev-only-change-this-secret-before-deploy').encode()
ACCESS_MINUTES=int(os.getenv('ACCESS_TOKEN_MINUTES','30')); WORKER_SECRET=os.getenv('WORKER_SECRET','dev-worker-secret')
@asynccontextmanager
async def lifespan(app):
    provider_cfg=os.getenv('PAYMENT_PROVIDER','simulated').lower()
    if APP_ENV=='production' and provider_cfg!='paga':
        raise RuntimeError('PAYMENT_PROVIDER must be paga in production. The local payment simulator is never permitted in production.')
    if provider_cfg=='simulated':
        try:sim.configured_scenario()
        except ValueError as exc:raise RuntimeError(str(exc))
    if APP_ENV=='production' and os.getenv('SUPPLYKEY_SECRET','dev-only-change-this-secret-before-deploy')=='dev-only-change-this-secret-before-deploy':
        raise RuntimeError('SUPPLYKEY_SECRET must be replaced before production startup.')
    if APP_ENV=='production' and (len(SECRET)<32 or len(WORKER_SECRET)<32):
        raise RuntimeError('SUPPLYKEY_SECRET and WORKER_SECRET must each be at least 32 bytes in production.')
    if APP_ENV=='production' and os.getenv('DATABASE_URL','sqlite:///./supplykey.db').startswith('sqlite'):
        raise RuntimeError('SQLite is not permitted for production; configure PostgreSQL.')
    if APP_ENV=='production' and (not os.getenv('WORKER_SECRET') or WORKER_SECRET=='dev-worker-secret'):
        raise RuntimeError('WORKER_SECRET must be set before production startup.')
    paga_mode=os.getenv('PAGA_MODE','disabled').lower()
    if APP_ENV=='production' and (not os.getenv('PAYOUT_ENCRYPTION_KEY') or len(os.getenv('PAYOUT_ENCRYPTION_KEY',''))<32):
        raise RuntimeError('PAYOUT_ENCRYPTION_KEY must be a separate random secret of at least 32 characters in production.')
    if paga_mode=='live':
        if os.getenv('PAGA_CONDITIONAL_CUSTODY_APPROVED','false').lower()!='true' or not os.getenv('PAGA_LEGAL_APPROVAL_REFERENCE'):
            raise RuntimeError('Paga live mode is blocked until conditional-custody approval and Nigerian legal approval are recorded.')
        PagaConfig.from_env()
    elif paga_mode=='sandbox':
        PagaConfig.from_env()
    init_db()
    yield
app=FastAPI(title='SupplyKey API',version='1.1.0',description='SupplyKey modular monolith API with a local simulator and gated Paga Engine adapter.',lifespan=lifespan)
# Declare bearer auth to FastAPI/OpenAPI while retaining database-backed token validation.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name='BearerAuth', description='SupplyKey access token. Obtain an administrator token through password login followed by MFA verification.')
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','),allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
_rate_windows={};_rate_lock=threading.Lock()
class ApiRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        if request.url.path.startswith('/api/v1/auth/') or request.url.path.endswith('/payments'):
            key=(request.client.host if request.client else 'unknown',request.url.path);now_m=time.monotonic();limit=10 if '/auth/' in request.url.path else 20
            with _rate_lock:
                hits=[t for t in _rate_windows.get(key,[]) if now_m-t<60]
                if len(hits)>=limit:return JSONResponse(status_code=429,headers={'Retry-After':'60'},content={'error':{'code':'RATE_LIMITED','message':'Too many requests. Try again later.','details':{}},'meta':{'server_time':iso()}})
                hits.append(now_m);_rate_windows[key]=hits
        response=await call_next(request);response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer';response.headers['X-Frame-Options']='DENY';return response
app.add_middleware(ApiRateLimitMiddleware)
def db():
    s=SessionLocal()
    try: yield s
    finally: s.close()
def uid(): return str(uuid.uuid4())
def iso(dt=None): return (dt or now()).isoformat().replace('+00:00','Z')
def fail(status,code,message,details=None):
    raise HTTPException(status_code=status,detail={'code':code,'message':message,'details':details or {}})
def normalize_email(value):
    if value is None:return None
    value=str(value).strip().lower()
    return value or None
def normalize_phone(value):
    if value is None:return None
    raw=str(value).strip()
    if not raw:return None
    compact=''.join(ch for ch in raw if ch.isdigit() or ch=='+')
    if compact.startswith('00'):compact='+'+compact[2:]
    if compact.count('+')>1 or ('+' in compact and not compact.startswith('+')):return None
    digits=compact[1:] if compact.startswith('+') else compact
    if not digits.isdigit() or len(digits)<7 or len(digits)>15:return None
    # SupplyKey's registration market is Nigeria: canonicalize local 11-digit
    # mobile numbers and bare 234 country-code numbers to the same E.164 form.
    if len(digits)==11 and digits.startswith('0'):return '+234'+digits[1:]
    if len(digits)==13 and digits.startswith('234'):return '+'+digits
    if compact.startswith('+'):return '+'+digits
    return digits
def resolve_buyer(s,contact):
    if not isinstance(contact,dict):fail(400,'VALIDATION_FAILED','Buyer email or phone is required.')
    email=normalize_email(contact.get('email'))
    phone=normalize_phone(contact.get('phone'))
    if contact.get('email') and (not email or '@' not in email):fail(400,'INVALID_BUYER_CONTACT','Enter a valid buyer email address.')
    if contact.get('phone') and not phone:fail(400,'INVALID_BUYER_CONTACT','Enter a valid buyer phone number.')
    if not email and not phone:fail(400,'VALIDATION_FAILED','Provide the buyer email or phone number.')
    matches=[]
    if email:
        user=s.scalar(select(User).where(User.email==email))
        if user:matches.append(user)
    if phone:
        user=s.scalar(select(User).where(User.phone==phone))
        if user and all(x.id!=user.id for x in matches):matches.append(user)
    if len(matches)>1:fail(409,'BUYER_CONTACT_MISMATCH','Buyer email and phone belong to different accounts.')
    buyer=matches[0] if matches else None
    if buyer and not buyer.verified:fail(422,'BUYER_NOT_VERIFIED','The matching buyer account must verify its contact before receiving transactions.')
    if buyer and buyer.role in ['MODERATOR','SENIOR_MODERATOR','ADMIN']:fail(422,'BUYER_NOT_ELIGIBLE','This account is not eligible to act as a transaction buyer.')
    return buyer,{'email':email,'phone':phone}
def invitation_matches_user(tx,user):
    if user.role in ['MODERATOR','SENIOR_MODERATOR','ADMIN'] or not user.verified:return False
    contact=(tx.data or {}).get('buyer_contact') or {}
    email=normalize_email(contact.get('email'));phone=normalize_phone(contact.get('phone'))
    return bool((email and user.email and email==normalize_email(user.email)) or (phone and user.phone and phone==normalize_phone(user.phone)))
@app.exception_handler(HTTPException)
async def http_error(request,exc):
    d=exc.detail if isinstance(exc.detail,dict) else {'code':'ERROR','message':str(exc.detail),'details':{}}
    return JSONResponse(status_code=exc.status_code,content={'error':d,'meta':{'server_time':iso()}})
@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    fields=[{'loc':list(e.get('loc',[])),'message':e.get('msg','Invalid value')} for e in exc.errors()]
    return JSONResponse(status_code=400,content={'error':{'code':'VALIDATION_FAILED','message':'Request validation failed.','details':{'fields':fields}},'meta':{'server_time':iso()}})
@app.exception_handler(Exception)
async def unhandled(request,exc):
    rid=uid(); return JSONResponse(status_code=500,content={'error':{'code':'INTERNAL_ERROR','message':'An unexpected error occurred.','details':{'request_id':rid}},'meta':{'server_time':iso()}})
def hash_password(p):
    salt=secrets.token_bytes(16); key=hashlib.scrypt(p.encode(),salt=salt,n=2**14,r=8,p=1); return salt.hex()+':'+key.hex()
def check_password(p,stored):
    try:
        salt,expected=stored.split(':'); actual=hashlib.scrypt(p.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex(); return hmac.compare_digest(actual,expected)
    except Exception:return False
def issue_token(s:Session,user:User,pending=False):
    token=secrets.token_urlsafe(36); s.add(SessionToken(id=uid(),user_id=user.id,token_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at=now()+timedelta(minutes=5 if pending else ACCESS_MINUTES),mfa_pending=pending)); s.commit(); return token
def issue_refresh(s:Session,user:User,family_id=None):
    token=secrets.token_urlsafe(48);s.add(RefreshSession(id=uid(),user_id=user.id,family_id=family_id or uid(),token_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at=now()+timedelta(days=30)));s.commit();return token
def set_refresh_cookie(response:Response,token):
    response.set_cookie('sk_refresh',token,httponly=True,secure=APP_ENV=='production',samesite='lax',max_age=30*24*3600,path='/api/v1/auth')
def current_user(credentials:HTTPAuthorizationCredentials|None=Depends(bearer_scheme),s:Session=Depends(db)):
    # HTTPBearer contributes the OpenAPI security requirement; token validity is
    # still decided exclusively by the existing hashed SessionToken lookup below.
    token=credentials.credentials if credentials and credentials.scheme.lower()=='bearer' else ''
    if not token: fail(401,'UNAUTHENTICATED','Authentication required.')
    row=s.scalar(select(SessionToken).where(SessionToken.token_hash==hashlib.sha256(token.encode()).hexdigest(),SessionToken.revoked==False,SessionToken.mfa_pending==False))
    if not row or row.expires_at.replace(tzinfo=timezone.utc)<now(): fail(401,'UNAUTHENTICATED','Access token expired or invalid.')
    user=s.get(User,row.user_id)
    if not user: fail(401,'UNAUTHENTICATED','Session user no longer exists.')
    return user
def require_role(user,roles):
    if user.role not in roles: fail(403,'FORBIDDEN','This role cannot perform the requested action.')
def audit(s,user,action,typ,eid,details=None):
    previous=s.scalar(select(Audit).order_by(Audit.created_at.desc()).with_for_update().limit(1))
    prev_hash=previous.record_hash if previous and previous.record_hash else None
    stamp=now();payload={'id':uid(),'actor_id':user.id if user else None,'action':action,'entity_type':typ,'entity_id':eid,'details':details or {},'previous_hash':prev_hash,'created_at':iso(stamp)}
    digest=hashlib.sha256(((prev_hash or '')+json.dumps(payload,sort_keys=True,separators=(',',':'))).encode()).hexdigest()
    s.add(Audit(id=payload['id'],actor_id=payload['actor_id'],action=action,entity_type=typ,entity_id=eid,details=details or {},previous_hash=prev_hash,record_hash=digest,created_at=stamp))
def notify(s,user_id,kind,entity_id,payload=None):
    if user_id:s.add(Notification(id=uid(),user_id=user_id,type=kind,entity_id=entity_id,payload=payload or {},status='QUEUED'))
def post_journal(s,txid,entry_type,reference_id,lines):
    existing=s.scalar(select(Journal).where(Journal.entry_type==entry_type,Journal.reference_id==reference_id))
    if existing:return existing.id
    debit=sum(x[2] for x in lines if x[1]=='DEBIT'); credit=sum(x[2] for x in lines if x[1]=='CREDIT')
    if debit!=credit or debit<=0: raise ValueError('Unbalanced or empty journal rejected')
    jid=uid();s.add(Journal(id=jid,transaction_id=txid,entry_type=entry_type,reference_id=reference_id))
    for account,direction,amount in lines:s.add(LedgerEntry(id=uid(),journal_id=jid,transaction_id=txid,account_code=account,direction=direction,amount_kobo=amount))
    return jid
def tx_get(s,tid,user=None,staff=False):
    tx=s.scalar(select(Aggregate).where(Aggregate.id==tid).with_for_update())
    if not tx: fail(404,'NOT_FOUND','Transaction not found.')
    if user and not staff and user.id not in [tx.supplier_id,tx.buyer_id]: fail(404,'NOT_FOUND','Transaction not found.')
    return tx
def tx_data(tx): return json.loads(json.dumps(tx.data))
def save_tx(s,tx,data,status=None):
    tx.data=data; tx.version+=1; tx.updated_at=now()
    if status: tx.status=status
    s.flush()
def check_version(request,tx):
    header=request.headers.get('if-match')
    if header:
        try: supplied=int(header.strip('\"'))
        except ValueError:fail(400,'VALIDATION_FAILED','If-Match must contain the current integer version.')
        if supplied!=tx.version:fail(409,'VERSION_CONFLICT','The resource changed; reload before retrying.',{'current_version':tx.version})
def allowed_actions(tx,user):
    d=tx.data; a=[]
    role='SUPPLIER' if user and user.id==tx.supplier_id else 'BUYER' if user and user.id==tx.buyer_id else None
    if role=='SUPPLIER':
        if tx.status=='DRAFT': a+=['edit','send']
        if tx.status=='FUNDED' and any(x.get('item_status')=='AWAITING_DISPATCH' for x in d.get('lots',[])):a+=['dispatch']
        if any(r.get('status')=='HANDOVER_RECORDED' for r in d.get('returns',[])):a+=['confirm_return_receipt','contest_return']
    if role=='BUYER':
        if any(x.get('item_status')=='RECEIPT_CONFIRMED' for x in d.get('lots',[])):a+=['submit_decisions']
        if any(x.get('item_status')=='REJECTED_AWAITING_RETURN' for x in d.get('lots',[])) and not d.get('return_id') and not any(r.get('status') not in ['RECEIPT_CONFIRMED','CLOSED'] for r in d.get('returns',[])):a+=['open_return']
        if any(r.get('status')=='AWAITING_HANDOVER_EVIDENCE' for r in d.get('returns',[])):a+=['submit_handover_evidence']
        if tx.status=='AWAITING_BUYER_AGREEMENT':
            if d.get('invitation_acceptance_required') and not d.get('invitation_accepted_at'):a+=['accept_invitation']
            else:a+=['agree']
        active_payment=any(p.get('status') in ['PENDING','PAYMENT_RECEIVED','UNKNOWN','FUNDS_SECURED'] for p in d.get('payments',[]))
        if tx.status=='AWAITING_PAYMENT' and not active_payment:a+=['pay','cancel']
        if tx.status=='FUNDED':a+=['confirm_received','raise_dispute']
    if any(x.get('review_status')=='SUBMITTED_FOR_REVIEW' and x.get('evidence_window',{}).get('status')=='OPEN' for x in d.get('decisions',[])) and role:a+=['add_review_evidence','waive_window']
    if d.get('dispute') and d['dispute'].get('status')=='EVIDENCE_WINDOW_OPEN' and role:a+=['add_dispute_evidence','waive_dispute_window']
    if any(p.get('status') in ['PENDING','PAYMENT_RECEIVED','UNKNOWN'] for p in d.get('payments',[])) and role=='BUYER':a+=['refresh_payment']
    if role=='BUYER' and sim.simulator_allowed() and any(p.get('mode')==sim.MODE and p.get('status') in ['PENDING','UNKNOWN'] for p in d.get('payments',[])):a+=['simulate_payment']
    return list(dict.fromkeys(a))
def present_tx(tx,user):
    d=tx_data(tx); d.update({'id':tx.id,'reference':tx.reference,'status':tx.status,'version':tx.version,'supplier_id':tx.supplier_id,'buyer_id':tx.buyer_id,'allowed_actions':allowed_actions(tx,user) if user else [],'created_at':iso(tx.created_at),'updated_at':iso(tx.updated_at)})
    d['has_open_dispute']=bool(d.get('dispute') and d['dispute'].get('status') not in ['RESOLVED','CLOSED'])
    d['supplier']=d.get('supplier') or 'Supplier'; d['buyer']=d.get('buyer') or ('Buyer' if tx.buyer_id else 'Awaiting buyer')
    active_returns=d.get('returns',[]); d['return_id']=next((r['id'] for r in reversed(active_returns) if r.get('status') not in ['CLOSED','COMPLETED']),None)
    d['current_party_role']='BUYER' if user and user.id==tx.buyer_id else 'SUPPLIER' if user and user.id==tx.supplier_id else None
    return d
def idem(s,user,endpoint,key,body):
    if not key: fail(400,'VALIDATION_FAILED','Idempotency-Key header is required.')
    digest=hashlib.sha256(json.dumps(body or {},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    row=s.scalar(select(Idempotency).where(Idempotency.user_id==user.id,Idempotency.endpoint==endpoint,Idempotency.key==key))
    if row:
        if row.request_hash!=digest: fail(409,'IDEMPOTENCY_KEY_REUSED','The idempotency key was already used with a different request body.')
        return row.response
    return digest
def remember(s,user,endpoint,key,digest,response):
    s.add(Idempotency(id=uid(),user_id=user.id,endpoint=endpoint,key=key,request_hash=digest,response=response))
def money_int(v):
    if isinstance(v,bool) or not isinstance(v,int) or v<=0: fail(400,'VALIDATION_FAILED','Money must be a positive integer number of kobo.')
    return v

@app.get('/health')
def health(s:Session=Depends(db)):
    provider=os.getenv('PAYMENT_PROVIDER','simulated').upper()
    mode=os.getenv('PAGA_MODE','disabled').upper() if provider=='PAGA' else 'SIMULATED'
    return {'status':'ok','service':'supplykey-api','payment_provider':provider,'payment_mode':mode,'database':'connected','meta':{'server_time':iso()}}
@app.post('/api/v1/auth/register',status_code=201)
def register(body:dict,s:Session=Depends(db)):
    if not body.get('accepted_terms') or len(str(body.get('password','')))<10 or not body.get('display_name') or not(body.get('email') or body.get('phone')): fail(400,'VALIDATION_FAILED','Name, email or phone, password of at least 10 characters and accepted_terms are required.')
    email=normalize_email(body.get('email'));phone=normalize_phone(body.get('phone'))
    if body.get('email') and (not email or '@' not in email):fail(400,'INVALID_EMAIL','Enter a valid email address.')
    if body.get('phone') and not phone:fail(400,'INVALID_PHONE','Enter a valid phone number.')
    if email and s.scalar(select(User).where(User.email==email)): fail(409,'EMAIL_TAKEN','Email is already registered.')
    if phone and s.scalar(select(User).where(User.phone==phone)): fail(409,'PHONE_TAKEN','Phone is already registered.')
    u=User(id=uid(),email=email,phone=phone,password_hash=hash_password(body['password']),display_name=body['display_name'],profile={'display_name':body['display_name']})
    s.add(u); s.commit()
    result={'user_id':u.id,'verification_required':True}
    if APP_ENV!='production': result['development_otp']='000000'
    return result
@app.post('/api/v1/auth/verify-otp')
def verify_otp(body:dict,response:Response,s:Session=Depends(db)):
    if APP_ENV=='production' or body.get('otp')!='000000': fail(422,'OTP_INVALID','The verification code is invalid or expired.')
    contact=body.get('contact'); email=normalize_email(contact);phone=normalize_phone(contact);u=s.scalar(select(User).where((User.email==email) if email and '@' in email else (User.phone==phone)))
    if not u: fail(404,'NOT_FOUND','Registration not found.')
    u.verified=True; token=issue_token(s,u);set_refresh_cookie(response,issue_refresh(s,u)); return {'access_token':token,'expires_in':ACCESS_MINUTES*60,'user':user_json(u)}
@app.post('/api/v1/auth/resend-otp')
def resend_otp(body:dict): return {'status':'SENT'}
@app.post('/api/v1/auth/login')
def login(body:dict,response:Response,s:Session=Depends(db)):
    ident=body.get('identifier','');email=normalize_email(ident);phone=normalize_phone(ident);u=s.scalar(select(User).where((User.email==email) if email and '@' in email else (User.phone==phone)))
    if not u or not check_password(body.get('password',''),u.password_hash): fail(401,'INVALID_CREDENTIALS','Invalid email/phone or password.')
    if not u.verified: fail(403,'VERIFICATION_REQUIRED','Verify your contact before signing in.')
    if u.role in ['MODERATOR','SENIOR_MODERATOR','ADMIN']:
        if not u.mfa_secret:fail(403,'MFA_NOT_ENROLLED','Staff account has no enrolled authenticator.')
        challenge=issue_token(s,u,pending=True);response.set_cookie('sk_mfa_challenge',challenge,httponly=True,secure=APP_ENV=='production',samesite='strict',max_age=300);return {'mfa_required':True,'user':user_json(u)}
    token=issue_token(s,u);set_refresh_cookie(response,issue_refresh(s,u));return {'access_token':token,'expires_in':ACCESS_MINUTES*60,'user':user_json(u)}
def totp_valid(secret,code):
    import base64,struct
    try:key=base64.b32decode(secret.upper()+'='*((8-len(secret)%8)%8));counter=int(time.time())//30;digest=hmac.new(key,struct.pack('>Q',counter),hashlib.sha1).digest();offset=digest[-1]&15;value=(struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000;return hmac.compare_digest(f'{value:06d}',str(code))
    except Exception:return False
@app.post('/api/v1/auth/mfa/verify')
def mfa(body:dict,request:Request,response:Response,s:Session=Depends(db)):
    challenge=request.cookies.get('sk_mfa_challenge','');row=s.scalar(select(SessionToken).where(SessionToken.token_hash==hashlib.sha256(challenge.encode()).hexdigest(),SessionToken.mfa_pending==True,SessionToken.revoked==False)) if challenge else None
    if not row or row.expires_at.replace(tzinfo=timezone.utc)<now():fail(401,'MFA_CHALLENGE_EXPIRED','Staff authentication challenge expired.')
    u=s.get(User,row.user_id)
    if not u or not totp_valid(u.mfa_secret or '',body.get('code')):fail(401,'MFA_INVALID','Authenticator code is invalid.')
    row.revoked=True;token=issue_token(s,u);set_refresh_cookie(response,issue_refresh(s,u));response.delete_cookie('sk_mfa_challenge');return {'access_token':token,'expires_in':ACCESS_MINUTES*60,'user':user_json(u)}
@app.post('/api/v1/auth/refresh')
def refresh(request:Request,response:Response,s:Session=Depends(db)):
    token=request.cookies.get('sk_refresh','')
    if not token:fail(401,'UNAUTHENTICATED','Refresh cookie is missing.')
    digest=hashlib.sha256(token.encode()).hexdigest();row=s.scalar(select(RefreshSession).where(RefreshSession.token_hash==digest))
    if not row:fail(401,'UNAUTHENTICATED','Refresh token is invalid.')
    if row.revoked:
        s.query(RefreshSession).filter(RefreshSession.family_id==row.family_id).update({'revoked':True});s.query(SessionToken).filter(SessionToken.user_id==row.user_id).update({'revoked':True});s.commit();fail(401,'REFRESH_REUSED','Refresh token reuse detected; all sessions in this family were revoked.')
    if row.expires_at.replace(tzinfo=timezone.utc)<now():row.revoked=True;s.commit();fail(401,'UNAUTHENTICATED','Refresh token expired.')
    user=s.get(User,row.user_id);family=row.family_id;row.revoked=True;s.commit();new_refresh=issue_refresh(s,user,family);set_refresh_cookie(response,new_refresh);return {'access_token':issue_token(s,user),'expires_in':ACCESS_MINUTES*60,'user':user_json(user)}
@app.post('/api/v1/auth/logout',status_code=204)
def logout(request:Request,response:Response,s:Session=Depends(db),u:User=Depends(current_user)):
    token=request.headers.get('authorization','')[7:]; row=s.scalar(select(SessionToken).where(SessionToken.token_hash==hashlib.sha256(token.encode()).hexdigest()));
    if row: row.revoked=True
    refresh_token=request.cookies.get('sk_refresh','')
    if refresh_token:
        rr=s.scalar(select(RefreshSession).where(RefreshSession.token_hash==hashlib.sha256(refresh_token.encode()).hexdigest()))
        if rr:rr.revoked=True
    s.commit();response.delete_cookie('sk_refresh',path='/api/v1/auth');response.status_code=204
    return response
def user_json(u): return {'id':u.id,'email':u.email,'phone':u.phone,'display_name':u.display_name,'role':u.role,'verified':u.verified}
def public_payout_account(account):
    if not account:return None
    return {k:v for k,v in account.items() if k not in ['account_number_enc','account_number']}

def payout_cipher():
    configured=os.getenv('PAYOUT_ENCRYPTION_KEY','')
    if APP_ENV=='production' and len(configured)<32:raise RuntimeError('PAYOUT_ENCRYPTION_KEY must be configured in production.')
    material=configured or SECRET.decode('utf-8','ignore')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))

def decrypt_payout_account(account):
    encrypted=(account or {}).get('account_number_enc')
    if not encrypted:raise ValueError('Verified payout account has no encrypted account number.')
    try:return payout_cipher().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:raise ValueError('Payout account encryption key does not match the stored account.') from exc

@app.get('/api/v1/me')
def me(u:User=Depends(current_user)): return {'user':user_json(u),'profile':u.profile,'payout_account':public_payout_account(u.payout_account)}
@app.patch('/api/v1/me/profile')
def profile(body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    u.profile={**(u.profile or {}),**{k:v for k,v in body.items() if k in ['display_name','business_name','state','address']}}
    if 'display_name' in body:u.display_name=body['display_name']
    s.commit(); return {'profile':u.profile}
_PAGA_BANK_CACHE={'expires_at':0.0,'mode':None,'banks':[]}
_PAGA_BANK_CACHE_LOCK=threading.Lock()

def get_cached_paga_banks(client):
    mode=client.config.provider_mode
    if _PAGA_BANK_CACHE['mode']==mode and time.time()<_PAGA_BANK_CACHE['expires_at']:
        return _PAGA_BANK_CACHE['banks']
    with _PAGA_BANK_CACHE_LOCK:
        if _PAGA_BANK_CACHE['mode']==mode and time.time()<_PAGA_BANK_CACHE['expires_at']:
            return _PAGA_BANK_CACHE['banks']
        result=client.get_banks()
        if str(result.get('responseCode'))!='0':raise PagaError('Paga did not return a confirmed bank directory.')
        banks=result.get('banks',[])
        _PAGA_BANK_CACHE.update({'expires_at':time.time()+7*24*60*60,'mode':mode,'banks':banks})
        return banks

@app.get('/api/v1/payments/paga/banks')
def paga_banks(u:User=Depends(current_user)):
    try:
        client=PagaClient();return {'data':get_cached_paga_banks(client),'mode':client.config.provider_mode}
    except PagaError as exc:fail(502,'PAGA_BANK_DIRECTORY_FAILED',str(exc))

@app.put('/api/v1/me/payout-account')
def payout_account(body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    account_number=str(body.get('account_number',''))
    if not account_number.isdigit() or len(account_number)!=10:fail(400,'VALIDATION_FAILED','A 10-digit account number is required.')
    provider=os.getenv('PAYMENT_PROVIDER','simulated').lower()
    if provider=='paga':
        try:
            client=PagaClient();banks={'banks':get_cached_paga_banks(client)}
            requested=str(body.get('bank_uuid') or body.get('bank_code') or '').strip().lower()
            bank=next((b for b in banks.get('banks',[]) if requested in [str(b.get('uuid','')).lower(),str(b.get('interInstitutionCode','')).lower(),str(b.get('sortCode','')).lower()]),None)
            if not bank:fail(422,'BANK_NOT_SUPPORTED','Select a bank returned by Paga.')
            validation=client.validate_bank_account('SKVAL'+secrets.token_hex(10).upper(),100000,bank['uuid'],account_number)
            if str(validation.get('responseCode'))!='0':fail(422,'BANK_ACCOUNT_NOT_VERIFIED',validation.get('message') or 'Paga could not verify this bank account.')
            encrypted=payout_cipher().encrypt(account_number.encode()).decode()
            u.payout_account={'bank_uuid':bank['uuid'],'bank_code':bank.get('interInstitutionCode') or bank.get('sortCode'),'bank_name':bank.get('name'),'account_number_enc':encrypted,'masked_account_number':'••••'+account_number[-4:],'account_name':validation.get('destinationAccountHolderNameAtBank') or u.display_name,'verified':True,'verified_at':iso(),'effective_at':iso(),'mode':client.config.provider_mode,'validation_fee_kobo':int((Decimal(str(validation.get('fee') or 0))+Decimal(str(validation.get('vat') or 0)))*100)}
        except PagaError as exc:fail(502,'PAGA_ACCOUNT_VALIDATION_FAILED',str(exc))
    else:
        u.payout_account={'bank_code':str(body.get('bank_code') or ''),'masked_account_number':'••••'+account_number[-4:],'account_name':u.display_name,'verified':False,'effective_at':None,'mode':'SIMULATED'}
    s.commit();return {'payout_account':public_payout_account(u.payout_account)}

@app.post('/api/v1/transactions',status_code=201)
def create_tx(body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    items=body.get('items') or []
    if not items or len(items)>50: fail(400,'VALIDATION_FAILED','At least one item is required; maximum 50 lines.')
    total=0; built=[]
    for i,item in enumerate(items,1):
        q=item.get('quantity'); price=item.get('unit_price_kobo'); cond=item.get('conditions') or []
        if not isinstance(q,int) or q<1 or not isinstance(price,int) or price<1 or not cond: fail(400,'VALIDATION_FAILED','Each item needs quantity >= 1, positive unit_price_kobo and at least one condition.',{'line_no':i})
        line=q*price; total+=line; iid=uid(); built.append({'id':iid,'line_no':i,'description':item.get('description',''),'specification':item.get('specification',''),'condition':item.get('condition',''),'quantity':q,'unit_price_kobo':price,'line_total_kobo':line,'conditions':[{'id':uid(),**c} for c in cond]})
    delivery=body.get('delivery') or {}; fee=int(delivery.get('delivery_fee_kobo',0)); platform=0
    buyer,buyer_contact=resolve_buyer(s,body.get('buyer_contact') or {}); data={'supplier':u.display_name,'buyer':buyer.display_name if buyer else None,'invitation_acceptance_required':bool(buyer),'destination':f"{delivery.get('city','')}, {delivery.get('destination_state','')}",'delivery':delivery,'buyer_contact':buyer_contact,'general_terms':body.get('general_terms',''),'goods_total_kobo':total,'delivery_fee_kobo':fee,'platform_fee_kobo':platform,'total_payable_kobo':total+fee+platform,'items':built,'lots':[],'payments':[],'dispatches':[],'delivery_photo_requirements':[],'agreement_versions':[],'created_at':iso()}
    tx=Aggregate(id=uid(),reference=f"SK-{datetime.now().year}-{secrets.randbelow(900000)+100000}",supplier_id=u.id,buyer_id=buyer.id if buyer else None,status='DRAFT',data=json.loads(json.dumps(data)))
    s.add(tx); s.flush(); version={'id':uid(),'version':1,'content_hash':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),'created_at':iso(),'conditions':[c for item in built for c in item['conditions']]}; data['agreement_versions']=[version]; data['current_agreement_version_id']=version['id']; tx.data=json.loads(json.dumps(data))
    audit(s,u,'TRANSACTION_CREATED','TRANSACTION',tx.id); s.commit(); return {'data':present_tx(tx,u)}
@app.get('/api/v1/transactions')
def list_tx(role:str|None=None,status:str|None=None,limit:int=20,cursor:str|None=None,s:Session=Depends(db),u:User=Depends(current_user)):
    q=select(Aggregate).where((Aggregate.supplier_id==u.id)|(Aggregate.buyer_id==u.id))
    if role=='SUPPLIER':q=q.where(Aggregate.supplier_id==u.id)
    elif role=='BUYER':q=q.where(Aggregate.buyer_id==u.id)
    if status:q=q.where(Aggregate.status==status)
    rows=s.scalars(q.order_by(Aggregate.created_at.desc()).limit(min(limit,100))).all()
    return {'data':[present_tx(x,u) for x in rows],'meta':{'server_time':iso(),'next_cursor':None}}
@app.get('/api/v1/transactions/{tid}')
def get_tx(tid:str,s:Session=Depends(db),u:User=Depends(current_user)): return present_tx(tx_get(s,tid,u),u)
@app.patch('/api/v1/transactions/{tid}')
def patch_tx(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if u.id!=tx.supplier_id or tx.status not in ['DRAFT','AWAITING_BUYER_AGREEMENT','AWAITING_PAYMENT']: fail(409,'TERMS_LOCKED','Transaction terms cannot be edited in the current state.')
    if d.get('payments'): fail(409,'TERMS_LOCKED','Terms are locked after a payment attempt.')
    # Full item replacement is deliberate; server recalculates totals.
    if 'items' in body:
        items=body['items']; total=0; new=[]
        for i,it in enumerate(items,1):
            q=it.get('quantity'); p=it.get('unit_price_kobo')
            if not isinstance(q,int) or q<1 or not isinstance(p,int) or p<1 or not it.get('conditions'):fail(400,'VALIDATION_FAILED','Invalid item terms.')
            total+=q*p; new.append({'id':uid(),'line_no':i,**it,'line_total_kobo':q*p,'conditions':[{'id':uid(),**c} for c in it['conditions']]})
        d['items']=new; d['goods_total_kobo']=total; d['total_payable_kobo']=total+d.get('delivery_fee_kobo',0)+d.get('platform_fee_kobo',0)
    version={'id':uid(),'version':len(d.get('agreement_versions',[]))+1,'content_hash':hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest(),'created_at':iso()}; d.setdefault('agreement_versions',[]).append(version); d['current_agreement_version_id']=version['id']; d['buyer_agreed_version_id']=None
    save_tx(s,tx,d,'AWAITING_BUYER_AGREEMENT' if tx.status!='DRAFT' else 'DRAFT'); audit(s,u,'AGREEMENT_VERSION_CREATED','TRANSACTION',tid); s.commit(); return present_tx(tx,u)
@app.post('/api/v1/transactions/{tid}/send')
def send_tx(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if tx.supplier_id!=u.id or tx.status!='DRAFT': fail(409,'INVALID_STATE_TRANSITION','Only a supplier draft can be sent.')
    contact=d.get('buyer_contact') or {}; d['invitation_token']=secrets.token_urlsafe(32); d['invitation_expires_at']=iso(now()+timedelta(days=7)); save_tx(s,tx,d,'AWAITING_BUYER_AGREEMENT'); audit(s,u,'TRANSACTION_SENT','TRANSACTION',tid); s.commit()
    return {'status':tx.status,'invitation_url':f"/invite/{d['invitation_token']}",'invitation_token':d['invitation_token']}
@app.get('/api/v1/invitations/{token}')
def invitation(token:str,s:Session=Depends(db)):
    tx=s.scalar(select(Aggregate).where(Aggregate.data['invitation_token'].as_string()==token))
    if not tx: fail(404,'NOT_FOUND','Invitation not found.')
    d=tx_data(tx)
    if datetime.fromisoformat(d['invitation_expires_at'].replace('Z','+00:00'))<now():fail(410,'INVITATION_EXPIRED','Invitation has expired.')
    return {'transaction':{'id':tx.id,'reference':tx.reference,'status':tx.status,'items':d['items'],'total_payable_kobo':d['total_payable_kobo'],'supplier_id':tx.supplier_id}}
@app.post('/api/v1/invitations/{token}/accept')
def accept_invite(token:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=s.scalar(select(Aggregate).where(Aggregate.data['invitation_token'].as_string()==token))
    if not tx:fail(404,'NOT_FOUND','Invitation not found.')
    if tx.supplier_id==u.id:fail(422,'SELF_TRANSACTION_NOT_ALLOWED','Supplier cannot accept their own invitation.')
    d=tx_data(tx)
    if d.get('invitation_accepted_at'):fail(409,'INVITATION_USED','Invitation already accepted.')
    if datetime.fromisoformat(d['invitation_expires_at'].replace('Z','+00:00'))<now():fail(410,'INVITATION_EXPIRED','Invitation has expired.')
    if not invitation_matches_user(tx,u):fail(403,'INVITATION_IDENTITY_MISMATCH','Sign in with the verified email or phone address the supplier invited.')
    if tx.buyer_id and tx.buyer_id!=u.id:fail(403,'INVITATION_IDENTITY_MISMATCH','This invitation is assigned to another buyer account.')
    tx.buyer_id=u.id;d['buyer']=u.display_name;d['invitation_accepted_at']=iso();d['invitation_acceptance_required']=False;save_tx(s,tx,d)
    notify(s,tx.supplier_id,'INVITATION_ACCEPTED',tx.id,{'reference':tx.reference});s.commit();return {'status':'ACCEPTED','transaction_id':tx.id}
@app.post('/api/v1/transactions/{tid}/invitation/accept')
def accept_assigned_invitation(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    # Existing, contact-resolved buyers can accept directly from My Purchases.
    # Unknown buyers still have to claim the invitation through its opaque token URL.
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if tx.supplier_id==u.id:fail(422,'SELF_TRANSACTION_NOT_ALLOWED','Supplier cannot accept their own invitation.')
    if tx.buyer_id!=u.id:fail(403,'INVITATION_IDENTITY_MISMATCH','This invitation is not assigned to your account.')
    if tx.status!='AWAITING_BUYER_AGREEMENT':fail(409,'INVALID_STATE_TRANSITION','This transaction is not awaiting buyer agreement.')
    if d.get('invitation_accepted_at') or not d.get('invitation_acceptance_required'):
        fail(409,'INVITATION_USED','Invitation has already been accepted.')
    expires=d.get('invitation_expires_at')
    if not d.get('invitation_token') or not expires:fail(409,'INVITATION_INVALID','The pending invitation is incomplete or invalid.')
    if datetime.fromisoformat(expires.replace('Z','+00:00'))<now():fail(410,'INVITATION_EXPIRED','Invitation has expired.')
    if not invitation_matches_user(tx,u):fail(403,'INVITATION_IDENTITY_MISMATCH','Sign in with the verified email or phone address the supplier invited.')
    d['invitation_accepted_at']=iso();d['invitation_acceptance_required']=False;save_tx(s,tx,d)
    audit(s,u,'INVITATION_ACCEPTED','TRANSACTION',tx.id,{'reference':tx.reference})
    notify(s,tx.supplier_id,'INVITATION_ACCEPTED',tx.id,{'reference':tx.reference})
    s.commit();return {'status':'ACCEPTED','transaction_id':tx.id}

@app.get('/api/v1/transactions/{tid}/agreement')
def agreement(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); return {'current_version':tx.data.get('agreement_versions',[])[-1] if tx.data.get('agreement_versions') else None,'versions':tx.data.get('agreement_versions',[]),'buyer_confirmation':tx.data.get('agreed_at')}
@app.post('/api/v1/transactions/{tid}/agreement/confirm')
def agree(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if tx.buyer_id!=u.id or tx.status!='AWAITING_BUYER_AGREEMENT' or (d.get('invitation_acceptance_required') and not d.get('invitation_accepted_at')):fail(409,'INVALID_STATE_TRANSITION','Only the invited buyer can agree after accepting the pending invitation.')
    v=body.get('agreement_version_id'); current=d.get('current_agreement_version_id')
    if v!=current:fail(409,'AGREEMENT_VERSION_STALE','Agreement version is no longer current.')
    d['buyer_agreed_version_id']=v; d['agreed_at']=iso(); save_tx(s,tx,d,'AWAITING_PAYMENT'); audit(s,u,'AGREEMENT_CONFIRMED','TRANSACTION',tid,{'version_id':v});notify(s,tx.supplier_id,'AGREEMENT_CONFIRMED',tid,{'reference':tx.reference}); s.commit(); return {'status':'AWAITING_PAYMENT','agreement_version_id':v}
@app.post('/api/v1/transactions/{tid}/cancel')
def cancel(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u)
    if u.id not in [tx.buyer_id,tx.supplier_id] or tx.status not in ['DRAFT','AWAITING_BUYER_AGREEMENT','AWAITING_PAYMENT']:fail(409,'INVALID_STATE_TRANSITION','Cancellation is only allowed before funding.')
    tx.status='CANCELLED'; audit(s,u,'TRANSACTION_CANCELLED','TRANSACTION',tid); s.commit(); return {'status':'CANCELLED'}
@app.post('/api/v1/transactions/{tid}/payments')
def start_payment(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user),idempotency_key:str|None=Header(default=None,alias='Idempotency-Key')):
    tx=tx_get(s,tid,u); endpoint=f'POST:/transactions/{tid}/payments'; digest=idem(s,u,endpoint,idempotency_key,body)
    if isinstance(digest,dict):return digest
    d=tx_data(tx)
    if tx.buyer_id!=u.id or tx.status!='AWAITING_PAYMENT' or d.get('buyer_agreed_version_id')!=d.get('current_agreement_version_id'):fail(409,'INVALID_STATE_TRANSITION','Buyer must agree to the current terms before funding.')
    if s.scalar(select(Payment).where(Payment.transaction_id==tid,Payment.status.in_(['PENDING','PAYMENT_RECEIVED','UNKNOWN','FUNDS_SECURED']))):fail(409,'PAYMENT_ALREADY_IN_PROGRESS','A payment attempt already exists.')
    amount=d['total_payable_kobo']; provider_mode=os.getenv('PAYMENT_PROVIDER','simulated').lower()
    if provider_mode not in ['simulated','paga']:
        fail(503,'PAYMENT_PROVIDER_NOT_CONFIGURED','Configured payment provider is not supported.')
    if provider_mode=='paga':
        try:
            client=PagaClient(); cfg=client.config
        except PagaError as exc:
            fail(503,'PAGA_NOT_CONFIGURED',str(exc))
        payment_id=uid(); provider_ref=('SKPAY'+payment_id.replace('-',''))[:50]
        account_reference=('SKTX'+payment_id.replace('-',''))[:30]
        name=(u.display_name or 'SupplyKey Buyer').strip(); parts=name.split()
        first_name=parts[0][:50]; last_name=(' '.join(parts[1:]) or parts[0])[:50]
        p=Payment(id=payment_id,transaction_id=tid,amount_kobo=amount,status='PENDING',provider_ref=provider_ref,mode=cfg.provider_mode)
        account_row=PagaSubsidiaryAccount(id=uid(),transaction_id=tid,payment_id=payment_id,account_reference=account_reference,account_number=None,provider_reference=provider_ref,status='CREATING')
        s.add(p);s.add(account_row)
        checkout={'type':'PAGA_BANK_TRANSFER','mode':cfg.provider_mode,'bank_name':'Paga','amount_kobo':amount,'amount_naira':client.amount_naira(amount),'account_reference':account_reference,'instructions':'Preparing your transaction-specific Paga account. Do not send money until the account number appears.'}
        d.setdefault('payments',[]).append({'id':p.id,'payment_id':p.id,'status':'PENDING','amount_kobo':amount,'mode':cfg.provider_mode,'provider_ref':provider_ref,'checkout':checkout})
        save_tx(s,tx,d);audit(s,u,'PAGA_PAYMENT_ACCOUNT_REQUESTED','PAYMENT',p.id,{'amount_kobo':amount,'mode':cfg.provider_mode,'account_reference':account_reference})
        # Persist the idempotent intent before calling Paga. A timeout must never
        # lose the reference needed to reconcile a possibly-created account.
        s.commit()
        try:
            account=client.create_transaction_account(reference=provider_ref,account_reference=account_reference,account_name=name,first_name=first_name,last_name=last_name,phone=u.phone,email=u.email,amount_kobo=amount)
            account_row.account_number=str(account['accountNumber']);account_row.status='ACTIVE'
            d=tx_data(tx);d['payments']=[{**x,'checkout':{**x.get('checkout',{}),'account_name':account.get('accountName') or name,'account_number':str(account['accountNumber']),'instructions':'Transfer the exact amount to this Paga account. Funds are not secured until the signed Paga callback and account balance are verified.'}} if x.get('id')==p.id else x for x in d.get('payments',[])]
            save_tx(s,tx,d)
            response={'data':{'payment_id':p.id,'status':'PENDING','amount_kobo':amount,'mode':cfg.provider_mode,'checkout':d['payments'][-1]['checkout'],'transaction_status':tx.status}}
        except PagaUnknownOutcome as exc:
            p.status='UNKNOWN';account_row.status='UNKNOWN'
            d=tx_data(tx);d['payments']=[{**x,'status':'UNKNOWN','checkout':{**x.get('checkout',{}),'instructions':'Paga account creation has an unknown outcome. Do not initiate another payment. SupplyKey is reconciling the original reference.'}} if x.get('id')==p.id else x for x in d.get('payments',[])]
            save_tx(s,tx,d);audit(s,u,'PAGA_ACCOUNT_CREATION_UNKNOWN','PAYMENT',p.id,{'provider_reference':provider_ref,'reason':str(exc)})
            response={'data':{'payment_id':p.id,'status':'UNKNOWN','amount_kobo':amount,'mode':cfg.provider_mode,'checkout':d['payments'][-1]['checkout'],'transaction_status':tx.status}}
        except PagaError as exc:
            p.status='FAILED';account_row.status='FAILED'
            d=tx_data(tx);d['payments']=[{**x,'status':'FAILED','checkout':{**x.get('checkout',{}),'instructions':'Paga could not create the payment account. No transfer should be made.'}} if x.get('id')==p.id else x for x in d.get('payments',[])]
            save_tx(s,tx,d);audit(s,u,'PAGA_ACCOUNT_CREATION_FAILED','PAYMENT',p.id,{'provider_reference':provider_ref,'reason':str(exc)})
            response={'data':{'payment_id':p.id,'status':'FAILED','amount_kobo':amount,'mode':cfg.provider_mode,'checkout':d['payments'][-1]['checkout'],'transaction_status':tx.status}}
    else:
        if not sim.simulator_allowed():fail(503,'SIMULATOR_DISABLED','The local payment simulator is not enabled in this environment.')
        checkout=simulated_checkout()
        p=Payment(id=uid(),transaction_id=tid,amount_kobo=amount,status='PENDING',provider_ref='sim_'+secrets.token_hex(12),mode=sim.MODE); s.add(p)
        d.setdefault('payments',[]).append({'id':p.id,'payment_id':p.id,'status':'PENDING','amount_kobo':amount,'mode':sim.MODE,'simulated':True,'provider_ref':p.provider_ref,'checkout':checkout});save_tx(s,tx,d);audit(s,u,'SIMULATED_PAYMENT_STARTED','PAYMENT',p.id,{'amount_kobo':amount,'mode':sim.MODE})
        response={'data':{'payment_id':p.id,'status':'PENDING','amount_kobo':amount,'mode':sim.MODE,'simulated':True,'checkout':checkout,'transaction_status':tx.status}}
    remember(s,u,endpoint,idempotency_key,digest,response); s.commit(); return response
@app.get('/api/v1/transactions/{tid}/payments')
def payments(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); return {'data':[{'id':p.id,'payment_id':p.id,'status':p.status,'amount_kobo':p.amount_kobo,'mode':p.mode} for p in s.scalars(select(Payment).where(Payment.transaction_id==tid)).all()]}
@app.post('/api/v1/transactions/{tid}/payments/{pid}/refresh')
def refresh_payment(tid:str,pid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); p=s.get(Payment,pid)
    if not p or p.transaction_id!=tid:fail(404,'NOT_FOUND','Payment not found.')
    if p.mode.startswith('PAGA_'):
        account=s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.payment_id==p.id))
        if account:
            try:
                result=PagaClient().get_subsidiary_balance(account.account_reference,'SKBAL'+secrets.token_hex(12).upper())
                balance_kobo=int((Decimal(str(result.get('balance','0')))*100).quantize(Decimal('1')))
                if str(result.get('statusCode'))=='0' and p.status=='PAYMENT_RECEIVED':
                    if balance_kobo==p.amount_kobo:
                        secure_paga_payment(s,p,tx,account,balance_kobo);s.commit()
                    elif balance_kobo>p.amount_kobo:
                        p.status='UNKNOWN';audit(s,u,'PAGA_OVERFUNDING_REQUIRES_REVIEW','PAYMENT',p.id,{'expected_kobo':p.amount_kobo,'balance_kobo':balance_kobo});s.commit()
                    else:
                        d=tx_data(tx);d['payments']=[{**x,'status':'PAYMENT_RECEIVED','checkout':{**x.get('checkout',{}),'amount_received_kobo':balance_kobo,'remaining_kobo':p.amount_kobo-balance_kobo,'instructions':'A partial transfer has been received. Transfer the remaining amount to the same Paga account; funding is not secured until the exact total is confirmed.'}} if x.get('id')==p.id else x for x in d.get('payments',[])];save_tx(s,tx,d);s.commit()
            except (PagaError,InvalidOperation):
                pass
    if p.mode==sim.MODE:release_due_simulation(s,p,tx)
    return {'status':p.status,'transaction_status':tx.status,'mode':p.mode}
def secure_paga_payment(s,p,tx,account,balance_kobo):
    if p.status=='FUNDS_SECURED':return
    if p.status not in ['PENDING','PAYMENT_RECEIVED']:
        return
    if balance_kobo != p.amount_kobo:
        p.status='PAYMENT_RECEIVED' if balance_kobo < p.amount_kobo else 'UNKNOWN'
        return
    p.status='FUNDS_SECURED';tx.status='FUNDED';d=tx_data(tx)
    d['funded_at']=iso();d['dispatch_deadline_at']=iso(now()+timedelta(hours=72));d['config_snapshot']={'dispatch_deadline_hours':72,'high_value_threshold_kobo':50000000}
    d['lots']=[]
    for item in d['items']:
        d['lots'].append({'id':uid(),'item_id':item['id'],'quantity':item['quantity'],'unit_start':1,'unit_end':item['quantity'],'item_status':'AWAITING_DISPATCH','money_status':'HELD','allocation_kobo':item['line_total_kobo'],'is_frozen':False})
    post_journal(s,tx.id,'PAYMENT_RECEIVED',p.id,[('PROVIDER_CLEARING','DEBIT',p.amount_kobo),('RECEIVED_UNSECURED','CREDIT',p.amount_kobo)])
    post_journal(s,tx.id,'FUNDS_SECURED',p.id,[('RECEIVED_UNSECURED','DEBIT',p.amount_kobo),('HELD_FUNDS','CREDIT',p.amount_kobo)])
    d['payments']=[{**x,'status':'FUNDS_SECURED','checkout':{**x.get('checkout',{}),'amount_received_kobo':p.amount_kobo,'remaining_kobo':0,'instructions':'The exact amount has been confirmed in the Paga Subsidiary Account. Funds are secured for this transaction.'}} if x.get('id')==p.id else x for x in d.get('payments',[])]
    tx.data=d;account.status='FUNDED';audit(s,None,'PAGA_FUNDS_SECURED','PAYMENT',p.id,{'mode':p.mode,'account_reference':account.account_reference,'balance_kobo':balance_kobo})
    notify(s,tx.supplier_id,'FUNDS_SECURED',tx.id,{'reference':tx.reference,'mode':p.mode});notify(s,tx.buyer_id,'FUNDS_SECURED',tx.id,{'reference':tx.reference,'mode':p.mode})

@app.post('/api/v1/webhooks/paga/subsidiary-account')
async def paga_subsidiary_webhook(request:Request,s:Session=Depends(db),x_paga_hash_v3:str|None=Header(default=None,alias='x-paga-hash-v3'),x_paga_hash_v3_parameters:str|None=Header(default=None,alias='x-paga-hash-v3-parameters'),x_paga_hash:str|None=Header(default=None,alias='x-paga-hash')):
    raw=await request.body()
    try:
        from decimal import Decimal
        signed_body=json.loads(raw,parse_float=Decimal)
        body=json.loads(raw)
    except Exception:
        fail(400,'VALIDATION_FAILED','Invalid Paga callback JSON.')
    try:
        client=PagaClient()
    except PagaError:
        fail(503,'PAGA_NOT_CONFIGURED','Paga callback endpoint is not configured.')
    operation_signature=x_paga_hash or body.get('hash')
    if operation_signature:
        if not client.verify_subsidiary_operation_webhook(body,operation_signature):
            fail(401,'INVALID_WEBHOOK_SIGNATURE','Paga subsidiary-operation callback signature is invalid.')
        event_id=str(body.get('notificationId') or '')
        if not event_id:fail(400,'VALIDATION_FAILED','Paga subsidiary-operation callback has no notificationId.')
        existing=s.scalar(select(ProviderEvent).where(ProviderEvent.provider_name=='PAGA_SUBSIDIARY_OPS',ProviderEvent.provider_event_id==event_id))
        if existing:return {'status':'SUCCESS','duplicate':True}
        s.add(ProviderEvent(id=uid(),provider_name='PAGA_SUBSIDIARY_OPS',provider_event_id=event_id,event_type=str(body.get('event') or 'SUBSIDIARY_OPERATION'),signature_valid=True,raw_body=body));s.commit()
        return {'status':'SUCCESS','queued':True}
    if not client.verify_subsidiary_funding_webhook(signed_body,x_paga_hash_v3,x_paga_hash_v3_parameters,request.headers):
        event_id=str(body.get('fundingTransactionReference') or body.get('transactionReference') or uid())
        if not s.scalar(select(ProviderEvent).where(ProviderEvent.provider_name=='PAGA',ProviderEvent.provider_event_id==event_id)):
            s.add(ProviderEvent(id=uid(),provider_name='PAGA_INVALID_SIGNATURE',provider_event_id=event_id,event_type='INVALID_SIGNATURE',signature_valid=False,raw_body=body));s.commit()
        fail(401,'INVALID_WEBHOOK_SIGNATURE','Paga callback signature is invalid.')
    event_id=str(body.get('fundingTransactionReference') or body.get('transactionReference') or body.get('fundingPaymentReference') or '')
    if not event_id:fail(400,'VALIDATION_FAILED','Paga funding callback has no unique funding reference.')
    existing=s.scalar(select(ProviderEvent).where(ProviderEvent.provider_name=='PAGA',ProviderEvent.provider_event_id==event_id))
    if existing:return {'status':'SUCCESS','duplicate':True}
    s.add(ProviderEvent(id=uid(),provider_name='PAGA',provider_event_id=event_id,event_type='SUBSIDIARY_ACCOUNT_FUNDING',signature_valid=True,raw_body=body))
    s.commit()
    return {'status':'SUCCESS','queued':True}

@app.post('/api/v1/webhooks/paga/bank-transfer')
async def paga_bank_transfer_webhook(request:Request,s:Session=Depends(db),x_paga_hash:str|None=Header(default=None,alias='x-paga-hash'),x_paga_hash_parameters:str|None=Header(default=None,alias='x-paga-hash-parameters')):
    raw=await request.body()
    try:body=json.loads(raw)
    except Exception:fail(400,'VALIDATION_FAILED','Invalid Paga payout callback JSON.')
    try:client=PagaClient()
    except PagaError:fail(503,'PAGA_NOT_CONFIGURED','Paga callback endpoint is not configured.')
    if not client.verify_bank_transfer_webhook(body,x_paga_hash,x_paga_hash_parameters,request.headers):
        fail(401,'INVALID_WEBHOOK_SIGNATURE','Paga payout callback signature is invalid.')
    event_id=str(body.get('referenceNumber') or '')
    if not event_id:fail(400,'VALIDATION_FAILED','Paga payout callback has no referenceNumber.')
    existing=s.scalar(select(ProviderEvent).where(ProviderEvent.provider_name=='PAGA_PAYOUT',ProviderEvent.provider_event_id==event_id))
    if existing:return {'status':'SUCCESS','duplicate':True}
    s.add(ProviderEvent(id=uid(),provider_name='PAGA_PAYOUT',provider_event_id=event_id,event_type=str(body.get('transactionType') or 'PAYOUT_STATUS'),signature_valid=True,raw_body=body));s.commit()
    return {'status':'SUCCESS','queued':True}

# ---------------------------------------------------------------------------
# Payment callback ingestion (simulated provider) and the local simulator.
#
# Every provider result, whether it arrives over HTTP or is produced by the
# server-side simulator, goes through ingest_payment_event(): signature check,
# payment resolution, reference / currency / integer-kobo amount validation,
# explicit monotonic state-transition check, then one atomic commit that holds
# the payment status, transaction status, ledger journals, audit and event
# record together.
# ---------------------------------------------------------------------------
_payment_locks={};_payment_locks_guard=threading.Lock()
def payment_lock(key):
    """Serialise callbacks for one payment inside this process. PostgreSQL row locks
    (SELECT ... FOR UPDATE) and the ledger/provider-event unique constraints protect
    multi-process deployments; this lock also makes SQLite development deterministic."""
    with _payment_locks_guard:
        return _payment_locks.setdefault(key,threading.Lock())
def simulated_checkout():
    try:default=sim.configured_scenario()
    except ValueError:default='PENDING'
    return {'type':'SIMULATED','simulated':True,'not_paga':True,'provider':'SupplyKey Payment Simulator','label':sim.SIMULATION_LABEL,'scenarios':list(sim.SCENARIOS),'default_scenario':default,'instructions':'This is a SupplyKey payment simulation. Choose an outcome to simulate. No real money moves and nothing is sent to Paga.'}
_SIM_STATUS_TEXT={'FUNDS_SECURED':'Simulated funds secured. This is a SupplyKey simulation, not a Paga settlement.','FAILED':'Simulated payment failed. No funds were secured and nothing was charged. You may start a new attempt.','UNKNOWN':'The simulated provider outcome is unknown. Choose a definitive simulated outcome to reconcile; do not start another attempt.','PAYMENT_RECEIVED':'Simulated payment received; confirming secure holding.'}
def _reject_callback(s,event,code,message,http,payment=None,details=None,review=False):
    """Record a rejected callback safely (no payment or transaction change), then raise."""
    mark_paga_event(s,event,'REJECTED',code)
    audit(s,None,'PAYMENT_CALLBACK_REJECTED','PAYMENT',payment.id if payment else event.id,{'code':code,'provider':event.provider_name,'event_id':event.provider_event_id,'event_status':event.event_type,'mode':sim.MODE,'requires_review':review,**(details or {})})
    s.commit();fail(http,code,message,details)
def fund_transaction(s,p,tx,d):
    """P3/T4: secure funds, create lots and post the FUNDS_SECURED journal. Caller commits."""
    d['funded_at']=iso();d['dispatch_deadline_at']=iso(now()+timedelta(hours=72));d['config_snapshot']={'dispatch_deadline_hours':72,'high_value_threshold_kobo':50000000}
    d['lots']=[{'id':uid(),'item_id':item['id'],'quantity':item['quantity'],'unit_start':1,'unit_end':item['quantity'],'item_status':'AWAITING_DISPATCH','money_status':'HELD','allocation_kobo':item['line_total_kobo'],'is_frozen':False} for item in d['items']]
    post_journal(s,tx.id,'FUNDS_SECURED',p.id,[('RECEIVED_UNSECURED','DEBIT',p.amount_kobo),('HELD_FUNDS','CREDIT',p.amount_kobo)])
def ingest_payment_event(s,provider,raw,signature):
    if provider!=sim.PROVIDER_NAME or not sim.simulator_allowed():fail(404,'NOT_FOUND','Unknown payment webhook provider.')
    try:body=json.loads(raw)
    except Exception:fail(400,'VALIDATION_FAILED','Invalid JSON webhook body.')
    if not isinstance(body,dict):fail(400,'VALIDATION_FAILED','Webhook body must be a JSON object.')
    expected=hmac.new(sim.signing_secret(),raw,hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature,expected):
        # Unsigned traffic must never consume a real (provider, event_id) pair, or an
        # attacker could pre-register event ids and make genuine events look like replays.
        digest=hashlib.sha256(raw).hexdigest();bad=provider+'_INVALID_SIGNATURE'
        if not s.scalar(select(ProviderEvent).where(ProviderEvent.provider_name==bad,ProviderEvent.provider_event_id==digest)):
            s.add(ProviderEvent(id=uid(),provider_name=bad,provider_event_id=digest,event_type='INVALID_SIGNATURE',signature_valid=False,raw_body=body))
            try:s.commit()
            except IntegrityError:s.rollback()
        fail(401,'INVALID_WEBHOOK_SIGNATURE','Webhook signature is invalid.')
    event_id=body.get('event_id');provider_ref=body.get('provider_ref')
    if not isinstance(event_id,str) or not event_id.strip() or len(event_id)>128:fail(400,'VALIDATION_FAILED','event_id is required for replay protection.')
    if not isinstance(provider_ref,str) or not provider_ref.strip():fail(400,'VALIDATION_FAILED','provider_ref is required.')
    with payment_lock(provider_ref.strip()):
        return _ingest_locked(s,provider,body,event_id.strip(),provider_ref.strip())
def _ingest_locked(s,provider,body,event_id,provider_ref):
    if s.scalar(select(ProviderEvent).where(ProviderEvent.provider_name==provider,ProviderEvent.provider_event_id==event_id)):
        return {'received':True,'duplicate':True,'processed':False}
    status=str(body.get('status') or 'UNKNOWN')
    event=ProviderEvent(id=uid(),provider_name=provider,provider_event_id=event_id,event_type=status[:64],signature_valid=True,raw_body=body);s.add(event)
    try:s.flush()
    except IntegrityError:
        s.rollback();return {'received':True,'duplicate':True,'processed':False}
    p=s.scalar(select(Payment).where(Payment.provider_ref==provider_ref).with_for_update())
    if not p:_reject_callback(s,event,'PAYMENT_NOT_FOUND','No payment matches this provider reference.',404)
    tx=s.scalar(select(Aggregate).where(Aggregate.id==p.transaction_id).with_for_update());d=tx_data(tx)
    if p.mode!=sim.MODE:_reject_callback(s,event,'CALLBACK_PROVIDER_MISMATCH','This callback type cannot affect a payment created by another provider.',409,p)
    if status not in ps.EVENT_STATUSES:_reject_callback(s,event,'CALLBACK_STATUS_INVALID','Unsupported callback status.',422,p,{'status':status})
    ref=body.get('transaction_reference')
    if ref is not None and ref!=tx.reference:_reject_callback(s,event,'CALLBACK_REFERENCE_MISMATCH','The callback transaction reference does not match this payment.',422,p)
    currency=body.get('currency')
    if currency is not None and str(currency).upper()!='NGN':_reject_callback(s,event,'CALLBACK_CURRENCY_MISMATCH','The callback currency does not match this payment.',422,p,{'expected_currency':'NGN'})
    amount=body.get('amount_kobo')
    if (status in ps.MONEY_EVENTS or amount is not None) and (isinstance(amount,bool) or not isinstance(amount,int)):
        _reject_callback(s,event,'CALLBACK_AMOUNT_INVALID','The callback must carry the amount as an integer number of kobo.',422,p)
    if amount is not None and amount!=p.amount_kobo:
        _reject_callback(s,event,'CALLBACK_AMOUNT_MISMATCH','The callback amount does not match the stored payment amount.',422,p,{'expected_amount_kobo':p.amount_kobo,'received_amount_kobo':amount},review=True)
    if d.get('total_payable_kobo')!=p.amount_kobo:
        _reject_callback(s,event,'PAYMENT_AMOUNT_INCONSISTENT','The stored payment amount no longer matches the transaction total.',409,p,review=True)
    decision=ps.evaluate_transition(p.status,status)
    if decision.kind==ps.NOOP:
        mark_paga_event(s,event,'IGNORED',decision.reason);s.commit()
        return {'received':True,'processed':False,'reason':decision.reason,'payment_status':p.status,'transaction_status':tx.status}
    if decision.kind==ps.REJECT:
        _reject_callback(s,event,'INVALID_PAYMENT_TRANSITION','This callback is not a valid next state for the payment.',409,p,{'from_status':p.status,'to_status':status,'reason':decision.reason},review=True)
    if status in ps.MONEY_EVENTS and tx.status!='AWAITING_PAYMENT':
        _reject_callback(s,event,'TRANSACTION_NOT_AWAITING_PAYMENT','The transaction can no longer accept funding; manual review is required.',409,p,{'transaction_status':tx.status},review=True)
    before=p.status;new=decision.new_status
    try:
        new_tx_status=None
        if new==ps.PAYMENT_RECEIVED:
            p.status=new;post_journal(s,tx.id,'PAYMENT_RECEIVED',p.id,[('PROVIDER_CLEARING','DEBIT',p.amount_kobo),('RECEIVED_UNSECURED','CREDIT',p.amount_kobo)])
        elif new==ps.FUNDS_SECURED:
            # Collection and hold verified in one step: P2 is applied first so the ledger stays balanced.
            post_journal(s,tx.id,'PAYMENT_RECEIVED',p.id,[('PROVIDER_CLEARING','DEBIT',p.amount_kobo),('RECEIVED_UNSECURED','CREDIT',p.amount_kobo)])
            p.status=new;fund_transaction(s,p,tx,d);new_tx_status='FUNDED'
        else:
            p.status=new
        d['payments']=[{**x,'status':p.status,'checkout':{**x.get('checkout',{}),'instructions':_SIM_STATUS_TEXT.get(p.status,x.get('checkout',{}).get('instructions'))}} if x.get('id')==p.id else x for x in d.get('payments',[])]
        save_tx(s,tx,d,new_tx_status)
        audit(s,None,'PROVIDER_WEBHOOK_PROCESSED','PAYMENT',p.id,{'provider':provider,'mode':sim.MODE,'simulated':True,'event_id':event_id,'from_status':before,'status':p.status})
        if p.status=='FUNDS_SECURED':
            for uid_ in (tx.supplier_id,tx.buyer_id):notify(s,uid_,'FUNDS_SECURED',tx.id,{'reference':tx.reference,'mode':sim.MODE})
        elif p.status=='FAILED':notify(s,tx.buyer_id,'PAYMENT_FAILED',tx.id,{'reference':tx.reference,'mode':sim.MODE})
        mark_paga_event(s,event,'PROCESSED',None);s.commit()
    except IntegrityError:
        s.rollback();return {'received':True,'duplicate':True,'processed':False,'reason':'CONCURRENT_EVENT'}
    return {'received':True,'processed':True,'payment_status':p.status,'transaction_status':tx.status}
@app.post('/api/v1/webhooks/payments/{provider}')
async def payment_webhook(provider:str,request:Request,s:Session=Depends(db),x_sim_signature:str|None=Header(default=None)):
    raw=await request.body()
    # Blocking DB work runs off the event loop so concurrent callbacks are genuinely concurrent.
    return await run_in_threadpool(ingest_payment_event,s,provider,raw,x_sim_signature)
def _event_result(result):
    if result.get('duplicate'):return 'DUPLICATE'
    return 'APPLIED' if result.get('processed') else 'IGNORED:'+str(result.get('reason'))
def deliver_simulated_events(s,p,tx,scenario):
    """Build provider callbacks on the server, sign them with the server-side secret and
    ingest them through the same validated path a real webhook uses."""
    out=[]
    for ev in sim.build_events(scenario,p.id,p.provider_ref,p.amount_kobo,tx.reference):
        raw=json.dumps(ev,separators=(',',':')).encode();sig=hmac.new(sim.signing_secret(),raw,hashlib.sha256).hexdigest()
        out.append({'event_id':ev['event_id'],'status':ev['status'],'result':_event_result(ingest_payment_event(s,sim.PROVIDER_NAME,raw,sig))})
    return out
def _sim_payment_view(p,tx,scenario,events=None,note=None):
    return {'data':{'payment_id':p.id,'status':p.status,'amount_kobo':p.amount_kobo,'mode':sim.MODE,'simulated':True,'not_paga':True,'label':sim.SIMULATION_LABEL,'scenario':scenario,'transaction_status':tx.status,'events':events or [],**({'note':note} if note else {})}}
@app.post('/api/v1/transactions/{tid}/payments/{pid}/simulate')
def simulate_payment(tid:str,pid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    """Run a named simulator scenario for the caller's own simulated payment.
    The client chooses a scenario only. It cannot supply amounts, statuses, references or signatures."""
    if not sim.simulator_allowed():fail(403,'SIMULATOR_DISABLED','The local payment simulator is not enabled in this environment.')
    extra=set(body or {})-{'scenario'}
    if extra:fail(400,'VALIDATION_FAILED','A simulation request accepts only a scenario.',{'rejected_fields':sorted(extra)})
    tx=tx_get(s,tid,u)
    if tx.buyer_id!=u.id:fail(403,'FORBIDDEN','Only the buyer can complete this payment.')
    p=s.get(Payment,pid)
    if not p or p.transaction_id!=tid:fail(404,'NOT_FOUND','Payment not found.')
    if p.mode!=sim.MODE:fail(409,'NOT_A_SIMULATED_PAYMENT','Only SupplyKey-simulated payments can be completed in the simulator.')
    try:default=sim.configured_scenario()
    except ValueError:default='PENDING'
    scenario=sim.normalize_scenario((body or {}).get('scenario'),default)
    if not scenario:fail(400,'VALIDATION_FAILED','Unknown simulator scenario.',{'allowed':list(sim.SCENARIOS)})
    audit(s,u,'SIMULATED_SCENARIO_REQUESTED','PAYMENT',p.id,{'scenario':scenario,'mode':sim.MODE,'payment_status':p.status});s.commit()
    if scenario=='PENDING':
        return _sim_payment_view(p,tx,scenario,note='Payment left pending by the simulator. Nothing was secured.')
    if scenario=='DELAYED':
        if p.status!='PENDING':fail(409,'INVALID_PAYMENT_TRANSITION','Only a pending simulated payment can be delayed.',{'payment_status':p.status})
        release_at=iso(now()+timedelta(seconds=sim.delay_seconds()));d=tx_data(tx)
        d['payments']=[{**x,'simulation':{'scenario':'DELAYED','release_at':release_at,'requested_by':u.id}} if x.get('id')==p.id else x for x in d.get('payments',[])]
        save_tx(s,tx,d);audit(s,u,'SIMULATED_PAYMENT_DELAYED','PAYMENT',p.id,{'release_at':release_at,'mode':sim.MODE});s.commit()
        return _sim_payment_view(p,tx,scenario,note=f'The simulated provider will confirm this payment at {release_at}. It remains pending until then.')
    events=deliver_simulated_events(s,p,tx,scenario)
    s.refresh(p);s.refresh(tx)
    return _sim_payment_view(p,tx,scenario,events)
def release_due_simulation(s,p,tx=None):
    """Deliver a DELAYED scenario's SUCCESS events once their release time has passed. Idempotent."""
    if p.mode!=sim.MODE or p.status!='PENDING' or not sim.simulator_allowed():return False
    tx=tx or s.get(Aggregate,p.transaction_id);d=tx_data(tx)
    entry=next((x for x in d.get('payments',[]) if x.get('id')==p.id),None);simu=(entry or {}).get('simulation')
    if not simu or simu.get('scenario')!='DELAYED' or simu.get('released_at'):return False
    if datetime.fromisoformat(simu['release_at'].replace('Z','+00:00'))>now():return False
    outcome='APPLIED'
    try:deliver_simulated_events(s,p,tx,'DELAYED')
    except HTTPException as exc:outcome='REJECTED:'+str((exc.detail or {}).get('error',{}).get('code') if isinstance(exc.detail,dict) else exc.detail)
    s.refresh(tx);d=tx_data(tx)
    d['payments']=[{**x,'simulation':{**x.get('simulation',{}),'released_at':iso(),'outcome':outcome}} if x.get('id')==p.id else x for x in d.get('payments',[])]
    save_tx(s,tx,d);audit(s,None,'SIMULATED_DELAYED_PAYMENT_RELEASED','PAYMENT',p.id,{'mode':sim.MODE,'outcome':outcome});s.commit();return True
def run_simulated_payments(s:Session):
    """Worker step: release due DELAYED simulated payments. No-op outside simulator environments."""
    if not sim.simulator_allowed():return {'released':0,'mode':sim.MODE,'enabled':False}
    released=0
    for p in s.scalars(select(Payment).where(Payment.mode==sim.MODE,Payment.status=='PENDING')).all():
        if release_due_simulation(s,p):released+=1
    return {'released':released,'mode':sim.MODE,'enabled':True}
@app.post('/api/v1/transactions/{tid}/dispatch')
def dispatch(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if tx.supplier_id!=u.id or tx.status!='FUNDED':fail(409,'INVALID_STATE_TRANSITION','Only the supplier can dispatch a funded transaction.')
    if datetime.fromisoformat(d['dispatch_deadline_at'].replace('Z','+00:00'))<now():fail(409,'DISPATCH_DEADLINE_PASSED','Dispatch deadline passed; a dispute must be opened.')
    evidence=body.get('evidence_ids') or []
    if not evidence:fail(422,'EVIDENCE_REQUIRED','Dispatch evidence is required.')
    verify_evidence(s,evidence,tx,u)
    results=[]
    for line in body.get('lines',[]):
        item=next((x for x in d['items'] if x['id']==line.get('item_id')),None); qty=line.get('quantity',0)
        lot=next((x for x in d['lots'] if x['item_id']==line.get('item_id') and x['item_status']=='AWAITING_DISPATCH'),None)
        if not item or not lot or not isinstance(qty,int) or qty<1 or qty>lot['quantity']:fail(422,'QUANTITY_EXCEEDS_AVAILABLE','Requested dispatch quantity is not available.')
        if qty<lot['quantity']:
            remaining=lot['quantity']-qty; lot['quantity']=qty; lot['unit_end']=lot.get('unit_start',1)+qty-1
            d['lots'].append({'id':uid(),'item_id':item['id'],'quantity':remaining,'unit_start':lot['unit_end']+1,'unit_end':lot['unit_end']+remaining,'item_status':'AWAITING_DISPATCH','money_status':'HELD','allocation_kobo':remaining*item['unit_price_kobo'],'is_frozen':False})
        lot['item_status']='DISPATCHED'; results.append({'lot_id':lot['id'],'item_id':item['id'],'quantity':qty,'item_status':'DISPATCHED'})
    dsp={'id':uid(),'declared_at':iso(),'courier_name':body.get('courier_name'),'rider_name':body.get('rider_name'),'rider_phone':body.get('rider_phone'),'tracking_ref':body.get('tracking_ref'),'lines':body.get('lines',[]),'evidence_ids':evidence,'note':body.get('note')}; d.setdefault('dispatches',[]).append(dsp)
    req={'id':uid(),'party_role':'SUPPLIER','source_type':'DISPATCH','status':'PENDING','due_at':iso(now()+timedelta(days=int(d.get('delivery',{}).get('expected_within_days',5))+1)),'submitted_at':None}; d.setdefault('delivery_photo_requirements',[]).append(req)
    save_tx(s,tx,d); audit(s,u,'DISPATCH_DECLARED','TRANSACTION',tid,{'dispatch_id':dsp['id']});notify(s,tx.buyer_id,'DISPATCH_DECLARED',tid,{'reference':tx.reference}); s.commit(); return {'data':{'dispatch_id':dsp['id'],'lots':results,'dispatch_deadline_at':d['dispatch_deadline_at'],'delivery_photo_requirement':req}}
@app.post('/api/v1/transactions/{tid}/delivery/evidence')
def delivery_evidence(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx); req=next((r for r in d.get('delivery_photo_requirements',[]) if r['id']==body.get('requirement_id')),None)
    role='SUPPLIER' if tx.supplier_id==u.id else 'BUYER'
    if not req or req['party_role']!=role:fail(403,'FORBIDDEN','Delivery photo requirement does not belong to this party.')
    if not body.get('evidence_ids'):fail(422,'EVIDENCE_REQUIRED','At least one delivery photograph is required.')
    verify_evidence(s,body['evidence_ids'],tx,u)
    req['status']='SUBMITTED_LATE' if datetime.fromisoformat(req['due_at'].replace('Z','+00:00'))<now() else 'SUBMITTED'; req['submitted_at']=iso(); req.setdefault('evidence_ids',[]).extend(body['evidence_ids']); save_tx(s,tx,d); s.commit(); return {'requirement':req,'lots_changed':[]}
@app.post('/api/v1/transactions/{tid}/delivery/confirm-received')
def confirm_received(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if tx.buyer_id!=u.id or tx.status!='FUNDED':fail(409,'INVALID_STATE_TRANSITION','Only the buyer can confirm receipt of funded goods.')
    req={'id':uid(),'party_role':'BUYER','source_type':'RECEIPT_CONFIRMATION','status':'PENDING','due_at':iso(now()+timedelta(hours=72)),'submitted_at':None}
    out=[]
    for line in body.get('lines',[]):
        remain=line.get('quantity',0); iid=line.get('item_id')
        for lot in d.get('lots',[]):
            if lot['item_id']==iid and lot['item_status']=='DISPATCHED' and remain:
                take=min(remain,lot['quantity']); item=next(i for i in d['items'] if i['id']==iid)
                if take<lot['quantity']:
                    extra=split_lot(lot,take,item); d['lots'].append(extra)
                lot['item_status']='RECEIPT_CONFIRMED'; lot['inspection_deadline_at']=iso(now()+timedelta(hours=72));lot['receipt_photo_requirement_id']=req['id']; out.append({'lot_id':lot['id'],'item_status':lot['item_status'],'quantity':take}); remain-=take
        if remain:fail(422,'QUANTITY_EXCEEDS_AVAILABLE','Quantity exceeds dispatched lots.')
    d.setdefault('delivery_photo_requirements',[]).append(req); d.setdefault('receipt_confirmations',[]).append({'id':uid(),'confirmed_at':iso(),'lines':body.get('lines',[])})
    save_tx(s,tx,d); audit(s,u,'PHYSICAL_RECEIPT_CONFIRMED','TRANSACTION',tid);notify(s,tx.supplier_id,'RECEIPT_CONFIRMED',tid,{'reference':tx.reference}); s.commit(); return {'lots':out,'delivery_photo_requirement':req}
def verify_evidence(s,ids,tx,user):
    for eid in ids:
        ev=s.get(Evidence,eid)
        if not ev or ev.transaction_id!=tx.id or ev.uploader_id!=user.id or ev.status!='VERIFIED':fail(422,'EVIDENCE_NOT_VERIFIED','Evidence must be uploaded and verified before use.',{'evidence_id':eid})
def split_lot(lot,qty,item):
    if qty<1 or qty>lot['quantity']:fail(422,'QUANTITY_EXCEEDS_AVAILABLE','Quantity exceeds available lot quantity.')
    if qty==lot['quantity']:return lot
    original=lot['quantity']; lot['quantity']=qty; lot['allocation_kobo']=qty*item['unit_price_kobo']; lot['unit_end']=lot.get('unit_start',1)+qty-1
    rest={'id':uid(),'item_id':lot['item_id'],'quantity':original-qty,'unit_start':lot['unit_end']+1,'unit_end':lot.get('unit_start',1)+original-1,'item_status':lot['item_status'],'money_status':lot.get('money_status','HELD'),'allocation_kobo':(original-qty)*item['unit_price_kobo'],'is_frozen':lot.get('is_frozen',False)}
    return rest
@app.post('/api/v1/transactions/{tid}/decisions')
def submit_decisions(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if tx.buyer_id!=u.id:fail(403,'FORBIDDEN','Only the buyer may submit acceptance or rejection.')
    buyer_req=next((r for r in d.get('delivery_photo_requirements',[]) if r['party_role']=='BUYER' and r['status'] in ['SUBMITTED','SUBMITTED_LATE']),None)
    if not buyer_req:fail(422,'DELIVERY_PHOTOS_REQUIRED','Buyer delivery photographs are required before acceptance or rejection.')
    decisions=[]
    for line in body.get('lines',[]):
        item=next((x for x in d['items'] if x['id']==line.get('item_id')),None)
        if not item:fail(404,'NOT_FOUND','Item not found.')
        accepted=int(line.get('quantity_accepted',0)); rejected=int(line.get('quantity_rejected',0)); total=accepted+rejected
        if total<1:continue
        if accepted and not line.get('accept_evidence_ids'):fail(422,'EVIDENCE_REQUIRED','Acceptance evidence is required.')
        if rejected:
            rej=line.get('reject') or {}
            if not rej.get('evidence_ids') or not rej.get('agreed_condition_ids'):fail(422,'EVIDENCE_REQUIRED','Rejection evidence and agreed condition references are required.')
            allowed={c['id'] for c in item.get('conditions',[]) if c.get('kind')=='REJECTION'}
            if not set(rej['agreed_condition_ids']).issubset(allowed):fail(422,'REJECTION_REASON_NOT_IN_AGREEMENT','Rejection condition must belong to the agreed agreement version.')
        ids=(line.get('accept_evidence_ids') or [])+((line.get('reject') or {}).get('evidence_ids') or []); verify_evidence(s,ids,tx,u)
        for status,qty in [('SUBMITTED_FOR_REVIEW',accepted),('REJECTED_AWAITING_RETURN',rejected)]:
            if not qty:continue
            lot=next((x for x in d['lots'] if x['item_id']==item['id'] and x['item_status']=='RECEIPT_CONFIRMED' and x['quantity']>=qty),None)
            if not lot:fail(409,'ITEM_NOT_DECIDABLE','No received lot of the requested quantity is available.')
            required_req=next((r for r in d.get('delivery_photo_requirements',[]) if r['id']==lot.get('receipt_photo_requirement_id')),None)
            if not required_req or required_req.get('status') not in ['SUBMITTED','SUBMITTED_LATE']:fail(422,'DELIVERY_PHOTOS_REQUIRED','Delivery photographs are required for this receipt quantity.')
            extra=split_lot(lot,qty,item)
            if extra is not lot:d['lots'].append(extra)
            lot['item_status']=status
            decisions.append({'id':uid(),'item_id':item['id'],'lot_id':lot['id'],'quantity':qty,'review_status':'SUBMITTED_FOR_REVIEW' if status=='SUBMITTED_FOR_REVIEW' else 'REJECTED_AWAITING_RETURN','evidence_ids':ids,'reason':(line.get('reject') or {}).get('reason_text')})
    window={'id':uid(),'status':'OPEN','ends_at':iso(now()+timedelta(hours=48)),'waivers':{'BUYER':False,'SUPPLIER':False}}
    for dec in decisions:
        dec['evidence_window']=window
    d.setdefault('decisions',[]).extend(decisions); save_tx(s,tx,d); audit(s,u,'BUYER_DECISION_SUBMITTED','TRANSACTION',tid,{'decision_ids':[x['id'] for x in decisions]});notify(s,tx.supplier_id,'BUYER_DECISION_SUBMITTED',tid,{'reference':tx.reference}); s.commit()
    return {'data':{'submission_id':uid(),'decisions':decisions,'lots':[{'lot_id':l['id'],'item_status':l['item_status'],'money_status':l.get('money_status','HELD')} for l in d['lots']],'next_steps':{'return_required_for':[{'item_id':x['item_id'],'quantity':x['quantity']} for x in decisions if x['review_status']=='REJECTED_AWAITING_RETURN']}}}
@app.get('/api/v1/decisions/{did}')
def decision_detail(did:str,s:Session=Depends(db),u:User=Depends(current_user)):
    for tx in s.scalars(select(Aggregate)).all():
        for d in tx.data.get('decisions',[]):
            if d['id']==did: tx_get(s,tx.id,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']); return {'decision':d,'transaction_id':tx.id,'version':tx.version,'evidence':[]}
    fail(404,'NOT_FOUND','Decision not found.')
@app.post('/api/v1/decisions/{did}/evidence')
def decision_evidence(did:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx,d,dec=find_decision(s,did,u); ids=body.get('evidence_ids',[]); verify_evidence(s,ids,tx,u); dec.setdefault('additional_evidence_ids',[]).extend(ids); save_tx(s,tx,d); s.commit(); return {'status':'ADDED'}
@app.post('/api/v1/decisions/{did}/waive-window')
def waive_decision(did:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx,d,dec=find_decision(s,did,u); role='BUYER' if tx.buyer_id==u.id else 'SUPPLIER'; dec['evidence_window']['waivers'][role]=True
    if all(dec['evidence_window']['waivers'].values()):dec['evidence_window']['status']='CLOSED_WAIVED'
    save_tx(s,tx,d);s.commit(); return {'status':dec['evidence_window']['status']}
def find_decision(s,did,u):
    for tx in s.scalars(select(Aggregate)).all():
        d=tx_data(tx)
        for dec in d.get('decisions',[]):
            if dec['id']==did: tx_get(s,tx.id,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']); return tx,d,dec
    fail(404,'NOT_FOUND','Decision not found.')
def find_instruction(s,iid):
    ins=s.get(Instruction,iid)
    if not ins:fail(404,'NOT_FOUND','Financial instruction not found.')
    return ins
@app.get('/api/v1/mod/acceptance-queue')
def acceptance_queue(s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']); rows=[]
    for tx in s.scalars(select(Aggregate).where(Aggregate.status=='FUNDED')).all():
        for dec in tx.data.get('decisions',[]):
            if dec['review_status']=='SUBMITTED_FOR_REVIEW': rows.append({'id':dec['id'],'version':tx.version,'transaction_id':tx.id,'reference':tx.reference,'item_id':dec['item_id'],'quantity':dec['quantity'],'status':dec['review_status'],'window_status':dec['evidence_window']['status'],'reason':dec.get('reason','')})
    return {'data':rows,'meta':{'server_time':iso(),'next_cursor':None}}
@app.post('/api/v1/mod/decisions/{did}/confirm')
def confirm_acceptance(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']); tx,d,dec=find_decision(s,did,u);check_version(request,tx)
    if u.id in [tx.supplier_id,tx.buyer_id]:fail(403,'MODERATOR_IS_PARTY','A transaction party cannot moderate this case.')
    if dec['review_status']!='SUBMITTED_FOR_REVIEW' or dec['evidence_window']['status'] not in ['CLOSED_ELAPSED','CLOSED_WAIVED']:fail(409,'EVIDENCE_WINDOW_OPEN','Evidence window must be closed before confirmation.')
    if not body.get('reason') or not body.get('evidence_ids_considered'):fail(400,'VALIDATION_FAILED','Decision reason and evidence considered are required.')
    for eid in body['evidence_ids_considered']:
        ev=s.get(Evidence,eid)
        if not ev or ev.transaction_id!=tx.id or ev.status!='VERIFIED':fail(422,'EVIDENCE_NOT_VERIFIED','Considered evidence must be verified and belong to this transaction.',{'evidence_id':eid})
    supplier_photos=[r for r in d.get('delivery_photo_requirements',[]) if r.get('party_role')=='SUPPLIER']
    if any(r.get('status')=='PENDING' for r in supplier_photos):fail(409,'SUPPLIER_DELIVERY_PHOTOS_PENDING','Supplier delivery photographs are still pending.')
    if any(r.get('status')=='OVERDUE' for r in supplier_photos) and not (body.get('acknowledge_missing_supplier_photos') and body.get('acknowledgement_reason')):fail(422,'ACKNOWLEDGEMENT_REQUIRED','Acknowledge overdue supplier photographs with a reason.')
    payout_amount=0; lots=[]
    for lot in d['lots']:
        if lot['id']==dec['lot_id']:
            lot['item_status']='ACCEPTED_CONFIRMED'; amount=lot.get('allocation_kobo',lot['quantity']*next(i['unit_price_kobo'] for i in d['items'] if i['id']==lot['item_id'])); payout_amount+=amount; lots.append(lot)
    threshold=d.get('config_snapshot',{}).get('high_value_threshold_kobo',50000000); status='PENDING_APPROVAL' if payout_amount>=threshold else 'QUEUED'
    ins=Instruction(id=uid(),transaction_id=tx.id,instruction_type='PAYOUT',amount_kobo=payout_amount,status=status,origin_id=did,deciding_moderator_id=u.id,idempotency_key='decision:'+did); s.add(ins)
    if lots:
        for lot in lots:lot['money_status']='RELEASE_PENDING'
    dec['review_status']='CONFIRMED'; dec['moderator_decision']={'moderator_id':u.id,'reason':body['reason'],'evidence_ids_considered':body['evidence_ids_considered'],'created_at':iso()}; save_tx(s,tx,d); audit(s,u,'ACCEPTANCE_CONFIRMED','DECISION',did,{'instruction_id':ins.id,'amount_kobo':payout_amount});notify(s,tx.supplier_id,'PAYOUT_INSTRUCTION_CREATED',tx.id,{'instruction_id':ins.id,'status':ins.status}); s.commit()
    return {'data':{'decision_id':did,'review_status':'CONFIRMED','lots':[{'lot_id':l['id'],'item_status':l['item_status'],'money_status':l['money_status']} for l in lots],'payout':{'id':ins.id,'status':ins.status,'amount_kobo':ins.amount_kobo,'origin_type':'ACCEPTANCE_CONFIRMATION'}}}
@app.post('/api/v1/mod/decisions/{did}/request-evidence')
def request_evidence(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']); tx,d,dec=find_decision(s,did,u);check_version(request,tx); dec['review_status']='EVIDENCE_REQUESTED'; dec.setdefault('evidence_requests',[]).append({'id':uid(),**body,'status':'OPEN'}); save_tx(s,tx,d);s.commit(); return {'status':'EVIDENCE_REQUESTED'}
@app.post('/api/v1/mod/decisions/{did}/open-dispute')
def decision_dispute(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']); tx,d,dec=find_decision(s,did,u);check_version(request,tx); dec['review_status']='DISPUTED'; disp={'id':uid(),'reason_code':body.get('reason_code'),'reason':body.get('reason'),'status':'EVIDENCE_WINDOW_OPEN','created_at':iso()}; d['dispute']=disp
    for lot in d['lots']:
        if lot['id']==dec['lot_id']:lot['pre_dispute_status']=lot['item_status'];lot['item_status']='IN_DISPUTE';lot['is_frozen']=True;lot['freeze_dispute_id']=disp['id']
    s.add(Dispute(id=disp['id'],transaction_id=tx.id,reason_code=disp['reason_code'] or 'MODERATOR_REVIEW',data=disp)); save_tx(s,tx,d); s.commit(); return {'status':'DISPUTED','dispute_id':disp['id']}
@app.post('/api/v1/mod/decisions/{did}/extend-window')
def extend_window(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']); tx,d,dec=find_decision(s,did,u);check_version(request,tx); w=dec['evidence_window']
    if w.get('extended'):fail(409,'WINDOW_ALREADY_EXTENDED','Evidence window can only be extended once.')
    hours=int(body.get('extra_hours',0));
    if not body.get('reason') or hours<1:fail(400,'VALIDATION_FAILED','Reason and positive extra_hours are required.')
    w['ends_at']=iso(datetime.fromisoformat(w['ends_at'].replace('Z','+00:00'))+timedelta(hours=hours));w['extended']=True;save_tx(s,tx,d);s.commit();return {'window_status':'OPEN','ends_at':w['ends_at']}
@app.post('/api/v1/transactions/{tid}/returns')
def open_return(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u); d=tx_data(tx)
    if tx.buyer_id!=u.id:fail(403,'FORBIDDEN','Only the buyer can initiate a return.')
    lines=body.get('lines',[]); selected=[]
    for line in lines:
        lot=next((x for x in d['lots'] if x['item_id']==line.get('item_id') and x['item_status']=='REJECTED_AWAITING_RETURN' and x['quantity']>=line.get('quantity',0)),None)
        if not lot:fail(409,'INVALID_STATE_TRANSITION','Return quantity is not awaiting return.')
        item=next(x for x in d['items'] if x['id']==lot['item_id']);qty=int(line['quantity'])
        if qty<lot['quantity']:
            extra=split_lot(lot,qty,item);d['lots'].append(extra)
        selected.append({'lot_id':lot['id'],'item_id':lot['item_id'],'quantity':qty})
    ret={'id':uid(),'status':'AWAITING_HANDOVER_EVIDENCE','lines':selected,'created_at':iso(),'handover_deadline_at':None}; d.setdefault('returns',[]).append(ret); save_tx(s,tx,d); s.commit(); return {'data':ret}
@app.get('/api/v1/returns/{rid}')
def return_detail(rid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    for tx in s.scalars(select(Aggregate)).all():
        for ret in tx.data.get('returns',[]):
            if ret['id']==rid:tx_get(s,tx.id,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']);return ret
    fail(404,'NOT_FOUND','Return not found.')
def find_return(s,rid,u):
    for tx in s.scalars(select(Aggregate)).all():
        d=tx_data(tx)
        for ret in d.get('returns',[]):
            if ret['id']==rid:tx_get(s,tx.id,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']);return tx,d,ret
    fail(404,'NOT_FOUND','Return not found.')
@app.post('/api/v1/returns/{rid}/handover-evidence')
def return_handover(rid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx,d,ret=find_return(s,rid,u)
    if tx.buyer_id!=u.id or ret['status']!='AWAITING_HANDOVER_EVIDENCE':fail(409,'INVALID_STATE_TRANSITION','Return is not awaiting handover evidence.')
    ids=body.get('evidence_ids',[])
    if not ids:fail(422,'EVIDENCE_REQUIRED','Return handover evidence is required.')
    verify_evidence(s,ids,tx,u);ret.update({'status':'HANDOVER_RECORDED','handover_evidence_ids':ids,'courier_name':body.get('courier_name'),'rider_name':body.get('rider_name'),'rider_phone':body.get('rider_phone'),'tracking_ref':body.get('tracking_ref'),'confirmation_deadline_at':iso(now()+timedelta(hours=72)),'handover_committed_at':iso()})
    for line in ret['lines']:
        lot=next(x for x in d['lots'] if x['id']==line['lot_id']);lot['item_status']='RETURN_HANDOVER_RECORDED'
    save_tx(s,tx,d);s.commit();return {'status':ret['status'],'confirmation_deadline_at':ret['confirmation_deadline_at']}
@app.post('/api/v1/returns/{rid}/additional-evidence')
def return_additional(rid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx,d,ret=find_return(s,rid,u);ids=body.get('evidence_ids',[]);verify_evidence(s,ids,tx,u);ret.setdefault('additional_evidence_ids',[]).extend(ids);s.commit();return {'status':'ADDED'}
@app.post('/api/v1/returns/{rid}/confirm-receipt')
def return_confirm(rid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx,d,ret=find_return(s,rid,u)
    if tx.supplier_id!=u.id or ret['status']!='HANDOVER_RECORDED':fail(409,'INVALID_STATE_TRANSITION','Supplier confirmation is not available.')
    ids=body.get('evidence_ids',[]);verify_evidence(s,ids,tx,u)
    total=0
    for line in body.get('lines',[]):
        lot=next((x for x in d['lots'] if x['item_id']==line.get('item_id') and x['item_status']=='RETURN_HANDOVER_RECORDED'),None)
        if not lot:fail(409,'INVALID_STATE_TRANSITION','Returned lot is not awaiting receipt confirmation.')
        qty=int(line.get('quantity_received',0)); item=next(x for x in d['items'] if x['id']==lot['item_id'])
        if qty<1 or qty>lot['quantity']:fail(422,'QUANTITY_EXCEEDS_AVAILABLE','Return receipt quantity is invalid.')
        if qty<lot['quantity']:
            old_qty=lot['quantity'];extra=split_lot(lot,qty,item);d['lots'].append(extra)
            for ret_line in ret['lines']:
                if ret_line['lot_id']==lot['id']:
                    ret_line['quantity']=qty;break
            ret['lines'].append({'lot_id':extra['id'],'item_id':extra['item_id'],'quantity':extra['quantity']})
        lot['item_status']='RETURN_RECEIPT_CONFIRMED';lot['money_status']='REFUND_PENDING';total+=qty*item['unit_price_kobo']
    ret['status']='HANDOVER_RECORDED' if any(any(l['id']==line['lot_id'] and l['item_status']=='RETURN_HANDOVER_RECORDED' for l in d['lots']) for line in ret['lines']) else 'RECEIPT_CONFIRMED';ret['supplier_evidence_ids']=ids
    threshold=d.get('config_snapshot',{}).get('high_value_threshold_kobo',50000000);status='PENDING_APPROVAL' if total>=threshold else 'QUEUED'
    ins=Instruction(id=uid(),transaction_id=tx.id,instruction_type='REFUND',amount_kobo=total,status=status,origin_id=rid,deciding_moderator_id=None,idempotency_key='return:'+rid);s.add(ins)
    save_tx(s,tx,d);audit(s,u,'RETURN_RECEIPT_CONFIRMED','RETURN',rid,{'refund_id':ins.id,'amount_kobo':total});notify(s,tx.buyer_id,'REFUND_INSTRUCTION_CREATED',tx.id,{'instruction_id':ins.id,'status':ins.status});s.commit();return {'status':ret['status'],'refund':{'id':ins.id,'status':status,'amount_kobo':total}}
@app.post('/api/v1/returns/{rid}/contest')
def return_contest(rid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx,d,ret=find_return(s,rid,u)
    if tx.supplier_id!=u.id:fail(403,'FORBIDDEN','Only the supplier can contest returned goods.')
    verify_evidence(s,body.get('evidence_ids',[]),tx,u)
    disp={'id':uid(),'reason_code':'RETURN_CONTEST','status':'EVIDENCE_WINDOW_OPEN','statement':body.get('note'),'lines':body.get('lines',[]),'created_at':iso()};d['dispute']=disp;ret['status']='CONTESTED'
    for line in ret['lines']:
        lot=next(x for x in d['lots'] if x['id']==line['lot_id']);lot['pre_dispute_status']=lot['item_status'];lot['item_status']='IN_DISPUTE';lot['is_frozen']=True;lot['freeze_dispute_id']=disp['id']
    s.add(Dispute(id=disp['id'],transaction_id=tx.id,reason_code='RETURN_CONTEST',data=disp));save_tx(s,tx,d);s.commit();return {'status':'DISPUTED','dispute_id':disp['id']}
@app.post('/api/v1/transactions/{tid}/disputes')
def raise_dispute(tid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u);d=tx_data(tx)
    if u.id not in [tx.buyer_id,tx.supplier_id]:fail(403,'FORBIDDEN','Only a transaction party may raise a dispute.')
    if d.get('dispute') and d['dispute'].get('status') not in ['RESOLVED','CLOSED']:fail(409,'DISPUTE_ALREADY_OPEN','A dispute is already open.')
    disp={'id':uid(),'reason_code':body.get('reason_code','OTHER'),'statement':body.get('statement'),'lines':body.get('lines',[]),'status':'EVIDENCE_WINDOW_OPEN','created_at':iso(),'window_ends_at':iso(now()+timedelta(hours=48))};d['dispute']=disp
    for lot in d['lots']:
        if any(lot['item_id']==line['item_id'] for line in body.get('lines',[])):lot['pre_dispute_status']=lot['item_status'];lot['item_status']='IN_DISPUTE';lot['is_frozen']=True;lot['freeze_dispute_id']=disp['id']
    s.add(Dispute(id=disp['id'],transaction_id=tid,reason_code=disp['reason_code'],data=disp));save_tx(s,tx,d);audit(s,u,'DISPUTE_OPENED','DISPUTE',disp['id']);s.commit();return {'data':disp}
@app.get('/api/v1/disputes/{did}')
def dispute_detail(did:str,s:Session=Depends(db),u:User=Depends(current_user)):
    disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    tx=tx_get(s,disp.transaction_id,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']);return disp.data
@app.post('/api/v1/disputes/{did}/evidence')
def dispute_evidence(did:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    tx=tx_get(s,disp.transaction_id,u);ids=body.get('evidence_ids',[]);verify_evidence(s,ids,tx,u);d=tx_data(tx);submission={'actor_id':u.id,'kind':body.get('kind','STATEMENT'),'evidence_ids':ids,'statement':body.get('statement'),'created_at':iso()};d.setdefault('dispute_evidence',[]).append(submission)
    for req in disp.data.get('evidence_requests',[]):
        if req.get('status')=='OPEN' and req.get('party') in ['BUYER','SUPPLIER'] and req.get('party')==('BUYER' if u.id==tx.buyer_id else 'SUPPLIER'):req['status']='ANSWERED'
    disp.data={**disp.data,'evidence_submissions':disp.data.get('evidence_submissions',[])+[submission]}
    if not any(x.get('status')=='OPEN' for x in disp.data.get('evidence_requests',[])) and disp.status=='AWAITING_EVIDENCE':disp.status='MODERATOR_REVIEW';disp.data['status']='MODERATOR_REVIEW'
    save_tx(s,tx,d);s.commit();return {'status':'ADDED'}
@app.post('/api/v1/disputes/{did}/waive-window')
def dispute_waive(did:str,s:Session=Depends(db),u:User=Depends(current_user)):
    disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    tx=tx_get(s,disp.transaction_id,u);d=tx_data(tx);w=disp.data.setdefault('waivers',{'BUYER':False,'SUPPLIER':False});w['BUYER' if u.id==tx.buyer_id else 'SUPPLIER']=True
    if all(w.values()):disp.data['status']='EVIDENCE_WINDOW_CLOSED_WAIVED';disp.data['window_closed']=True;disp.status='EVIDENCE_WINDOW_CLOSED_WAIVED';d.get('dispute',{})['status']='EVIDENCE_WINDOW_CLOSED_WAIVED';d.get('dispute',{})['window_closed']=True;save_tx(s,tx,d)
    s.commit();return {'status':disp.data['status']}
@app.get('/api/v1/mod/disputes')
def mod_disputes(s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);return {'data':[{'id':x.id,'transaction_id':x.transaction_id,'reason_code':x.reason_code,'status':x.status,**x.data} for x in s.scalars(select(Dispute)).all()],'meta':{'server_time':iso(),'next_cursor':None}}
@app.get('/api/v1/mod/disputes/{did}')
def mod_dispute_detail(did:str,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    tx=s.get(Aggregate,disp.transaction_id);return {'dispute':disp.data,'transaction':present_tx(tx,None),'audit':[],'version':tx.version}
@app.post('/api/v1/mod/disputes/{did}/assign')
def assign_dispute(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    check_version(request,s.get(Aggregate,disp.transaction_id))
    disp.data={**disp.data,'assigned_to':body.get('moderator_id',u.id)};s.commit();return {'status':'ASSIGNED'}
@app.post('/api/v1/mod/disputes/{did}/request-evidence')
def dispute_request_evidence(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    check_version(request,s.get(Aggregate,disp.transaction_id))
    disp.data.setdefault('evidence_requests',[]).append({'id':uid(),**body,'status':'OPEN'});disp.status='AWAITING_EVIDENCE';s.commit();return {'status':disp.status}
@app.post('/api/v1/mod/disputes/{did}/notes')
def dispute_note(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    check_version(request,s.get(Aggregate,disp.transaction_id))
    disp.data.setdefault('internal_notes',[]).append({'id':uid(),'actor_id':u.id,'note':body.get('note'),'created_at':iso()});s.commit();return {'status':'RECORDED'}
@app.post('/api/v1/mod/disputes/{did}/hold')
@app.post('/api/v1/mod/disputes/{did}/resume')
def dispute_hold_resume(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    check_version(request,s.get(Aggregate,disp.transaction_id))
    disp.status='ON_HOLD' if request and request.url.path.endswith('/hold') else 'MODERATOR_REVIEW';disp.data={**disp.data,'moderator_reason':body.get('reason')};s.commit();return {'status':disp.status}
@app.post('/api/v1/mod/disputes/{did}/extend-window')
def dispute_extend(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    check_version(request,s.get(Aggregate,disp.transaction_id))
    if disp.data.get('window_extended'):fail(409,'WINDOW_ALREADY_EXTENDED','Window can only be extended once.')
    disp.data['window_extended']=True;disp.data['window_ends_at']=iso(now()+timedelta(hours=int(body.get('extra_hours',24))));s.commit();return {'status':'OPEN','window_ends_at':disp.data['window_ends_at']}
@app.post('/api/v1/mod/disputes/{did}/decision')
def dispute_decision(did:str,body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);disp=s.get(Dispute,did)
    if not disp:fail(404,'NOT_FOUND','Dispute not found.')
    if not body.get('reason') or not body.get('outcomes') or not body.get('evidence_ids_considered'):
        fail(400,'VALIDATION_FAILED','Reason, evidence considered and quantity-level outcomes are required.')
    if disp.status not in ['EVIDENCE_WINDOW_CLOSED_ELAPSED','EVIDENCE_WINDOW_CLOSED_WAIVED'] and not disp.data.get('window_closed'):
        fail(409,'EVIDENCE_WINDOW_OPEN','Dispute evidence window must be closed before a decision.')
    if any(x.get('status')=='OPEN' for x in disp.data.get('evidence_requests',[])):
        fail(409,'EVIDENCE_REQUEST_OPEN','Open evidence requests must be closed before a decision.')
    tx=tx_get(s,disp.transaction_id,staff=True);check_version(request,tx)
    if u.id in [tx.supplier_id,tx.buyer_id]:fail(403,'MODERATOR_IS_PARTY','A transaction party cannot decide this dispute.')
    d=tx_data(tx);instructions=[];payout_total=0;refund_total=0;payout_lots=[];refund_lots=[];seen=set()
    for eid in body['evidence_ids_considered']:
        ev=s.get(Evidence,eid)
        if not ev or ev.transaction_id!=tx.id or ev.status!='VERIFIED':
            fail(422,'EVIDENCE_NOT_VERIFIED','Every considered evidence reference must be verified and belong to this transaction.',{'evidence_id':eid})
    for outcome in body['outcomes']:
        lot=next((x for x in d['lots'] if x['id']==outcome.get('lot_id')),None)
        if not lot or not lot.get('is_frozen'):fail(409,'LOT_FROZEN','Outcome must reference a frozen lot in this dispute.')
        if lot.get('freeze_dispute_id')!=did:fail(409,'LOT_FROZEN','Lot is not frozen by this dispute.')
        if lot['id'] in seen:fail(400,'VALIDATION_FAILED','A lot may appear only once in a moderator decision.')
        seen.add(lot['id']);kind=outcome.get('outcome')
        if kind not in ['RELEASE','REFUND','REDISPATCH_OR_REPLACEMENT','NO_FINANCIAL_ACTION']:
            fail(400,'VALIDATION_FAILED','Unsupported dispute outcome.')
        if int(outcome.get('quantity',lot['quantity']))!=lot['quantity']:
            fail(422,'QUANTITY_EXCEEDS_AVAILABLE','Decision outcomes must cover a whole lot; split the lot first.')
        if kind in ['RELEASE','REFUND']:
            amount=lot.get('allocation_kobo',lot['quantity']*next(i['unit_price_kobo'] for i in d['items'] if i['id']==lot['item_id']))
            if outcome.get('amount_kobo') not in [None,amount]:
                fail(422,'AMOUNT_MUST_MATCH_ALLOCATION','Amount must equal the lot allocation.',{'expected_amount_kobo':amount})
            if kind=='RELEASE':payout_total+=amount;payout_lots.append(lot)
            else:refund_total+=amount;refund_lots.append(lot)
            lot['money_status']='RELEASE_PENDING' if kind=='RELEASE' else 'REFUND_PENDING'
            # The dispute freeze remains until provider-confirmed settlement.
        elif kind=='NO_FINANCIAL_ACTION':
            if outcome.get('amount_kobo',0)!=0:fail(422,'AMOUNT_MUST_MATCH_ALLOCATION','NO_FINANCIAL_ACTION must have zero amount.')
            lot['item_status']=lot.get('pre_dispute_status','AWAITING_DISPATCH');lot['money_status']='HELD';lot['is_frozen']=False
            lot.pop('pre_dispute_status',None);lot.pop('freeze_dispute_id',None)
        elif kind=='REDISPATCH_OR_REPLACEMENT':
            if outcome.get('amount_kobo',0)!=0:fail(422,'AMOUNT_MUST_MATCH_ALLOCATION','REDISPATCH_OR_REPLACEMENT must have zero amount.')
            consents={x.get('actor_id') for x in d.get('dispute_evidence',[]) if x.get('kind')=='CONSENT'}
            if tx.buyer_id not in consents or tx.supplier_id not in consents:
                fail(422,'ACKNOWLEDGEMENT_REQUIRED','Both buyer and supplier must record consent for redispatch.')
            deadline=outcome.get('new_dispatch_deadline_at')
            if not deadline:fail(400,'VALIDATION_FAILED','new_dispatch_deadline_at is required for redispatch.')
            try:deadline_dt=datetime.fromisoformat(deadline.replace('Z','+00:00'))
            except Exception:fail(400,'VALIDATION_FAILED','new_dispatch_deadline_at must be ISO-8601.')
            if deadline_dt<=now():fail(400,'VALIDATION_FAILED','Redispatch deadline must be in the future.')
            lot['item_status']='AWAITING_DISPATCH';lot['money_status']='HELD';lot['is_frozen']=False
            lot.pop('pre_dispute_status',None);lot.pop('freeze_dispute_id',None);d['dispatch_deadline_at']=iso(deadline_dt)
    threshold=d.get('config_snapshot',{}).get('high_value_threshold_kobo',50000000);requires_second=False
    if payout_total:
        status='PENDING_APPROVAL' if payout_total>=threshold else 'QUEUED';requires_second|=status=='PENDING_APPROVAL'
        ins=Instruction(id=uid(),transaction_id=tx.id,instruction_type='PAYOUT',amount_kobo=payout_total,status=status,origin_id=did,deciding_moderator_id=u.id,idempotency_key='dispute:'+did+':PAYOUT');s.add(ins)
        if status=='QUEUED':post_journal(s,tx.id,'RELEASE_AUTHORIZED',ins.id,[('HELD_FUNDS','DEBIT',payout_total),('SUPPLIER_PAYABLE','CREDIT',payout_total)])
        instructions.append({'type':'PAYOUT','id':ins.id,'status':status,'amount_kobo':payout_total,'origin_type':'DISPUTE_DECISION'})
    if refund_total:
        status='PENDING_APPROVAL' if refund_total>=threshold else 'QUEUED';requires_second|=status=='PENDING_APPROVAL'
        ins=Instruction(id=uid(),transaction_id=tx.id,instruction_type='REFUND',amount_kobo=refund_total,status=status,origin_id=did,deciding_moderator_id=u.id,idempotency_key='dispute:'+did+':REFUND');s.add(ins)
        if status=='QUEUED':post_journal(s,tx.id,'REFUND_AUTHORIZED',ins.id,[('HELD_FUNDS','DEBIT',refund_total),('BUYER_REFUNDABLE','CREDIT',refund_total)])
        instructions.append({'type':'REFUND','id':ins.id,'status':status,'amount_kobo':refund_total,'origin_type':'DISPUTE_DECISION'})
    disp.status='AWAITING_SECOND_APPROVAL' if requires_second else 'DECIDED'
    disp.data={**disp.data,'status':disp.status,'decision':{'reason':body['reason'],'outcomes':body['outcomes'],'evidence_ids_considered':body['evidence_ids_considered'],'moderator_id':u.id,'created_at':iso()}}
    d['dispute']={**d.get('dispute',{}),'status':disp.status,'decision':disp.data.get('decision')}
    save_tx(s,tx,d);audit(s,u,'DISPUTE_DECIDED','DISPUTE',did,{'instructions':instructions});s.commit()
    return {'status':disp.status,'instructions':instructions}

@app.post('/api/v1/mod/payouts/{iid}/approve')
@app.post('/api/v1/mod/refunds/{iid}/approve')
def approve_instruction(iid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['SENIOR_MODERATOR']);ins=find_instruction(s,iid)
    if ins.status!='PENDING_APPROVAL':fail(409,'INSTRUCTION_NOT_PENDING_APPROVAL','Instruction is not awaiting approval.')
    if ins.deciding_moderator_id==u.id:fail(403,'SELF_APPROVAL_NOT_ALLOWED','A different senior moderator must approve.')
    ins.status='QUEUED';post_journal(s,ins.transaction_id,'RELEASE_AUTHORIZED' if ins.instruction_type=='PAYOUT' else 'REFUND_AUTHORIZED',ins.id,[('HELD_FUNDS','DEBIT',ins.amount_kobo),('SUPPLIER_PAYABLE' if ins.instruction_type=='PAYOUT' else 'BUYER_REFUNDABLE','CREDIT',ins.amount_kobo)])
    origin=s.get(Dispute,ins.origin_id)
    if origin and origin.status=='AWAITING_SECOND_APPROVAL':
        pending=s.scalar(select(Instruction).where(Instruction.origin_id==ins.origin_id,Instruction.status=='PENDING_APPROVAL'))
        if not pending:origin.status='DECIDED';origin.data={**origin.data,'status':'DECIDED'}
    audit(s,u,'FINANCIAL_INSTRUCTION_APPROVED',ins.instruction_type,iid);s.commit();return {'status':'QUEUED'}
@app.post('/api/v1/mod/payouts/{iid}/decline')
@app.post('/api/v1/mod/refunds/{iid}/decline')
def decline_instruction(iid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['SENIOR_MODERATOR']);ins=find_instruction(s,iid)
    if ins.status!='PENDING_APPROVAL':fail(409,'INSTRUCTION_NOT_PENDING_APPROVAL','Instruction is not awaiting approval.')
    if ins.deciding_moderator_id==u.id:fail(403,'SELF_APPROVAL_NOT_ALLOWED','A different senior moderator must approve.')
    ins.status='CANCELLED';audit(s,u,'FINANCIAL_INSTRUCTION_DECLINED',ins.instruction_type,iid,{'reason':body.get('reason')});s.commit();return {'status':'CANCELLED'}
@app.post('/api/v1/evidence/upload-urls')
def evidence_upload_urls(body:dict,request:Request,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,body.get('transaction_id',''),u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']); purpose=body.get('purpose','GENERAL'); uploads=[]
    limits={'image/jpeg':10*1024*1024,'image/png':10*1024*1024,'image/heic':10*1024*1024,'application/pdf':20*1024*1024,'video/mp4':50*1024*1024}
    for f in body.get('files',[]):
        mime=f.get('mime_type'); size=int(f.get('size_bytes',0))
        if mime not in limits:fail(415,'UNSUPPORTED_FILE_TYPE','File type is not supported.')
        if size<1 or size>limits[mime]:fail(413,'FILE_TOO_LARGE','File exceeds the configured limit.')
        if purpose in ['DELIVERY_PHOTO','RETURN_HANDOVER'] and not mime.startswith('image/'):fail(415,'UNSUPPORTED_FILE_TYPE','This evidence purpose requires a photograph.')
        eid=uid(); expires=int(time.time())+900; sig=hmac.new(SECRET,f'{eid}:{expires}'.encode(),hashlib.sha256).hexdigest(); token=f'{expires}.{sig}'; path=f"{eid}.blob"
        ev=Evidence(id=eid,transaction_id=tx.id,uploader_id=u.id,purpose=purpose,filename=f.get('filename','upload'),mime_type=mime,size_bytes=size,sha256=f.get('sha256',''),object_path=path)
        s.add(ev);uploads.append({'evidence_id':eid,'upload_url':str(request.base_url).rstrip('/')+f'/api/v1/evidence/upload/{eid}?token={token}','headers':{'Content-Type':mime},'expires_in':900})
    s.commit();return {'uploads':uploads}
@app.put('/api/v1/evidence/upload/{eid}')
async def evidence_put(eid:str,request:Request,token:str,s:Session=Depends(db)):
    ev=s.get(Evidence,eid)
    try: expiry,signature=token.split('.',1);expected=hmac.new(SECRET,f'{eid}:{expiry}'.encode(),hashlib.sha256).hexdigest()
    except Exception:expiry='0';signature='' ;expected='invalid'
    if not ev or int(expiry)<int(time.time()) or not hmac.compare_digest(signature,expected):fail(404,'NOT_FOUND','Upload URL is invalid or expired.')
    raw=await request.body()
    if len(raw)!=ev.size_bytes:fail(422,'FILE_SIZE_MISMATCH','Uploaded file size does not match declared size.')
    signatures={'image/jpeg':raw.startswith(b'\xff\xd8\xff'),'image/png':raw.startswith(b'\x89PNG\r\n\x1a\n'),'application/pdf':raw.startswith(b'%PDF-'),'video/mp4':len(raw)>12 and raw[4:8]==b'ftyp','image/heic':len(raw)>12 and raw[4:8]==b'ftyp' and b'hei' in raw[8:16]}
    if not signatures.get(ev.mime_type,False):fail(415,'UNSUPPORTED_FILE_TYPE','File content does not match its declared media type.')
    root=os.getenv('EVIDENCE_STORAGE_DIR','./private-evidence');os.makedirs(root,mode=0o700,exist_ok=True)
    with open(os.path.join(root,ev.object_path),'wb') as f:f.write(raw)
    ev.status='UPLOADED';s.commit();return Response(status_code=204)
@app.post('/api/v1/evidence/{eid}/complete')
def evidence_complete(eid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    ev=s.get(Evidence,eid)
    if not ev or ev.uploader_id!=u.id:fail(404,'NOT_FOUND','Evidence not found.')
    root=os.getenv('EVIDENCE_STORAGE_DIR','./private-evidence');path=os.path.join(root,ev.object_path)
    if not os.path.isfile(path):fail(422,'EVIDENCE_NOT_UPLOADED','File upload must complete before verification.')
    digest=hashlib.sha256(open(path,'rb').read()).hexdigest()
    if digest!=ev.sha256:fail(422,'HASH_MISMATCH','Uploaded file hash does not match the declared SHA-256.')
    ev.status='VERIFIED';s.commit();return {'evidence_id':eid,'status':'VERIFIED','sha256':digest,'server_received_at':iso()}
@app.get('/api/v1/evidence/{eid}')
def evidence_detail(eid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    ev=s.get(Evidence,eid)
    if not ev:fail(404,'NOT_FOUND','Evidence not found.')
    tx=tx_get(s,ev.transaction_id,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR'])
    return {'id':ev.id,'transaction_id':ev.transaction_id,'purpose':ev.purpose,'filename':ev.filename,'mime_type':ev.mime_type,'size_bytes':ev.size_bytes,'sha256':ev.sha256,'status':ev.status,'uploaded_at':iso(ev.created_at)}
@app.post('/api/v1/mod/evidence/{eid}/flag')
def flag_evidence(eid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['MODERATOR','SENIOR_MODERATOR']);ev=s.get(Evidence,eid)
    if not ev:fail(404,'NOT_FOUND','Evidence not found.')
    audit(s,u,'EVIDENCE_FLAGGED','EVIDENCE',eid,{'reason':body.get('reason')});s.commit();return {'status':'FLAGGED'}
@app.get('/api/v1/transactions/{tid}/payouts')
def tx_payouts(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']);return {'data':[instruction_json(x) for x in s.scalars(select(Instruction).where(Instruction.transaction_id==tid,Instruction.instruction_type=='PAYOUT')).all()]}
@app.get('/api/v1/transactions/{tid}/refunds')
def tx_refunds(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']);return {'data':[instruction_json(x) for x in s.scalars(select(Instruction).where(Instruction.transaction_id==tid,Instruction.instruction_type=='REFUND')).all()]}
def instruction_json(x):return {'id':x.id,'type':x.instruction_type,'amount_kobo':x.amount_kobo,'status':x.status,'origin_id':x.origin_id,'created_at':iso(x.created_at)}
@app.get('/api/v1/mod/financial-exceptions')
def financial_exceptions(s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['SENIOR_MODERATOR']);return {'data':[instruction_json(x) for x in s.scalars(select(Instruction).where(Instruction.status.in_(['FAILED','UNKNOWN']),Instruction.resolved==False)).all()],'meta':{'server_time':iso(),'next_cursor':None}}
@app.post('/api/v1/mod/financial-exceptions/{iid}/resolve')
def resolve_exception(iid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['SENIOR_MODERATOR']);ins=find_instruction(s,iid)
    if ins.status not in ['FAILED','UNKNOWN'] or ins.resolved:fail(409,'INVALID_STATE_TRANSITION','Only unresolved failed or unknown instructions can be resolved.')
    if ins.status=='UNKNOWN':fail(409,'OUTCOME_UNKNOWN_RECONCILE_FIRST','Unknown provider outcomes remain frozen until an external provider status query and reconciliation record are available.')
    if body.get('action')!='RELEASE' or not body.get('reason'):fail(400,'VALIDATION_FAILED','A reasoned RELEASE is required for a definitive failure.')
    tx=s.get(Aggregate,ins.transaction_id);d=tx_data(tx);payable='SUPPLIER_PAYABLE' if ins.instruction_type=='PAYOUT' else 'BUYER_REFUNDABLE'
    post_journal(s,tx.id,'REVERSAL',ins.id,[(payable,'DEBIT',ins.amount_kobo),('HELD_FUNDS','CREDIT',ins.amount_kobo)])
    for lot in d.get('lots',[]):
        if lot.get('money_status') in ['RELEASE_PENDING','REFUND_PENDING']:
            lot['money_status']='HELD';lot['pre_dispute_status']=lot['item_status'];lot['is_frozen']=True;lot['item_status']='IN_DISPUTE'
    if not d.get('dispute'):
        disp={'id':uid(),'reason_code':'OTHER','status':'EVIDENCE_WINDOW_OPEN','created_at':iso(),'window_ends_at':iso(now()+timedelta(hours=48)),'system_opened':True,'reason':'Financial instruction failed; senior moderator released the reservation.'};d['dispute']=disp
        for lot in d.get('lots',[]):
            if lot.get('is_frozen') and lot.get('item_status')=='IN_DISPUTE':lot['freeze_dispute_id']=disp['id']
        s.add(Dispute(id=disp['id'],transaction_id=tx.id,reason_code='OTHER',data=disp))
    save_tx(s,tx,d);ins.resolved=True;audit(s,u,'FINANCIAL_EXCEPTION_RESOLVED','INSTRUCTION',iid,{'action':'RELEASE','reason':body.get('reason')});s.commit();return {'status':'FAILED','resolved':True}
@app.get('/api/v1/notifications')
def notifications(s:Session=Depends(db),u:User=Depends(current_user)):
    return {'data':[{'id':n.id,'type':n.type,'entity_id':n.entity_id,'payload':n.payload,'status':n.status,'created_at':iso(n.created_at)} for n in s.scalars(select(Notification).where(Notification.user_id==u.id).order_by(Notification.created_at.desc())).all()]}
@app.post('/api/v1/notifications/{nid}/read')
def notification_read(nid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    n=s.get(Notification,nid)
    if not n or n.user_id!=u.id:fail(404,'NOT_FOUND','Notification not found.')
    n.status='READ';s.commit();return {'status':'READ'}
@app.get('/api/v1/admin/audit')
def admin_audit(limit:int=50,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['ADMIN']);return {'data':[{'id':x.id,'actor_id':x.actor_id,'action':x.action,'entity_type':x.entity_type,'entity_id':x.entity_id,'details':x.details,'created_at':iso(x.created_at)} for x in s.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(min(limit,200))).all()],'meta':{'server_time':iso(),'next_cursor':None}}
@app.post('/api/v1/admin/reconciliation/run')
def reconciliation_run(s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['ADMIN'])
    if os.getenv('PAYMENT_PROVIDER','simulated').lower()=='paga':
        results={
            'account_creation_recovery':resolve_paga_account_creations(s),
            'funding_events':process_paga_events(s),
            'subsidiary_operations':process_paga_subsidiary_operation_events(s),
            'payout_events':process_paga_payout_events(s),
            'instruction_status':resolve_paga_instruction_statuses(s),
        }
        try:results['provider_statements']=run_paga_statement_reconciliation(s,PagaClient())
        except PagaError as exc:results['provider_statements']={'errors':[{'reason':str(exc)}]}
        statement=results.get('provider_statements',{})
        pending=sum(int(v.get('pending',0))+int(v.get('unknown',0)) for v in results.values() if isinstance(v,dict))
        pending+=len(statement.get('balance_mismatches',[]))+len(statement.get('missing_settled_transfers',[]))+len(statement.get('provider_settled_local_unresolved',[]))+len(statement.get('errors',[]))
        status='REVIEW_REQUIRED' if pending else 'CHECKS_COMPLETED'
        audit(s,u,'PAGA_RECONCILIATION_SWEEP_COMPLETED','SYSTEM','system',{'status':status,'results':results,'provider_statement_reconciliation':'DAILY_WINDOW_CHECKED'});s.commit()
        return {'status':status,'mode':PagaConfig.from_env().provider_mode,'results':results,'provider_statement_reconciliation':'DAILY_WINDOW_CHECKED'}
    audit(s,u,'SIMULATED_RECONCILIATION_REQUESTED','SYSTEM','system');s.commit();return {'status':'NOT_APPLICABLE','mode':'SIMULATED','message':'The simulator has no external provider statement to reconcile.'}
@app.get('/api/v1/admin/config/{key}')
def config_get(key:str,u:User=Depends(current_user)):
    require_role(u,['ADMIN']);return {'key':key,'value':{'high_value_threshold_kobo':50000000,'dispatch_deadline_hours':72,'evidence_window_hours':48}.get(key)}
@app.put('/api/v1/admin/config/{key}')
def config_put(key:str,body:dict,u:User=Depends(current_user)):
    require_role(u,['ADMIN']);fail(501,'DUAL_ADMIN_APPROVAL_REQUIRED','Configuration changes require a two-admin approval workflow, not enabled in this build.')
@app.get('/api/v1/admin/staff')
def staff_list(s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['ADMIN']);return {'data':[user_json(x) for x in s.scalars(select(User).where(User.role.in_(['MODERATOR','SENIOR_MODERATOR','ADMIN']))).all()]}
@app.post('/api/v1/admin/staff')
def staff_create(body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['ADMIN']);fail(501,'STAFF_PROVISIONING_NOT_CONFIGURED','Staff provisioning requires an approved TOTP enrolment workflow.')
@app.patch('/api/v1/admin/staff/{sid}')
def staff_update(sid:str,body:dict,s:Session=Depends(db),u:User=Depends(current_user)):
    require_role(u,['ADMIN']);staff=s.get(User,sid)
    if not staff:fail(404,'NOT_FOUND','Staff account not found.')
    if body.get('role') not in ['MODERATOR','SENIOR_MODERATOR','ADMIN']:fail(400,'VALIDATION_FAILED','Invalid staff role.')
    staff.role=body['role'];s.commit();return {'user':user_json(staff)}
def run_deadlines(s:Session):
    # Safe, idempotent deadline scan. Expiry opens review/dispute; never moves money.
    opened=0
    for tx in s.scalars(select(Aggregate).where(Aggregate.status=='FUNDED')).all():
        d=tx_data(tx); deadline=d.get('dispatch_deadline_at')
        if deadline and datetime.fromisoformat(deadline.replace('Z','+00:00'))<now() and any(l.get('item_status')=='AWAITING_DISPATCH' for l in d.get('lots',[])) and not d.get('dispute'):
            disp={'id':uid(),'reason_code':'NO_DISPATCH','status':'EVIDENCE_WINDOW_OPEN','created_at':iso(),'window_ends_at':iso(now()+timedelta(hours=48)),'system_opened':True};d['dispute']=disp
            for lot in d['lots']:
                if lot['item_status']=='AWAITING_DISPATCH':lot['pre_dispute_status']=lot['item_status'];lot['item_status']='IN_DISPUTE';lot['is_frozen']=True;lot['freeze_dispute_id']=disp['id']
            s.add(Dispute(id=disp['id'],transaction_id=tx.id,reason_code='NO_DISPATCH',data=disp));save_tx(s,tx,d);opened+=1
        for req in d.get('delivery_photo_requirements',[]):
            if req['status']=='PENDING' and datetime.fromisoformat(req['due_at'].replace('Z','+00:00'))<now():req['status']='OVERDUE';save_tx(s,tx,d)
        for dec in d.get('decisions',[]):
            w=dec.get('evidence_window',{})
            if w.get('status')=='OPEN' and w.get('ends_at') and datetime.fromisoformat(w['ends_at'].replace('Z','+00:00'))<now():w['status']='CLOSED_ELAPSED';save_tx(s,tx,d)
        if d.get('dispute') and d['dispute'].get('status')=='EVIDENCE_WINDOW_OPEN' and d['dispute'].get('window_ends_at') and datetime.fromisoformat(d['dispute']['window_ends_at'].replace('Z','+00:00'))<now():
            d['dispute']['status']='EVIDENCE_WINDOW_CLOSED_ELAPSED';d['dispute']['window_closed']=True;disp_row=s.get(Dispute,d['dispute']['id'])
            if disp_row:disp_row.status='EVIDENCE_WINDOW_CLOSED_ELAPSED';disp_row.data={**disp_row.data,'status':'EVIDENCE_WINDOW_CLOSED_ELAPSED','window_closed':True}
            save_tx(s,tx,d)
        for ret in d.get('returns',[]):
            deadline=ret.get('confirmation_deadline_at')
            if ret.get('status')=='HANDOVER_RECORDED' and deadline and datetime.fromisoformat(deadline.replace('Z','+00:00'))<now() and not d.get('dispute'):
                disp={'id':uid(),'reason_code':'RETURN_CONFIRMATION_OVERDUE','status':'EVIDENCE_WINDOW_OPEN','created_at':iso(),'window_ends_at':iso(now()+timedelta(hours=48)),'system_opened':True};d['dispute']=disp;ret['status']='DISPUTED'
                for line in ret.get('lines',[]):
                    lot=next((x for x in d['lots'] if x['id']==line['lot_id']),None)
                    if lot:lot['pre_dispute_status']=lot['item_status'];lot['item_status']='IN_DISPUTE';lot['is_frozen']=True;lot['freeze_dispute_id']=disp['id']
                s.add(Dispute(id=disp['id'],transaction_id=tx.id,reason_code=disp['reason_code'],data=disp));opened+=1;save_tx(s,tx,d)
        for lot in d.get('lots',[]):
            deadline=lot.get('inspection_deadline_at')
            if lot.get('item_status')=='RECEIPT_CONFIRMED' and deadline and datetime.fromisoformat(deadline.replace('Z','+00:00'))<now() and not d.get('dispute'):
                disp={'id':uid(),'reason_code':'INSPECTION_PERIOD_EXPIRED','status':'EVIDENCE_WINDOW_OPEN','created_at':iso(),'window_ends_at':iso(now()+timedelta(hours=48)),'system_opened':True};d['dispute']=disp;lot['pre_dispute_status']=lot['item_status'];lot['item_status']='IN_DISPUTE';lot['is_frozen']=True;lot['freeze_dispute_id']=disp['id'];s.add(Dispute(id=disp['id'],transaction_id=tx.id,reason_code=disp['reason_code'],data=disp));opened+=1;save_tx(s,tx,d);break
    s.commit();return {'disputes_opened':opened,'meta':{'server_time':iso()}}
@app.get('/openapi.json')
def openapi_alias():return app.openapi()
def mark_paga_event(s,event,status,error):
    row=s.scalar(select(ProviderEventProcessing).where(ProviderEventProcessing.provider_event_id==event.id))
    if not row:
        row=ProviderEventProcessing(id=uid(),provider_event_id=event.id,status=status,processing_error=error);s.add(row)
    else:
        row.status=status;row.processing_error=error;row.updated_at=now()

def resolve_paga_account_creations(s:Session):
    if os.getenv('PAGA_MODE','disabled').lower()=='disabled':return {'resolved':0,'pending':0}
    rows=s.scalars(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.status.in_(['CREATING','UNKNOWN'])).limit(25)).all()
    if not rows:return {'resolved':0,'pending':0}
    try:client=PagaClient()
    except PagaError as exc:return {'resolved':0,'pending':len(rows),'configuration_error':str(exc)}
    resolved=0;pending=0
    for row in rows:
        tx=s.get(Aggregate,row.transaction_id);buyer=s.get(User,tx.buyer_id) if tx and tx.buyer_id else None
        if not tx or not buyer:
            row.status='FAILED';pending+=1;continue
        try:
            lookup=client.get_subsidiary_account(row.account_reference,'SKLOOK'+secrets.token_hex(10).upper())
            if str(lookup.get('statusCode'))=='0' and lookup.get('accountNumber'):
                row.account_number=str(lookup['accountNumber']);row.status='ACTIVE'
                payment=s.get(Payment,row.payment_id)
                if payment and payment.status=='UNKNOWN':
                    payment.status='PENDING';d=tx_data(tx);d['payments']=[{**x,'status':'PENDING','checkout':{**x.get('checkout',{}),'account_number':row.account_number,'instructions':'Transfer the exact amount to this Paga account. Funds are not secured until the signed Paga callback and account balance are verified.'}} if x.get('id')==payment.id else x for x in d.get('payments',[])];save_tx(s,tx,d)
                resolved+=1;continue
        except PagaError:
            pass
        try:
            name=(buyer.display_name or 'SupplyKey Buyer').strip();parts=name.split()
            created=client.create_transaction_account(reference=row.provider_reference,account_reference=row.account_reference,account_name=name,first_name=parts[0][:50],last_name=(' '.join(parts[1:]) or parts[0])[:50],phone=buyer.phone,email=buyer.email,amount_kobo=s.get(Payment,row.payment_id).amount_kobo)
            row.account_number=str(created['accountNumber']);row.status='ACTIVE'
            payment=s.get(Payment,row.payment_id)
            if payment and payment.status=='UNKNOWN':
                payment.status='PENDING';d=tx_data(tx);d['payments']=[{**x,'status':'PENDING','checkout':{**x.get('checkout',{}),'account_number':row.account_number,'instructions':'Transfer the exact amount to this Paga account. Funds are not secured until the signed Paga callback and account balance are verified.'}} if x.get('id')==payment.id else x for x in d.get('payments',[])];save_tx(s,tx,d)
            resolved+=1
        except PagaUnknownOutcome:
            # The original references are deliberately reused. Never mint a new account
            # reference after an ambiguous create operation.
            pending+=1
        except PagaError as exc:
            row.status='FAILED';payment=s.get(Payment,row.payment_id)
            if payment:payment.status='FAILED'
            d=tx_data(tx);d['payments']=[{**x,'status':'FAILED'} if payment and x.get('id')==payment.id else x for x in d.get('payments',[])];save_tx(s,tx,d)
            audit(s,None,'PAGA_ACCOUNT_CREATION_FAILED','PAYMENT',row.payment_id,{'reason':str(exc)});pending+=1
    s.commit();return {'resolved':resolved,'pending':pending}

def process_paga_events(s:Session):
    """Reconcile durably acknowledged Paga subsidiary-account callbacks."""
    if os.getenv('PAGA_MODE','disabled').lower()=='disabled':return {'processed':0,'pending':0}
    events=s.scalars(select(ProviderEvent).where(ProviderEvent.provider_name=='PAGA',ProviderEvent.signature_valid==True,~select(ProviderEventProcessing.id).where(ProviderEventProcessing.provider_event_id==ProviderEvent.id,ProviderEventProcessing.status=='PROCESSED').exists()).order_by(ProviderEvent.received_at).limit(50)).all()
    processed=0;pending=0
    try:client=PagaClient()
    except PagaError as exc:return {'processed':0,'pending':len(events),'configuration_error':str(exc)}
    for event in events:
        body=event.raw_body or {}
        account=s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.account_number==str(body.get('accountNumber',''))))
        if not account:
            mark_paga_event(s,event,'RETRY','No SupplyKey transaction account matches the Paga account number yet. Account creation reconciliation will run before the next attempt.');pending+=1;continue
        payment=s.get(Payment,account.payment_id);tx=s.get(Aggregate,account.transaction_id)
        if not payment or not tx:
            mark_paga_event(s,event,'PROCESSED','Payment or transaction record is missing.');processed+=1;continue
        if str(body.get('statusCode'))!='0':
            payment.status='UNKNOWN';mark_paga_event(s,event,'PROCESSED','Paga callback did not confirm a successful funding event.');processed+=1;continue
        try:received_kobo=int((Decimal(str(body.get('amount','0')))*100).quantize(Decimal('1')))
        except (InvalidOperation,ValueError):
            payment.status='UNKNOWN';mark_paga_event(s,event,'PROCESSED','Paga callback amount is invalid.');processed+=1;continue
        if received_kobo<=0 or received_kobo>payment.amount_kobo:
            payment.status='UNKNOWN';reason=f'Invalid funding amount: expected at most {payment.amount_kobo} kobo, received {received_kobo} kobo.'
            audit(s,None,'PAGA_AMOUNT_MISMATCH','PAYMENT',payment.id,{'expected_kobo':payment.amount_kobo,'received_kobo':received_kobo});mark_paga_event(s,event,'PROCESSED',reason);processed+=1;continue
        payment.status='PAYMENT_RECEIVED'
        try:
            balance_ref='SKBAL'+secrets.token_hex(12).upper()
            balance=client.get_subsidiary_balance(account.account_reference,balance_ref)
            if str(balance.get('statusCode'))!='0':raise PagaUnknownOutcome('Paga balance query did not return a confirmed result.')
            balance_kobo=int((Decimal(str(balance.get('balance','0')))*100).quantize(Decimal('1')))
            if balance_kobo>payment.amount_kobo:
                payment.status='UNKNOWN';audit(s,None,'PAGA_OVERFUNDING_REQUIRES_REVIEW','PAYMENT',payment.id,{'expected_kobo':payment.amount_kobo,'balance_kobo':balance_kobo});mark_paga_event(s,event,'PROCESSED','Paga account balance exceeds the transaction amount. Manual reconciliation is required.');processed+=1;continue
            if balance_kobo<payment.amount_kobo:
                d=tx_data(tx);d['payments']=[{**x,'status':'PAYMENT_RECEIVED','checkout':{**x.get('checkout',{}),'amount_received_kobo':balance_kobo,'remaining_kobo':payment.amount_kobo-balance_kobo,'instructions':'A partial transfer has been received. Transfer the remaining amount to the same Paga account; funding is not secured until the exact total is confirmed.'}} if x.get('id')==payment.id else x for x in d.get('payments',[])];save_tx(s,tx,d)
                mark_paga_event(s,event,'PROCESSED',None);processed+=1;continue
            secure_paga_payment(s,payment,tx,account,balance_kobo)
            mark_paga_event(s,event,'PROCESSED',None);processed+=1
        except (PagaError,InvalidOperation,ValueError) as exc:
            mark_paga_event(s,event,'RETRY',str(exc));pending+=1
    s.commit();return {'processed':processed,'pending':pending}

def freeze_instruction_unknown(s,ins,tx,d,reason):
    ins.status='UNKNOWN';ins.provider_stage='UNKNOWN';ins.provider_response={**(ins.provider_response or {}),'last_error':reason}
    for lot in d.get('lots',[]):
        if lot.get('money_status') in ['RELEASE_PENDING','REFUND_PENDING']:
            lot['is_frozen']=True;lot['freeze_reason']='PROVIDER_UNKNOWN'
    save_tx(s,tx,d);audit(s,None,'PAGA_INSTRUCTION_UNKNOWN','INSTRUCTION',ins.id,{'reason':reason})

def finalize_instruction_success(s,ins,tx,d,provider_mode):
    if ins.status=='SUCCEEDED':return
    ins.status='SUCCEEDED';ins.provider_stage='SETTLED';ins.provider_response={**(ins.provider_response or {}),'settled_at':iso(),'settlement_source':provider_mode}
    payable='SUPPLIER_PAYABLE' if ins.instruction_type=='PAYOUT' else 'BUYER_REFUNDABLE'
    post_journal(s,tx.id,'PAYOUT_SENT' if ins.instruction_type=='PAYOUT' else 'REFUND_SENT',ins.id,[(payable,'DEBIT',ins.amount_kobo),('PROVIDER_CLEARING','CREDIT',ins.amount_kobo)])
    origin=ins.origin_id
    if ins.instruction_type=='PAYOUT':
        released_ids=[]
        for dec in d.get('decisions',[]):
            if dec['id']==origin:released_ids.append(dec['lot_id'])
        if d.get('dispute',{}).get('id')==origin:
            released_ids.extend(o.get('lot_id') for o in d['dispute'].get('decision',{}).get('outcomes',[]) if o.get('outcome')=='RELEASE')
        for lot in d['lots']:
            if lot['id'] in released_ids and lot.get('money_status')=='RELEASE_PENDING':lot['money_status']='RELEASED';lot['item_status']='SETTLED_RELEASED';lot['is_frozen']=False;lot.pop('freeze_reason',None);lot.pop('freeze_dispute_id',None)
    else:
        refunded_ids=[]
        for ret in d.get('returns',[]):
            if ret['id']==origin:refunded_ids.extend(line['lot_id'] for line in ret['lines'])
        if d.get('dispute',{}).get('id')==origin:
            refunded_ids.extend(o.get('lot_id') for o in d['dispute'].get('decision',{}).get('outcomes',[]) if o.get('outcome')=='REFUND')
        for lot in d['lots']:
            if lot['id'] in refunded_ids and lot.get('money_status')=='REFUND_PENDING' and lot.get('item_status') in ['IN_DISPUTE','RETURN_RECEIPT_CONFIRMED']:
                lot['money_status']='REFUNDED';lot['item_status']='SETTLED_REFUNDED';lot['is_frozen']=False;lot.pop('freeze_reason',None);lot.pop('freeze_dispute_id',None)
    if d.get('lots') and all(x.get('item_status') in ['SETTLED_RELEASED','SETTLED_REFUNDED'] for x in d['lots']):tx.status='COMPLETED'
    save_tx(s,tx,d);audit(s,None,'FINANCIAL_INSTRUCTION_SETTLED','INSTRUCTION',ins.id,{'provider_mode':provider_mode,'provider_reference':ins.provider_reference})

def restore_paga_subsidiary_funds(s,client,ins,tx,d,account,reason):
    """Return a failed bank-transfer amount from the Paga business balance to its source subaccount.

    The instruction remains UNKNOWN until the top-up and exact restored balance are confirmed.
    """
    before=int((ins.provider_response or {}).get('balance_before_kobo',0))
    if before<=0:
        freeze_instruction_unknown(s,ins,tx,d,'Cannot restore funds: pre-charge subsidiary balance was not recorded. '+reason);return False
    restore_ref='SKBACK'+ins.id.replace('-','')
    ins.status='UNKNOWN';ins.provider_stage='RESTORE_SUBMITTING';ins.provider_reference=restore_ref
    ins.provider_response={**(ins.provider_response or {}),'restore_reason':reason,'restore_reference':restore_ref}
    save_tx(s,tx,d);s.commit()
    try:
        result=client.top_up_subsidiary_account(account.account_reference,restore_ref,ins.amount_kobo,f'SupplyKey restore {ins.id[:8]}')
        if str(result.get('statusCode'))!='0':raise PagaUnknownOutcome('Paga did not confirm the restoration top-up.')
    except PagaUnknownOutcome as exc:
        ins.status='UNKNOWN';ins.provider_stage='RESTORE_UNKNOWN';ins.provider_response={**(ins.provider_response or {}),'restore_error':str(exc)};s.commit();return False
    except PagaError as exc:
        ins.status='UNKNOWN';ins.provider_stage='RESTORE_UNKNOWN';ins.provider_response={**(ins.provider_response or {}),'restore_error':str(exc)};s.commit();return False
    try:
        balance=client.get_subsidiary_balance(account.account_reference,'SKRBL'+ins.id.replace('-',''))
        restored=int((Decimal(str(balance.get('balance','0')))*100).quantize(Decimal('1')))
        if str(balance.get('statusCode'))=='0' and restored==before:
            ins.status='FAILED';ins.provider_stage='FUNDS_RESTORED';ins.provider_response={**(ins.provider_response or {}),'restore_response':result,'restored_balance_kobo':restored}
            audit(s,None,'PAGA_FUNDS_RESTORED_AFTER_FAILED_TRANSFER','INSTRUCTION',ins.id,{'reason':reason,'restored_balance_kobo':restored});s.commit();return True
    except (PagaError,InvalidOperation,ValueError):
        pass
    ins.status='UNKNOWN';ins.provider_stage='RESTORE_UNKNOWN';ins.provider_response={**(ins.provider_response or {}),'restore_response':result,'restore_error':'Top-up response received but exact subsidiary balance restoration was not confirmed.'};s.commit();return False

def run_paga_financial(s:Session):
    """Submit queued instructions through Paga sandbox/live adapter with staged references."""
    try:client=PagaClient()
    except PagaError as exc:return {'processed':[],'pending':0,'configuration_error':str(exc),'mode':'PAGA'}
    if not client.config.payout_callback_url:
        return {'processed':[],'pending':s.query(Instruction).filter(Instruction.status=='QUEUED').count(),'configuration_error':'PAGA_PAYOUT_CALLBACK_URL is required before bank payouts can be submitted.','mode':client.config.provider_mode}
    processed=[];pending=0
    for ins in s.scalars(select(Instruction).where(Instruction.status=='QUEUED').order_by(Instruction.created_at).limit(50)).all():
        tx=s.get(Aggregate,ins.transaction_id);d=tx_data(tx)
        active=s.scalar(select(Instruction).where(Instruction.transaction_id==ins.transaction_id,Instruction.id!=ins.id,Instruction.status.in_(['SUBMITTED','UNKNOWN'])).limit(1))
        if active:pending+=1;continue
        account=s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.transaction_id==tx.id))
        target_id=tx.supplier_id if ins.instruction_type=='PAYOUT' else tx.buyer_id
        target=s.get(User,target_id) if target_id else None
        payout=target.payout_account if target else None
        if not account or not account.account_reference or not payout or not payout.get('verified') or not payout.get('account_number_enc'):
            pending+=1;continue
        try:account_number=decrypt_payout_account(payout)
        except ValueError as exc:
            ins.status='FAILED';ins.provider_response={'error':str(exc)};audit(s,None,'PAGA_DESTINATION_INVALID','INSTRUCTION',ins.id,{'reason':str(exc)});processed.append({'id':ins.id,'status':'FAILED'});continue
        charge_ref='SKCHG'+ins.id.replace('-','')
        transfer_ref='SKOUT'+ins.id.replace('-','')
        if ins.provider_stage in [None,'','CHARGE_SUBMITTING','CHARGE_UNKNOWN']:
            if ins.provider_stage in [None,'']:
                try:
                    before=client.get_subsidiary_balance(account.account_reference,'SKPRE'+ins.id.replace('-',''))
                    if str(before.get('statusCode'))!='0':raise PagaUnknownOutcome('Could not establish the pre-charge subsidiary balance.')
                    before_kobo=int((Decimal(str(before.get('balance','0')))*100).quantize(Decimal('1')))
                    if before_kobo<ins.amount_kobo:raise PagaError('Transaction subsidiary account has insufficient available balance for this instruction.')
                    ins.provider_response={'balance_before_kobo':before_kobo};ins.provider_reference=charge_ref;ins.provider_stage='CHARGE_SUBMITTING';s.commit()
                except PagaUnknownOutcome as exc:
                    freeze_instruction_unknown(s,ins,tx,d,str(exc));ins.provider_reference=charge_ref;ins.provider_stage='CHARGE_UNKNOWN';processed.append({'id':ins.id,'status':'UNKNOWN'});s.commit();continue
                except PagaError as exc:
                    ins.status='FAILED';ins.provider_stage='CHARGE_FAILED';ins.provider_response={'error':str(exc)};audit(s,None,'PAGA_CHARGE_FAILED','INSTRUCTION',ins.id,{'reason':str(exc)});processed.append({'id':ins.id,'status':'FAILED'});s.commit();continue
            try:
                charged=client.charge_subsidiary_account(account.account_reference,charge_ref,ins.amount_kobo,f'SupplyKey {ins.instruction_type.lower()} {ins.id[:8]}')
                before=int((ins.provider_response or {}).get('balance_before_kobo',0))
                try:new_balance=int((Decimal(str(charged.get('newBalance')))*100).quantize(Decimal('1')))
                except (InvalidOperation,TypeError,ValueError):new_balance=-1
                if str(charged.get('statusCode'))!='0' or before-new_balance!=ins.amount_kobo:
                    ins.status='UNKNOWN';ins.provider_stage='CHARGE_UNKNOWN';ins.provider_reference=charge_ref;ins.provider_response={**(ins.provider_response or {}),'charge':charged,'charge_error':'Paga charge response did not prove the exact authorised debit.'};freeze_instruction_unknown(s,ins,tx,d,'Paga charge amount or balance delta did not match the instruction.');s.commit();processed.append({'id':ins.id,'status':'UNKNOWN'});continue
                ins.provider_stage='SUBSIDIARY_CHARGED';ins.provider_reference=transfer_ref;ins.provider_response={**(ins.provider_response or {}),'charge':charged}
                s.commit()
            except PagaUnknownOutcome as exc:
                freeze_instruction_unknown(s,ins,tx,d,str(exc));ins.provider_reference=charge_ref;ins.provider_stage='CHARGE_UNKNOWN';processed.append({'id':ins.id,'status':'UNKNOWN'});s.commit();continue
            except PagaError as exc:
                ins.status='FAILED';ins.provider_stage='CHARGE_FAILED';ins.provider_response={**(ins.provider_response or {}),'error':str(exc)};audit(s,None,'PAGA_CHARGE_FAILED','INSTRUCTION',ins.id,{'reason':str(exc)});processed.append({'id':ins.id,'status':'FAILED'});s.commit();continue
        if ins.provider_stage=='SUBSIDIARY_CHARGED':
            try:
                validation=client.validate_bank_account('SKVAL'+ins.id.replace('-',''),ins.amount_kobo,payout['bank_uuid'],account_number)
                if str(validation.get('responseCode'))!='0':
                    ins.provider_response={**(ins.provider_response or {}),'destination_validation':validation}
                    restore_paga_subsidiary_funds(s,client,ins,tx,d,account,f'Paga destination validation failed with responseCode {validation.get("responseCode")}.')
                    audit(s,None,'PAGA_DESTINATION_VALIDATION_FAILED','INSTRUCTION',ins.id,{'response_code':validation.get('responseCode')});processed.append({'id':ins.id,'status':ins.status});s.commit();continue
                fee_kobo=int(((Decimal(str(validation.get('fee') or 0))+Decimal(str(validation.get('vat') or 0)))*100).quantize(Decimal('1')))
                balance=client.get_business_balance('SKBAL'+ins.id.replace('-',''))
                available_kobo=int((Decimal(str(balance.get('availableBalance','0')))*100).quantize(Decimal('1')))
                if str(balance.get('responseCode'))!='0' or available_kobo < ins.amount_kobo+fee_kobo:
                    ins.provider_response={**(ins.provider_response or {}),'available_balance_kobo':available_kobo,'required_balance_kobo':ins.amount_kobo+fee_kobo,'destination_validation':validation};pending+=1;s.commit();continue
                result=client.deposit_to_bank(transfer_ref,ins.amount_kobo,payout['bank_uuid'],account_number,client.config.payout_callback_url,f'SupplyKey {ins.instruction_type.lower()} {ins.id[:8]}')
                code=str(result.get('responseCode'))
                ins.provider_response={**(ins.provider_response or {}),'transfer':result}
                if code=='0':
                    ins.status='SUBMITTED';ins.provider_stage='TRANSFER_SUBMITTED'
                elif code=='3':
                    ins.status='SUBMITTED';ins.provider_stage='TRANSFER_PENDING'
                elif code in ['-2','-9','-8','-4','116','117','132','136','137','138','139','4','101','103','107','114','115','133','104','106','108','110','111','113','118','119','129','131']:
                    restore_paga_subsidiary_funds(s,client,ins,tx,d,account,f'Paga bank transfer definitively rejected with responseCode {code}.')
                else:
                    ins.status='UNKNOWN';ins.provider_stage='TRANSFER_UNKNOWN'
                    for lot in d.get('lots',[]):
                        if lot.get('money_status') in ['RELEASE_PENDING','REFUND_PENDING']:lot['is_frozen']=True;lot['freeze_reason']='PROVIDER_UNKNOWN'
                    save_tx(s,tx,d)
                audit(s,None,'PAGA_BANK_TRANSFER_SUBMITTED','INSTRUCTION',ins.id,{'response_code':code,'reference':transfer_ref});processed.append({'id':ins.id,'status':ins.status})
            except PagaUnknownOutcome as exc:
                ins.status='UNKNOWN';ins.provider_stage='TRANSFER_UNKNOWN';ins.provider_response={**(ins.provider_response or {}),'last_error':str(exc)}
                for lot in d.get('lots',[]):
                    if lot.get('money_status') in ['RELEASE_PENDING','REFUND_PENDING']:lot['is_frozen']=True;lot['freeze_reason']='PROVIDER_UNKNOWN'
                save_tx(s,tx,d);audit(s,None,'PAGA_TRANSFER_UNKNOWN','INSTRUCTION',ins.id,{'reference':transfer_ref,'reason':str(exc)});processed.append({'id':ins.id,'status':'UNKNOWN'})
            except PagaError as exc:
                restore_paga_subsidiary_funds(s,client,ins,tx,d,account,f'Paga rejected the bank transfer request: {exc}')
                audit(s,None,'PAGA_TRANSFER_FAILED','INSTRUCTION',ins.id,{'reason':str(exc)});processed.append({'id':ins.id,'status':ins.status})
            s.commit()
        else:
            pending+=1
    return {'processed':processed,'pending':pending,'mode':client.config.provider_mode}

def process_paga_subsidiary_operation_events(s:Session):
    if os.getenv('PAYMENT_PROVIDER','simulated').lower()!='paga' or os.getenv('PAGA_MODE','disabled').lower()=='disabled':return {'processed':0,'pending':0}
    events=s.scalars(select(ProviderEvent).where(ProviderEvent.provider_name=='PAGA_SUBSIDIARY_OPS',ProviderEvent.signature_valid==True,~select(ProviderEventProcessing.id).where(ProviderEventProcessing.provider_event_id==ProviderEvent.id,ProviderEventProcessing.status=='PROCESSED').exists()).order_by(ProviderEvent.received_at).limit(50)).all()
    processed=0;pending=0
    for event in events:
        body=event.raw_body or {};ref=str(body.get('externalReferenceNumber') or '')
        ins=s.scalar(select(Instruction).where(Instruction.provider_reference==ref)) if ref else None
        if not ins:
            mark_paga_event(s,event,'PROCESSED','No SupplyKey instruction matches the subsidiary operation reference.');processed+=1;continue
        if body.get('event')=='SUBSIDIARY_ACCOUNT_CHARGE_COMPLETE' and str(body.get('statusCode'))=='0':
            try:
                amount_kobo=int((Decimal(str(body.get('amount','0')).replace(',',''))*100).quantize(Decimal('1')))
                fee_kobo=int((Decimal(str(body.get('fee','0')))*100).quantize(Decimal('1')))
            except (InvalidOperation,ValueError):amount_kobo=-1;fee_kobo=-1
            if amount_kobo==ins.amount_kobo and fee_kobo==0:
                ins.provider_stage='SUBSIDIARY_CHARGED';ins.provider_reference='SKOUT'+ins.id.replace('-','');ins.provider_response={**(ins.provider_response or {}),'charge_callback':body};ins.status='QUEUED'
                mark_paga_event(s,event,'PROCESSED',None);processed+=1
            else:
                ins.status='UNKNOWN';ins.provider_stage='CHARGE_UNKNOWN';mark_paga_event(s,event,'PROCESSED','Paga charge callback amount or fee did not match the authorised instruction.');pending+=1
        elif body.get('event')=='SUBSIDIARY_ACCOUNT_TOP_UP_COMPLETE' and str(body.get('statusCode'))=='0':
            try:
                amount_kobo=int((Decimal(str(body.get('amount','0')).replace(',',''))*100).quantize(Decimal('1')))
                fee_kobo=int((Decimal(str(body.get('fee','0')))*100).quantize(Decimal('1')))
            except (InvalidOperation,ValueError):amount_kobo=-1;fee_kobo=-1
            if amount_kobo==ins.amount_kobo and fee_kobo==0 and ins.provider_stage=='FUNDS_RESTORED' and ins.status=='FAILED':
                mark_paga_event(s,event,'PROCESSED',None);processed+=1
            elif amount_kobo==ins.amount_kobo and fee_kobo==0 and ins.provider_stage in ['RESTORE_SUBMITTING','RESTORE_UNKNOWN']:
                ins.status='FAILED';ins.provider_stage='FUNDS_RESTORED';ins.provider_response={**(ins.provider_response or {}),'restore_callback':body};mark_paga_event(s,event,'PROCESSED',None);processed+=1
            else:
                ins.status='UNKNOWN';ins.provider_stage='RESTORE_UNKNOWN';mark_paga_event(s,event,'PROCESSED','Paga top-up callback did not match the restoration instruction.');pending+=1
        else:
            ins.status='UNKNOWN';ins.provider_stage='CHARGE_UNKNOWN';mark_paga_event(s,event,'PROCESSED','Paga subsidiary operation did not conclusively confirm the charge.');pending+=1
    s.commit();return {'processed':processed,'pending':pending}

def process_paga_payout_events(s:Session):
    if os.getenv('PAYMENT_PROVIDER','simulated').lower()!='paga' or os.getenv('PAGA_MODE','disabled').lower()=='disabled':return {'processed':0,'pending':0}
    events=s.scalars(select(ProviderEvent).where(ProviderEvent.provider_name=='PAGA_PAYOUT',ProviderEvent.signature_valid==True,~select(ProviderEventProcessing.id).where(ProviderEventProcessing.provider_event_id==ProviderEvent.id,ProviderEventProcessing.status=='PROCESSED').exists()).order_by(ProviderEvent.received_at).limit(50)).all()
    processed=0;pending=0
    try:client=PagaClient()
    except PagaError as exc:return {'processed':0,'pending':len(events),'configuration_error':str(exc)}
    definitive_failures={'-2','-9','-8','-4','116','117','132','136','137','138','139','4','101','103','107','114','115','133','104','106','108','110','111','113','118','119','129','131','140'}
    for event in events:
        body=event.raw_body or {};ins=s.scalar(select(Instruction).where(Instruction.provider_reference==str(body.get('referenceNumber',''))))
        if not ins:
            mark_paga_event(s,event,'PROCESSED','No financial instruction matches the Paga transfer reference.');processed+=1;continue
        tx=s.get(Aggregate,ins.transaction_id);d=tx_data(tx)
        response_code=str(body.get('responseCode'))
        settled='SETTLED' in str(body.get('transactionType','')).upper() and not body.get('reversalId')
        if response_code=='0' and settled:
            finalize_instruction_success(s,ins,tx,d,'PAGA');mark_paga_event(s,event,'PROCESSED',None);processed+=1
        elif response_code=='3':
            ins.status='SUBMITTED';ins.provider_stage='TRANSFER_PENDING';mark_paga_event(s,event,'RETRY','Paga reports transfer pending.');pending+=1
        elif response_code in definitive_failures or body.get('reversalId'):
            account=s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.transaction_id==ins.transaction_id))
            restore_paga_subsidiary_funds(s,client,ins,tx,d,account,'Paga callback confirmed a failed or reversed bank transfer.')
            mark_paga_event(s,event,'PROCESSED','Paga bank transfer failed or was reversed; source funds restoration was initiated.');processed+=1
        else:
            ins.status='UNKNOWN';ins.provider_stage='TRANSFER_UNKNOWN'
            for lot in d.get('lots',[]):
                if lot.get('money_status') in ['RELEASE_PENDING','REFUND_PENDING']:lot['is_frozen']=True;lot['freeze_reason']='PROVIDER_UNKNOWN'
            save_tx(s,tx,d);mark_paga_event(s,event,'PROCESSED','Paga callback did not conclusively confirm settlement.');pending+=1
    s.commit();return {'processed':processed,'pending':pending}

def run_paga_statement_reconciliation(s:Session,client:PagaClient):
    """Compare Paga business history and Subsidiary Account balances to local instructions."""
    end=now().replace(hour=0,minute=0,second=0,microsecond=0)
    start=end-timedelta(days=1)
    start_text=start.strftime('%Y-%m-%dT%H:%M:%S');end_text=end.strftime('%Y-%m-%dT%H:%M:%S')
    summary={'period_start_utc':start_text,'period_end_utc':end_text,'business_history_items':0,'subsidiary_accounts_checked':0,'subsidiary_history_items':0,'history_resolved_instructions':0,'balance_mismatches':[],'missing_settled_transfers':[],'provider_settled_local_unresolved':[],'uncertain_instructions':[],'errors':[]}
    business_items=[]
    try:
        history=client.get_business_transactions('SKHIS'+secrets.token_hex(12).upper(),start_text,end_text)
        if str(history.get('responseCode'))!='0':raise PagaUnknownOutcome('Paga business transaction history did not return a confirmed result.')
        business_items=history.get('items',[]) or [];summary['business_history_items']=len(business_items)
        if int(history.get('totalItems',len(business_items)) or 0)>10000:summary['errors'].append({'source':'business_history','reason':'Paga returned more than its documented 10,000-item history limit for this date range.'})
    except PagaError as exc:summary['errors'].append({'source':'business_history','reason':str(exc)})
    by_reference={str(x.get('referenceNumber')):x for x in business_items if x.get('referenceNumber')}
    instructions=s.scalars(select(Instruction).where(Instruction.transaction_id.is_not(None))).all()
    known_transfer_refs=set()
    for ins in instructions:
        transfer_ref='SKOUT'+ins.id.replace('-','');known_transfer_refs.add(transfer_ref)
        item=by_reference.get(transfer_ref)
        settled_at=(ins.provider_response or {}).get('settled_at')
        settled_in_window=False
        if settled_at:
            try:
                settled_dt=datetime.fromisoformat(settled_at.replace('Z','+00:00'))
                settled_in_window=start<=settled_dt<end
            except Exception:pass
        if ins.status=='SUCCEEDED' and settled_in_window and not item:
            summary['missing_settled_transfers'].append({'instruction_id':ins.id,'reference':transfer_ref})
        elif item and str(item.get('status','')).upper() in ['SUCCESSFUL','SUCCEEDED'] and ins.status!='SUCCEEDED':
            try:item_amount=int((Decimal(str(item.get('amount','0')))*100).quantize(Decimal('1')))
            except (InvalidOperation,ValueError):item_amount=-1
            settled_type='SETTLED' in str(item.get('transactionType','')).upper() and not item.get('reversalId')
            if ins.status in ['SUBMITTED','UNKNOWN'] and ins.provider_stage!='FUNDS_RESTORED' and item_amount==ins.amount_kobo and settled_type:
                tx=s.get(Aggregate,ins.transaction_id);finalize_instruction_success(s,ins,tx,tx_data(tx),'PAGA_HISTORY_RECONCILIATION');summary['history_resolved_instructions']+=1
            else:
                summary['provider_settled_local_unresolved'].append({'instruction_id':ins.id,'reference':transfer_ref,'local_status':ins.status,'provider_amount_kobo':item_amount})
        if ins.status in ['UNKNOWN','SUBMITTED']:
            summary['uncertain_instructions'].append({'instruction_id':ins.id,'status':ins.status,'provider_stage':ins.provider_stage})
    for ref,item in by_reference.items():
        if ref.startswith('SKOUT') and ref not in known_transfer_refs and str(item.get('status','')).upper() in ['SUCCESSFUL','SUCCEEDED']:
            summary['provider_settled_local_unresolved'].append({'reference':ref,'local_status':'MISSING_INSTRUCTION'})
    accounts=s.scalars(select(PagaSubsidiaryAccount)).all()
    for account in accounts:
        payment=s.get(Payment,account.payment_id)
        if not payment or not account.account_reference:continue
        tx_instructions=s.scalars(select(Instruction).where(Instruction.transaction_id==account.transaction_id)).all()
        uncertain=[x for x in tx_instructions if x.status in ['UNKNOWN','SUBMITTED'] or x.provider_stage in ['CHARGE_SUBMITTING','CHARGE_UNKNOWN','RESTORE_SUBMITTING','RESTORE_UNKNOWN','TRANSFER_UNKNOWN']]
        moved=sum(x.amount_kobo for x in tx_instructions if x.status=='SUCCEEDED' or x.provider_stage in ['SUBSIDIARY_CHARGED','TRANSFER_SUBMITTED','TRANSFER_PENDING'])
        expected=max(0,payment.amount_kobo-moved)
        try:
            bal=client.get_subsidiary_balance(account.account_reference,'SKREC'+secrets.token_hex(12).upper())
            if str(bal.get('statusCode'))!='0':raise PagaUnknownOutcome('Paga subsidiary balance query was not confirmed.')
            actual=int((Decimal(str(bal.get('balance','0')))*100).quantize(Decimal('1')))
            history_page=client.get_subsidiary_history(account.account_reference,'SKRCH'+secrets.token_hex(12).upper(),start_text,end_text,20,0)
            if str(history_page.get('statusCode'))!='0':raise PagaUnknownOutcome('Paga subsidiary account history query was not confirmed.')
            account_history_count=len(history_page.get('transactions',history_page.get('items',[])) or [])
            total_pages=int(history_page.get('totalPages',history_page.get('pageCount',1)) or 1)
            if total_pages>50:summary['errors'].append({'account_reference':account.account_reference,'reason':'Subsidiary account history exceeded the 50-page reconciliation safety cap.'})
            for page_number in range(1,min(total_pages,50)):
                page=client.get_subsidiary_history(account.account_reference,'SKRCH'+secrets.token_hex(12).upper(),start_text,end_text,20,page_number)
                if str(page.get('statusCode'))!='0':raise PagaUnknownOutcome(f'Paga subsidiary account history page {page_number} was not confirmed.')
                account_history_count+=len(page.get('transactions',page.get('items',[])) or [])
            summary['subsidiary_history_items']+=account_history_count
            summary['subsidiary_accounts_checked']+=1
            if not uncertain and actual!=expected:
                summary['balance_mismatches'].append({'transaction_id':account.transaction_id,'account_reference':account.account_reference,'expected_kobo':expected,'actual_kobo':actual})
        except PagaError as exc:summary['errors'].append({'account_reference':account.account_reference,'reason':str(exc)})
    return summary

def resolve_paga_instruction_statuses(s:Session):
    if os.getenv('PAYMENT_PROVIDER','simulated').lower()!='paga':return {'checked':0,'settled':0,'unknown':0}
    try:client=PagaClient()
    except PagaError as exc:return {'checked':0,'settled':0,'unknown':0,'configuration_error':str(exc)}
    rows=s.scalars(select(Instruction).where(Instruction.status.in_(['SUBMITTED','UNKNOWN']),Instruction.provider_reference.is_not(None)).limit(50)).all()
    checked=settled=unknown=0
    definitive_failures={'-2','-9','-8','-4','116','117','132','136','137','138','139','4','101','103','107','114','115','133','104','106','108','110','111','113','118','119','129','131','140'}
    for ins in rows:
        if ins.provider_stage in ['RESTORE_UNKNOWN','RESTORE_SUBMITTING']:
            account=s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.transaction_id==ins.transaction_id))
            restore_ref='SKBACK'+ins.id.replace('-','')
            try:
                restored=client.top_up_subsidiary_account(account.account_reference,restore_ref,ins.amount_kobo,f'SupplyKey restore {ins.id[:8]}')
            except PagaError as exc:
                restored=None;ins.provider_response={**(ins.provider_response or {}),'restore_reconciliation_error':str(exc)}
            try:
                balance=client.get_subsidiary_balance(account.account_reference,'SKRBL'+ins.id.replace('-',''))
                expected=int((ins.provider_response or {}).get('balance_before_kobo',0))
                actual=int((Decimal(str(balance.get('balance','0')))*100).quantize(Decimal('1')))
                if str(balance.get('statusCode'))=='0' and expected>0 and actual==expected:
                    ins.status='FAILED';ins.provider_stage='FUNDS_RESTORED';ins.provider_response={**(ins.provider_response or {}),'restore_reconciled':restored,'restored_balance_kobo':actual}
                else:unknown+=1
                checked+=1;continue
            except (PagaError,InvalidOperation,ValueError):
                unknown+=1;continue
        if ins.provider_stage in ['CHARGE_UNKNOWN','CHARGE_SUBMITTING']:
            account=s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.transaction_id==ins.transaction_id))
            charge_ref='SKCHG'+ins.id.replace('-','')
            try:
                result=client.charge_subsidiary_account(account.account_reference,charge_ref,ins.amount_kobo,f'SupplyKey {ins.instruction_type.lower()} {ins.id[:8]}')
            except PagaError as exc:
                result=None;ins.provider_response={**(ins.provider_response or {}),'charge_reconciliation_error':str(exc)}
            try:
                before=int((Decimal(str((ins.provider_response or {}).get('balance_before_kobo',0)))).quantize(Decimal('1')))
                balance=client.get_subsidiary_balance(account.account_reference,'SKCHK'+ins.id.replace('-',''))
                after=int((Decimal(str(balance.get('balance','0')))*100).quantize(Decimal('1')))
                if str(balance.get('statusCode'))=='0' and before-after==ins.amount_kobo:
                    ins.provider_stage='SUBSIDIARY_CHARGED';ins.provider_reference='SKOUT'+ins.id.replace('-','');ins.status='QUEUED';ins.provider_response={**(ins.provider_response or {}),'charge_reconciled':result,'charge_balance_reconciled_kobo':after}
                else:unknown+=1
                checked+=1;continue
            except (PagaError,InvalidOperation,ValueError):
                unknown+=1;continue
        try:
            result=client.transaction_status(ins.provider_reference);checked+=1
            code=str(result.get('responseCode'));kind=str(result.get('transactionType','')).upper()
            if code=='0' and 'SETTLED' in kind and not result.get('reversalId'):
                tx=s.get(Aggregate,ins.transaction_id);finalize_instruction_success(s,ins,tx,tx_data(tx),'PAGA_STATUS_QUERY');settled+=1
            elif code in definitive_failures:
                tx=s.get(Aggregate,ins.transaction_id);account=s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.transaction_id==ins.transaction_id))
                restore_paga_subsidiary_funds(s,client,ins,tx,tx_data(tx),account,f'Paga status query confirmed bank transfer failure with responseCode {code}.')
                ins.provider_response={**(ins.provider_response or {}),'status_query':result};audit(s,None,'PAGA_TRANSFER_FAILED','INSTRUCTION',ins.id,{'response_code':code})
            elif code=='3':
                ins.status='SUBMITTED';ins.provider_stage='TRANSFER_PENDING'
            else:
                ins.status='UNKNOWN';ins.provider_stage='TRANSFER_UNKNOWN';unknown+=1
        except PagaUnknownOutcome:
            ins.status='UNKNOWN';ins.provider_stage='TRANSFER_UNKNOWN';unknown+=1
        except PagaError as exc:
            ins.provider_response={**(ins.provider_response or {}),'status_query_error':str(exc)};unknown+=1
    s.commit();return {'checked':checked,'settled':settled,'unknown':unknown}

def run_financial(s:Session):
    """Dispatch queued instructions through the configured provider adapter."""
    if os.getenv('PAYMENT_PROVIDER','simulated').lower()=='paga':return run_paga_financial(s)
    scenario=os.getenv('SIMULATED_INSTRUCTION_SCENARIO','PENDING').upper();processed=[]
    if scenario not in ['PENDING','SUCCESS','FAILED','UNKNOWN']:scenario='PENDING'
    for ins in s.scalars(select(Instruction).where(Instruction.status=='QUEUED')).all():
        tx=s.get(Aggregate,ins.transaction_id);d=tx_data(tx)
        if scenario=='PENDING':continue
        if scenario=='UNKNOWN':
            ins.status='UNKNOWN'
            for lot in d.get('lots',[]):
                if lot.get('money_status') in ['RELEASE_PENDING','REFUND_PENDING']:lot['is_frozen']=True;lot.setdefault('freeze_reason','PROVIDER_UNKNOWN')
            save_tx(s,tx,d);processed.append({'id':ins.id,'status':'UNKNOWN'});continue
        if scenario=='FAILED':
            # F5: definitive failure is recorded; reservation and authorisation remain until senior F10.
            ins.status='FAILED';audit(s,None,'INSTRUCTION_FAILED','INSTRUCTION',ins.id,{'provider_mode':'SIMULATED'});processed.append({'id':ins.id,'status':'FAILED'});continue
        finalize_instruction_success(s,ins,tx,d,'SIMULATED');processed.append({'id':ins.id,'status':'SUCCEEDED'})
    s.commit();return {'processed':processed,'scenario':scenario,'mode':'SIMULATED'}
@app.get('/api/v1/transactions/{tid}/ledger')
def transaction_ledger(tid:str,s:Session=Depends(db),u:User=Depends(current_user)):
    tx=tx_get(s,tid,u,staff=u.role in ['MODERATOR','SENIOR_MODERATOR']);rows=s.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id==tid)).all()
    journals={j.id:j for j in s.scalars(select(Journal).where(Journal.transaction_id==tid)).all()}
    modes={p.id:p.mode for p in s.scalars(select(Payment).where(Payment.transaction_id==tid)).all()}
    # provider_mode is set where the journal originates from a payment, so simulated money is never mistaken for a real settlement.
    return {'data':[{'journal_id':r.journal_id,'entry_type':journals[r.journal_id].entry_type,'account_code':r.account_code,'direction':r.direction,'amount_kobo':r.amount_kobo,'created_at':iso(r.created_at),'provider_mode':modes.get(journals[r.journal_id].reference_id)} for r in rows]}

@app.post('/api/v1/worker/run-deadlines')
def worker_deadlines(x_worker_secret:str|None=Header(default=None,alias='X-Worker-Secret'),s:Session=Depends(db)):
    if not x_worker_secret or not hmac.compare_digest(x_worker_secret,WORKER_SECRET):fail(403,'FORBIDDEN','Worker authentication required.')
    return run_deadlines(s)
@app.post('/api/v1/worker/run-financial')
def worker_financial(x_worker_secret:str|None=Header(default=None,alias='X-Worker-Secret'),s:Session=Depends(db)):
    if not x_worker_secret or not hmac.compare_digest(x_worker_secret,WORKER_SECRET):fail(403,'FORBIDDEN','Worker authentication required.')
    return run_financial(s)
