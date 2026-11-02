// Execute generated commands against inert local programs, never the network or installer.
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { buildInstallCommand } from '../portalapp/static/guide-install.mjs';
const ref='344116330a2568d5c909c5fcc07b54a682e9b944';
for(const bad of ['', 'http://cluster.test', 'https:/cluster.test', 'https://u:p@cluster.test', 'https://cluster.test?x=1', 'https://cluster.test#x', 'https://cluster.test\n;echo bad', 'file:///tmp/x']) {
  assert.throws(()=>buildInstallCommand(bad,'',ref), undefined, bad);
}
assert.throws(()=>buildInstallCommand('https://cluster.test','','main'));
const dir=mkdtempSync(join(tmpdir(),'orbit-guide-'));
try {
  writeFileSync(join(dir,'curl'), '#!/bin/sh\nprintf "%s\\n" "$@" > "$GUIDE_TEST_CURL"\n', {mode:0o700});
  writeFileSync(join(dir,'bash'), '#!/bin/sh\nprintf "%s\\n" "$@" > "$GUIDE_TEST_BASH"\n', {mode:0o700});
  const ca="/tmp/CA folder/it's $(touch "+join(dir,'injected')+").crt";
  const command=buildInstallCommand('https://cluster.test:8443/api-root/',ca,ref);
  const result=spawnSync('/bin/bash',['-c',command],{env:{...process.env,PATH:dir+':'+process.env.PATH,GUIDE_TEST_CURL:join(dir,'curl-args'),GUIDE_TEST_BASH:join(dir,'bash-args')},encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  const bashArgs=readFileSync(join(dir,'bash-args'),'utf8').trimEnd().split('\n');
  assert.deepEqual(bashArgs.slice(1),['--ref',ref,'--server','https://cluster.test:8443/api-root','--ca-bundle',ca]);
  assert.equal(existsSync(bashArgs[0]),false,'temporary installer should be cleaned');
  assert.equal(existsSync(join(dir,'injected')),false,'shell substitution must remain literal');
  assert.ok(readFileSync(join(dir,'curl-args'),'utf8').includes('https://raw.githubusercontent.com/hello3x3/OrbitCluster/'+ref+'/terminal/install.sh\n'));
  const noCa=buildInstallCommand('https://cluster.test','',ref);
  assert.ok(!noCa.includes('--ca-bundle'));
  // A failed download must not run even an old or empty installer.
  rmSync(join(dir,'bash-args'));
  writeFileSync(join(dir,'curl'),'#!/bin/sh\nexit 22\n',{mode:0o700});
  const failed=spawnSync('/bin/bash',['-c',noCa],{env:{...process.env,PATH:dir+':'+process.env.PATH,GUIDE_TEST_BASH:join(dir,'bash-args')}});
  assert.notEqual(failed.status,0);
  assert.equal(existsSync(join(dir,'bash-args')),false);
  console.log('Guide command checks passed: HTTPS validation, quoting, fixed release, cleanup, failed download.');
} finally { rmSync(dir,{recursive:true,force:true}); }
