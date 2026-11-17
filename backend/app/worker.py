from . import config as _config 
import os,time
from .db import SessionLocal,init_db
from .main import run_deadlines,run_financial,process_paga_events,resolve_paga_account_creations,process_paga_subsidiary_operation_events,process_paga_payout_events,resolve_paga_instruction_statuses

def once():
    init_db()
    s=SessionLocal()
    try:
        # Reuse idempotent domain services. External Paga calls occur only when explicitly configured.
        deadlines=run_deadlines(s)
        paga_accounts=resolve_paga_account_creations(s)
        paga=process_paga_events(s)
        paga_operations=process_paga_subsidiary_operation_events(s)
        paga_payouts=process_paga_payout_events(s)
        paga_status=resolve_paga_instruction_statuses(s)
        financial=run_financial(s)
        return {'deadlines':deadlines,'paga_accounts':paga_accounts,'paga_events':paga,'paga_operation_events':paga_operations,'paga_payout_events':paga_payouts,'paga_status':paga_status,'financial':financial}
    finally:s.close()
if __name__=='__main__':
    if os.getenv('APP_ENV','development')=='production' and len(os.getenv('PAYOUT_ENCRYPTION_KEY',''))<32:
        raise RuntimeError('PAYOUT_ENCRYPTION_KEY must be configured before the production worker starts.')
    interval=max(10,int(os.getenv('WORKER_INTERVAL_SECONDS','60')))
    while True:
        try:print(once(),flush=True)
        except Exception as exc:print({'worker_error':str(exc)},flush=True)
        time.sleep(interval)
