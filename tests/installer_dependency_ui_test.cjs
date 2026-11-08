const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
async function main() {
  let info = {eInstallState: 0};
  const calls = [];
  const context = vm.createContext({SteamClient: {Downloads: {ResumeAppUpdate:async (id,client)=>{assert.equal(client,'0');calls.push(['resume',id]);}},Installs: {
    GetInstallManagerInfo: async () => info,
    OpenInstallWizard: async (ids,client) => {assert.equal(client,'0');calls.push(['open', [...ids]]);},
    OpenUninstallWizard: async (ids,client,confirmed) => {assert.deepEqual([...ids],[438100]);assert.equal(client,'0');assert.equal(confirmed,true);calls.push(['remove', [...ids]]);},
    ContinueInstall: async () => calls.push(['continue']),
    MarkEulaAccepted: () => {throw Error('EULA must never be accepted');},
  }}});
  const run = async action => vm.runInContext('(' + source + ')(' + JSON.stringify({action, appid: 1826330}) + ')', context);
  assert.equal((await run('read')).phase, 'idle');
  await run('start'); assert.deepEqual(calls, [['open', [1826330]]]);
  info = null; assert.equal((await run('read')).phase, 'unknown');
  await assert.rejects(run('start')); assert.equal(calls.length,1);
  const config = {eInstallState: 7, rgApps: [{nAppID: 1826330}], currentAppID: 1826330,
                  nDiskSpaceAvailable: 1024, nDiskSpaceRequired: 100, iUnmountedFolder: -1};
  info = {...config, eInstallState: 8};
  assert.equal((await run('read')).phase, 'confirmation');
  await assert.rejects(run('continue')); assert.equal(calls.length, 1);
  info = {...config, rgApps: [{nAppID: 570}]};
  assert.equal((await run('read')).phase, 'foreign');
  await assert.rejects(run('continue')); await assert.rejects(run('start')); assert.equal(calls.length, 1);
  info = {...config, nDiskSpaceAvailable: 99};
  assert.equal((await run('read')).phase, 'confirmation');
  await assert.rejects(run('continue')); assert.equal(calls.length, 1);
  info = {...config, eInstallState: 999};
  assert.equal((await run('read')).phase, 'unknown'); await assert.rejects(run('continue'));
  info = config; await run('continue'); assert.deepEqual(calls.at(-1), ['continue']);
  info = {...config,eInstallState:14,rgApps:[{nAppID:1493710}],currentAppID:1493710};
  assert.equal((await run('read')).phase,'complete');
  await run('start'); assert.deepEqual(calls.at(-1),['open',[1826330]]);
  info = {...info,rgApps:[{nAppID:570}],currentAppID:570};
  await assert.rejects(run('start'));
  const remove = appid => vm.runInContext('(' + source + ')(' + JSON.stringify({action:'remove',appid}) + ')', context);
  let before=calls.length;
  await assert.rejects(remove(438100));assert.equal(calls.length,before);
  info={eInstallState:0};
  await assert.rejects(remove(1826330));await assert.rejects(remove(570));assert.equal(calls.length,before);
  await remove(438100);assert.deepEqual(calls.at(-1),['remove',[438100]]);
  info={eInstallState:14,rgApps:[{nAppID:438100}],currentAppID:438100};
  await remove(438100);assert.deepEqual(calls.at(-1),['remove',[438100]]);
  info={eInstallState:8,rgApps:[{nAppID:438100}],currentAppID:438100};before=calls.length;
  await assert.rejects(remove(438100));assert.equal(calls.length,before);
  const resume = appid => vm.runInContext('(' + source + ')(' + JSON.stringify({action:'resume',appid}) + ')', context);
  await assert.rejects(resume(1493710));
  info={eInstallState:0};await resume(1493710);assert.deepEqual(calls.at(-1),['resume',1493710]);
  info={eInstallState:14,rgApps:[{nAppID:1493710}],currentAppID:1493710};await resume(1493710);
  await assert.rejects(resume(570));
  console.log('dependency UI guards passed');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
