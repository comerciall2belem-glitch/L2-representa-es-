"""Generate production SQL for execution against an isolated PostgreSQL engine."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import activity_search as module
cases=[]
class Capture:
    def execute(self,sql,args):
        self.sql=sql;self.args=args;return self
    def fetchall(self):return []
    def fetchone(self):return ({'text':'fixture'},None)
con=Capture()
def case(name,user='Ana',seller=True,permissions=None,expected=(),**filters):
    module.search(con,user,seller,permissions or {'commercial'},**filters)
    cases.append(dict(name=name,sql=con.sql,args=con.args,expected=list(expected)))
case('accent + client name',q='reposição sao',expected=['owned'])
case('explicit authorization',q='autoriza',expected=['authorized'])
case('other portfolio excluded',q='secreto')
case('own general task',q='agenda',expected=['general-own'])
case('other general task hidden',q='agenda',user='Bia',expected=['general-other'])
case('route author restricted',q='rota',expected=['route-own'])
case('date + owner + status',kind='task',owner='Ana',status='Aberta',start='2026-11-01',end='2026-11-09',expected=['owned'])
case('office without permission',seller=False,q='escritorio')
case('office nonfinancial',seller=False,permissions={'office'},q='escritorio',expected=['office-normal'])
case('office finance authorized',seller=False,permissions={'office','finance'},q='escritorio',expected=['office-normal','office-finance'])
case('page one',seller=False,kind='visit',expected=['bulk-'+str(i) for i in range(11)])
case('page two',seller=False,kind='visit',offset=10,expected=['bulk-10','bulk-11'])
for kind,id,expected in [('task','secret',[]),('task','owned',['owned']),('office_action','office-finance',[])]:
    module.detail(con,'Ana',True,{'commercial'},kind,id)
    cases.append(dict(name='detail '+id,sql=con.sql,args=con.args,expected=expected,detail=True))
print(json.dumps(cases))
