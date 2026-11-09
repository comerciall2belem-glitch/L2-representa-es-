const assert=require('node:assert/strict'),G=require('../goals_dashboard.js');
const goal={id:'plan',month:'2026-11',brand:'BT',user:'Equipe',amount:1000.01,planVersion:1,workDates:['2026-11-02','2026-11-03','2026-11-07','2026-11-10'],allocations:[{user:'Ana Paula',weight:6000},{user:'Euler',weight:4000}]};
const state={industries:[{name:'BT'}],team:['Ana Paula','Euler'],sellers:['Euler'],goals:[goal,{id:'old',month:'2026-11',brand:'BT',user:'Euler',amount:999},{id:'general',month:'2026-11',user:'Equipe',amount:9999}],orders:[]};
const projected=G.expandGoals(state.goals);assert.equal(projected.find(x=>x.user==='Euler').amount,400);assert.equal(projected.filter(x=>x.sourcePlan).reduce((n,x)=>n+Math.round(x.amount*100),0),100001);
assert.deepEqual(G.expandGoals(projected),projected);
let sumDays=0,sumWeeks=0;
for(const day of goal.workDates){const m=G.separated(state,'2026-11',day);sumDays+=m.teamDaily;assert.equal(m.allocations.reduce((n,x)=>n+x.daily,0),m.teamDaily);assert.equal(m.members.reduce((n,x)=>n+x.target,0),100001);assert.equal(m.teamTarget,100001);}
for(const day of ['2026-11-02','2026-11-09','2026-11-16','2026-11-23','2026-11-30'])sumWeeks+=G.separated(state,'2026-11',day).teamWeekly;
assert.equal(sumDays,100001);assert.equal(sumWeeks,100001);
assert.equal(G.separated(state,'2026-11','2026-11-04').teamDaily,0);
assert.equal(G.separated(state,'2026-11','2026-12-01').allocations[0].requiredDaily,null);
const personal=G.separated({...state,goals:projected.filter(x=>x.user==='Euler')},'2026-11','2026-11-03');assert.equal(personal.members.find(x=>x.seller==='Euler').target,40000);
const before=JSON.stringify(state);G.separated(state,'2026-11','2026-11-07');assert.equal(JSON.stringify(state),before);
console.log('Monthly plans: exact monthly/daily/weekly reconciliation, overrides, scoped projections, custom Saturday, month end and no mutation OK');
