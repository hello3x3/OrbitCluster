import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {buildInstallCommand} from '../portalapp/static/guide-install.mjs';
for(const bad of ['', 'file:///tmp/x','http://u:p@cluster.test','http://cluster.test?x=1','http://cluster.test#x','http://cluster.test\n;echo bad'])assert.throws(()=>buildInstallCommand(bad));
assert.throws(()=>buildInstallCommand('http://cluster.test','/tmp/ca.crt'));
const dir=mkdtempSync(join(tmpdir(),'orbit-guide-'));
try{
 writeFileSync(join(dir,'curl'),'#!/bin/sh\nprintf "%s\\n" "$@" > "$GUIDE_TEST_CURL"\n',{mode:0o700});
 writeFileSync(join(dir,'bash'),'#!/bin/sh\nprintf "%s\\n" "$@" > "$GUIDE_TEST_BASH"\n',{mode:0o700});
 const env={...process.env,PATH:dir+':'+process.env.PATH,GUIDE_TEST_CURL:join(dir,'curl-args'),GUIDE_TEST_BASH:join(dir,'bash-args')};
 const ca="/tmp/CA folder/it's $(touch "+join(dir,'injected')+").crt";
 for(const [server,certificate] of [['http://10.71.106.251',''],['https://cluster.test:8443/api-root/',ca]]){
  const command=buildInstallCommand(server,certificate);
  const result=spawnSync('/bin/bash',['-c',command],{env,encoding:'utf8'});assert.equal(result.status,0,result.stderr);
  const bashArgs=readFileSync(join(dir,'bash-args'),'utf8').trimEnd().split('\n');
  const normalized=server.replace(/\/+$/,'');
  assert.deepEqual(bashArgs.slice(1),['--server',normalized,...(certificate?['--ca-bundle',ca]:[])]);
  assert.ok(readFileSync(join(dir,'curl-args'),'utf8').includes(normalized+'/terminal/install.sh\n'));
  assert.ok(!command.includes('github.com')&&!command.includes('pip'));
  assert.equal(existsSync(bashArgs[0]),false);assert.equal(existsSync(join(dir,'injected')),false);
 }
 const windows=buildInstallCommand('http://10.71.106.251','','windows');assert.ok(windows.includes('/terminal/install.ps1'));assert.ok(windows.includes('curl.exe'));assert.ok(windows.includes("-Server 'http://10.71.106.251'"));
 rmSync(join(dir,'bash-args'));writeFileSync(join(dir,'curl'),'#!/bin/sh\nexit 22\n',{mode:0o700});
 const result=spawnSync('/bin/bash',['-c',buildInstallCommand('http://cluster.test')],{env});assert.notEqual(result.status,0);assert.equal(existsSync(join(dir,'bash-args')),false);
 console.log('Guide commands: default HTTP, portal-only downloads, CA quoting, Windows generation, failed downloads and cleanup passed.');
}finally{rmSync(dir,{recursive:true,force:true})}
