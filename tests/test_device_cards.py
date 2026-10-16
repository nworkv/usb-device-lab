"""Node-backed checks for identity parsing, cache bounds and session isolation."""
import shutil
import subprocess
import unittest
from usb_device_lab.device_cards import SCRIPT

PRELUDE = """const assert=require('node:assert/strict');
let token='fixture',epoch=1,calls=0,inFlight=0,peak=0;
const fixture={descriptors:[{type:1,hex:'12010002000000406b1d1001000101020301'}],metadata:{family:'hid',profile:'keyboard'},speed:2};
let responseConfig=fixture;
const nodes={campaign:{value:'campaign'},attempt:{options:[]},'attempt-cards':{children:[]}};
const el=id=>nodes[id];
function node(tag,text,cls){return {textContent:text,className:cls};}
function setup(count){nodes.attempt.options=Array.from({length:count},(_,i)=>({value:'attempt-'+String(i).padStart(6,'0')}));nodes['attempt-cards'].children=Array.from({length:count},()=>({prepend(info){this.info=info;}}));}
function file(id,attempt,name){return id+'/'+attempt+'/'+name;}
async function api(path){calls++;inFlight++;peak=Math.max(peak,inFlight);await new Promise(resolve=>setTimeout(resolve,2));inFlight--;return responseConfig;}
async function details(id){}
"""
CHECKS = """(async()=>{
const identity=usbIdentity(fixture);
assert.equal(identity.vid,'1D6B');assert.equal(identity.pid,'0110');assert.equal(identity.bcdUSB,'0200');assert.equal(identity.speed,'Full Speed');assert.equal(identity.icon,'⌨');
for(const hex of ['', '12', 'zz'.repeat(18), '0'.repeat(37)])assert.throws(()=>usbIdentity({descriptors:[{type:1,hex}]}));
assert.throws(()=>usbIdentity(null));assert.throws(()=>usbIdentity({}));
assert.equal(usbIdentity({...fixture,metadata:{family:'toString'}}).icon,'⎈');
assert.equal(usbIdentity({...fixture,speed:'toString'}).speed,'не указана');
setup(50);await details('campaign');assert.equal(calls,50);assert.ok(peak<=4);assert.ok(nodes['attempt-cards'].children.every(c=>c.info.textContent.includes('VID:PID 1D6B:0110')));
setup(50);await details('campaign');assert.equal(calls,50);
for(let i=0;i<5;i++){const id='campaign-'+i;nodes.campaign.value=id;setup(50);await details(id);assert.ok(deviceCardCache.size<=200);}
epoch++;calls=0;nodes.campaign.value='new-session';setup(50);await details('new-session');assert.equal(calls,50);assert.equal(deviceCardCache.size,50);
epoch++;calls=0;nodes.campaign.value='logout';setup(50);const pending=details('logout');setTimeout(()=>{epoch++;token='';},0);await pending;assert.ok(calls<=4);assert.equal(deviceCardCache.size,0);
token='fixture';epoch++;nodes.campaign.value='malformed';responseConfig={};setup(1);await details('malformed');assert.ok(nodes['attempt-cards'].children[0].info.textContent.includes('недоступны'));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@unittest.skipUnless(shutil.which('node'), 'Node.js required')
class DeviceCardTests(unittest.TestCase):
    def test_script_syntax(self):
        result = subprocess.run(['node', '--check'], input=SCRIPT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_identity_cache_concurrency_and_logout(self):
        source = PRELUDE.encode('utf-8') + SCRIPT + CHECKS.encode('utf-8')
        result = subprocess.run(['node'], input=source, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn(b'innerHTML', SCRIPT)


if __name__ == '__main__':
    unittest.main()
