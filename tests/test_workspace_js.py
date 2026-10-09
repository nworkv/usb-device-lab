import json
import shutil
import subprocess
import unittest
from usb_device_lab.workspace_ui import SCRIPT

HARNESS = r'''const vm=require('vm'),assert=require('assert');const nodes=new Map(),calls=[],timers=[];
function node(id){if(!nodes.has(id)){const n={id,value:'',textContent:'',hidden:false,disabled:false,dataset:{},children:[],handlers:{},scrollHeight:0,scrollTop:0,clientHeight:100,append(...x){this.children.push(...x);},replaceChildren(...x){this.children=x;},addEventListener(k,f){this.handlers[k]=f;},setAttribute(){},get options(){return this.children;},get selectedOptions(){return[];}};nodes.set(id,n);}return nodes.get(id);}
const tabs=['manage','results','logs','corpus'].map(x=>{const n=node('tab-'+x);n.dataset.tab=x;return n;});const pages=['manage','results','logs','corpus'].map(x=>{const n=node('page-'+x);n.dataset.page=x;return n;});
const status={status:'idle',completed:0,active:{iterations:1000,seconds:2,seed:42,families:[]},configured:{iterations:1000,seconds:2,seed:42,families:[]},campaigns:[],current:null,failure:'',has_history:false,restart_required:false};
const context={AbortController,URL,URLSearchParams,location:{pathname:'/'},setTimeout(){},setInterval(f,ms){timers.push([f,ms]);},confirm(){return true;},document:{getElementById:node,createElement:t=>node('created-'+Math.random()),body:{append(){}},querySelectorAll:q=>q==='[data-tab]'?tabs:q==='[data-page]'?pages:[]},fetch:async(url,options)=>{calls.push([url,options]);return{ok:true,json:async()=>url.startsWith('/api/corpus')?{items:[],next_offset:0,more:false}:status};},console};
vm.createContext(context);vm.runInContext(SOURCE,context);
(async()=>{node('access').value='private-token';node('corpus-source').value='seeds';await node('enter').handlers.click();assert.equal(node('access').value,'');assert.equal(node('app').hidden,false);for(const tab of tabs)tab.handlers.click();await timers[0][0]();assert.equal(timers[0][1],3000);for(const call of calls)assert.equal(call[1].headers.Authorization,'Bearer private-token');await node('start').handlers.click();const start=calls.find(x=>x[0]==='/api/workspace/start');assert(start);assert.equal(start[1].body,'{}');node('exit').handlers.click();const count=calls.length;await timers[0][0]();assert.equal(calls.length,count);assert.equal(node('app').hidden,true);})().catch(error=>{console.error(error);process.exitCode=1;});'''


class WorkspaceJavaScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_login_tabs_config_start_logout_and_polling(self):
        source=HARNESS.replace('SOURCE',json.dumps(SCRIPT.decode('utf-8')))
        result=subprocess.run([shutil.which('node'),'-e',source],text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
