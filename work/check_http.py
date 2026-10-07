import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
base='http://127.0.0.1:8765'
state=json.load(urlopen(base+'/api/state'))
theme_id=state['themes'][0]['id']
request=Request(base+'/api/dashboard',data=json.dumps({'theme_id':theme_id}).encode(),headers={'Content-Type':'application/json','Origin':base})
dashboard=json.load(urlopen(request))
assert abs(dashboard['summary']['portfolio']['point']-.18)<1e-10
assert abs(dashboard['summary']['benchmark']['point']-.14)<1e-10
assert abs(dashboard['summary']['active']['point']-.04)<1e-10
print('HTTP dashboard passed: 18%, 14%, +4pp')
try:
    urlopen(Request(base+'/api/demo',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://untrusted.example'}))
    raise AssertionError('Cross-origin write accepted')
except HTTPError as error:
    assert error.code==403
    print('Cross-origin write rejected')
