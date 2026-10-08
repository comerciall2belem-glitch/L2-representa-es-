"""OSRM road matrix and route geometry; geographic fallback is explicit."""
import json
import math
import os
import urllib.request
import urllib.parse

class RoutingError(ValueError):pass

def _get(path,opener=None):
    base=os.getenv('L2_ROUTING_URL','https://router.project-osrm.org').rstrip('/')
    parsed=urllib.parse.urlparse(base)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise RoutingError('Provedor de mapas inválido')
    req=urllib.request.Request(base+path,headers={'Accept':'application/json','User-Agent':'L2-One/StrategicCRM'})
    try:
        with (opener or urllib.request.urlopen)(req,timeout=8) as response:
            raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise RoutingError('Resposta de mapas excedeu o limite')
        data=json.loads(raw)
        if data.get('code')!='Ok':raise RoutingError('Sem percurso disponível no provedor')
        return data
    except (OSError,ValueError,TypeError) as exc:raise RoutingError('Cálculo rodoviário indisponível') from exc

def coordinates(points):
    return ';'.join(f"{p['longitude']:.6f},{p['latitude']:.6f}" for p in points)

def matrix(points,opener=None):
    if not 2<=len(points)<=41:raise RoutingError('Matriz deve conter 2–41 pontos')
    data=_get('/table/v1/driving/'+coordinates(points)+'?annotations=duration,distance',opener)
    durations,distances=data.get('durations'),data.get('distances');n=len(points)
    if not isinstance(durations,list) or not isinstance(distances,list) or len(durations)!=n or len(distances)!=n or any(not isinstance(r,list) or len(r)!=n for r in durations+distances):raise RoutingError('Matriz inválida')
    for rows in (durations,distances):
        if any(v is not None and (not isinstance(v,(int,float)) or not math.isfinite(v) or v<0) for row in rows for v in row):raise RoutingError('Matriz inválida')
    ids={p['_node']:i for i,p in enumerate(points)}
    def distance(a,b):
        i,j=ids[a['_node']],ids[b['_node']]
        if durations[i][j] is None or distances[i][j] is None:return None
        return {'minutes':max(1,math.ceil(durations[i][j]/60)),'km':round(distances[i][j]/1000,3)}
    return distance

def geometry(points,opener=None):
    data=_get('/route/v1/driving/'+coordinates(points)+'?overview=full&geometries=geojson&steps=false',opener)
    try:
        result=data['routes'][0]['geometry']
        if result['type']!='LineString' or not isinstance(result['coordinates'],list) or not 2<=len(result['coordinates'])<=100000:raise ValueError()
        if any(not isinstance(p,list) or len(p)!=2 or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in p) or not -180<=p[0]<=180 or not -90<=p[1]<=90 for p in result['coordinates']):raise ValueError()
        return result
    except (KeyError,IndexError,TypeError,ValueError) as exc:raise RoutingError('Trajeto inválido') from exc
