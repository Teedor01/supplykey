import {describe,it,expect} from 'vitest';
import {money,TX_STATES,REASONS} from '../lib/constants';
describe('SupplyKey shared contracts',()=>{
 it('formats integer kobo as naira without floating point arithmetic in storage',()=>expect(money(110000000)).toContain('1,100,000'));
 it('exposes canonical transaction states',()=>{expect(TX_STATES).toContain('IN_DISPUTE');expect(TX_STATES).toContain('SETTLED_REFUNDED');expect(TX_STATES).not.toContain('PAID');});
 it('exposes documented dispute reason codes',()=>expect(REASONS).toContain('NO_DISPATCH'));
});
